from __future__ import annotations

import asyncio
import json
import logging
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from warbrief.config import Settings
from warbrief.models import NewsArticle
from warbrief.utils import canonical_url, clean_text, parse_datetime, stable_id

logger = logging.getLogger(__name__)


class NewsProvider(ABC):
    name: str

    @abstractmethod
    async def fetch(self) -> list[NewsArticle]:
        raise NotImplementedError


def _http_client(settings: Settings, timeout: int = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        proxy=settings.http_proxy_url or None,
        headers={
            "User-Agent": "WarBrief/0.1 (+https://github.com/bcllcc/WarBrief)",
            "Accept": "application/json, application/rss+xml, application/xml, text/xml, */*",
        },
    )


class GDELTNewsProvider(NewsProvider):
    name = "gdelt"
    endpoint = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def fetch(self) -> list[NewsArticle]:
        params = {
            "query": self.settings.news_gdelt_query,
            "mode": "artlist",
            "maxrecords": str(min(self.settings.news_max_items, 250)),
            "format": "json",
            "timespan": f"{max(self.settings.news_lookback_hours, 1)}h",
            "sort": "datedesc",
        }
        async with _http_client(self.settings, 40) as client:
            response = await client.get(self.endpoint, params=params)
            response.raise_for_status()
            payload = response.json()

        results: list[NewsArticle] = []
        for item in payload.get("articles", []):
            title = clean_text(str(item.get("title", "")))
            url = canonical_url(str(item.get("url", "")))
            if not title or not url:
                continue
            source_name = str(item.get("domain") or urlparse(url).netloc or "GDELT")
            seen_date = str(item.get("seendate", ""))
            if seen_date and len(seen_date) == 14 and seen_date.isdigit():
                seen_date = (
                    f"{seen_date[0:4]}-{seen_date[4:6]}-{seen_date[6:8]}T"
                    f"{seen_date[8:10]}:{seen_date[10:12]}:{seen_date[12:14]}Z"
                )
            article_id = stable_id("article", f"gdelt:{url}")
            results.append(
                NewsArticle(
                    id=article_id,
                    provider=self.name,
                    source_name=source_name,
                    title=title,
                    summary=clean_text(str(item.get("snippet", ""))),
                    url=url,
                    published_at=parse_datetime(seen_date),
                    language=str(item.get("language", "")),
                    source_country=str(item.get("sourcecountry", "")),
                    image_url=str(item.get("socialimage", "") or ""),
                    raw=item,
                )
            )
        return results


class RSSNewsProvider(NewsProvider):
    name = "rss"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def fetch(self) -> list[NewsArticle]:
        if not self.settings.news_rss_urls:
            return []
        async with _http_client(self.settings, 35) as client:
            responses = await asyncio.gather(
                *(client.get(url) for url in self.settings.news_rss_urls),
                return_exceptions=True,
            )
        articles: list[NewsArticle] = []
        for feed_url, result in zip(self.settings.news_rss_urls, responses, strict=True):
            if isinstance(result, Exception):
                logger.warning("RSS fetch failed for %s: %s", feed_url, result)
                continue
            try:
                result.raise_for_status()
                articles.extend(self._parse_feed(feed_url, result.content))
            except Exception as exc:
                logger.warning("RSS parse failed for %s: %s", feed_url, exc)
        return articles[: self.settings.news_max_items]

    def _parse_feed(self, feed_url: str, content: bytes) -> list[NewsArticle]:
        root = ET.fromstring(content)
        feed_title = self._first_text(root, ["title"]) or urlparse(feed_url).netloc
        entries = [node for node in root.iter() if self._local(node.tag) in {"item", "entry"}]
        output: list[NewsArticle] = []
        for entry in entries:
            title = self._child_text(entry, ["title"])
            link = self._entry_link(entry)
            summary = self._child_text(entry, ["description", "summary", "content"])
            published = self._child_text(
                entry, ["pubDate", "published", "updated", "date", "created"]
            )
            image_url = self._entry_image(entry)
            title = clean_text(title)
            link = canonical_url(link)
            if not title or not link:
                continue
            output.append(
                NewsArticle(
                    id=stable_id("article", f"rss:{link}"),
                    provider=self.name,
                    source_name=clean_text(feed_title),
                    title=title,
                    summary=clean_text(summary)[:2000],
                    url=link,
                    published_at=parse_datetime(published),
                    image_url=image_url,
                    raw={"feed_url": feed_url},
                )
            )
        return output

    @staticmethod
    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def _first_text(self, node: ET.Element, names: list[str]) -> str:
        for child in node.iter():
            if self._local(child.tag) in names and child.text:
                return child.text
        return ""

    def _child_text(self, node: ET.Element, names: list[str]) -> str:
        for child in list(node):
            if self._local(child.tag) in names:
                return "".join(child.itertext()) if list(child) else (child.text or "")
        return ""

    def _entry_link(self, entry: ET.Element) -> str:
        for child in list(entry):
            if self._local(child.tag) == "link":
                return child.attrib.get("href") or (child.text or "")
            if self._local(child.tag) == "guid" and child.text and child.text.startswith("http"):
                return child.text
        return ""

    def _entry_image(self, entry: ET.Element) -> str:
        for child in entry.iter():
            local = self._local(child.tag).lower()
            if local in {"thumbnail", "content", "enclosure"}:
                url = child.attrib.get("url", "")
                mime = child.attrib.get("type", "")
                if url and ("image" in mime or local == "thumbnail"):
                    return url
        return ""


class MockNewsProvider(NewsProvider):
    name = "mock"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def fetch(self) -> list[NewsArticle]:
        path = files("warbrief").joinpath("sample_data/news.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        now = datetime.now(UTC)
        articles: list[NewsArticle] = []
        for index, item in enumerate(payload):
            published = now - timedelta(minutes=index * 37)
            url = item["url"]
            articles.append(
                NewsArticle(
                    id=stable_id("article", f"mock:{url}"),
                    provider=self.name,
                    source_name=item["source_name"],
                    title=item["title"],
                    summary=item["summary"],
                    content=item.get("content", item["summary"]),
                    url=url,
                    published_at=published,
                    language=item.get("language", "zh"),
                    source_country=item.get("source_country", ""),
                    raw={"mock": True},
                )
            )
        return articles


class ArticleExtractor:
    """Conservative article body extractor used only for evidence enrichment."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def extract(self, url: str) -> str:
        if not url.startswith(("http://", "https://")) or "example.invalid" in url:
            return ""
        async with _http_client(self.settings, 35) as client:
            response = await client.get(url)
            response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower():
            return ""
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        container = soup.find("article") or soup.find("main") or soup.body
        if container is None:
            return ""
        paragraphs = [clean_text(p.get_text(" ")) for p in container.find_all("p")]
        paragraphs = [p for p in paragraphs if len(p) >= 35]
        return "\n".join(paragraphs)[:16000]
