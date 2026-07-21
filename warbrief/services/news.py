from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta

from warbrief.config import Settings
from warbrief.models import NewsArticle, NewsEvent, RefreshResult
from warbrief.providers.news import (
    ArticleExtractor,
    GDELTNewsProvider,
    MockNewsProvider,
    NewsProvider,
    RSSNewsProvider,
)
from warbrief.storage import Storage
from warbrief.utils import clean_text, jaccard, stable_id, title_tokens

logger = logging.getLogger(__name__)

MILITARY_TERMS = {
    "military",
    "defense",
    "defence",
    "missile",
    "navy",
    "army",
    "airforce",
    "air force",
    "nato",
    "drone",
    "fighter",
    "warship",
    "security",
    "军",
    "导弹",
    "海军",
    "空军",
    "无人机",
    "演习",
    "防空",
    "国防",
    "防务",
}


class NewsService:
    def __init__(self, settings: Settings, storage: Storage):
        self.settings = settings
        self.storage = storage
        self.extractor = ArticleExtractor(settings)
        self._refresh_lock = asyncio.Lock()
        self.providers: list[NewsProvider] = []
        if settings.news_gdelt_enabled and not settings.demo_mode:
            self.providers.append(GDELTNewsProvider(settings))
        if settings.news_rss_urls and not settings.demo_mode:
            self.providers.append(RSSNewsProvider(settings))
        if settings.demo_mode:
            self.providers.append(MockNewsProvider(settings))

    async def refresh(self) -> RefreshResult:
        async with self._refresh_lock:
            providers_ok: list[str] = []
            providers_failed: dict[str, str] = {}
            all_articles: list[NewsArticle] = []

            if self.providers:
                results = await asyncio.gather(
                    *(provider.fetch() for provider in self.providers), return_exceptions=True
                )
                for provider, result in zip(self.providers, results, strict=True):
                    if isinstance(result, Exception):
                        providers_failed[provider.name] = str(result)
                        logger.warning("News provider %s failed: %s", provider.name, result)
                    else:
                        providers_ok.append(provider.name)
                        all_articles.extend(result)

            if not all_articles and self.settings.news_mock_fallback:
                fallback = MockNewsProvider(self.settings)
                all_articles = await fallback.fetch()
                providers_ok.append("mock_fallback")

            deduped = self._dedupe(all_articles)[: self.settings.news_max_items]
            self.storage.upsert_articles(deduped)
            events = self._cluster(deduped)
            self.storage.upsert_events(events)
            retention_days = max(self.settings.news_retention_days, 1)
            self.storage.prune_news(datetime.now(UTC) - timedelta(days=retention_days))
            return RefreshResult(
                articles_collected=len(deduped),
                events_created=len(events),
                providers_ok=providers_ok,
                providers_failed=providers_failed,
            )

    async def enrich_articles(
        self, articles: list[NewsArticle], max_articles: int = 3
    ) -> list[NewsArticle]:
        selected = articles[:max_articles]

        async def enrich(article: NewsArticle) -> NewsArticle:
            if article.content:
                return article
            try:
                content = await self.extractor.extract(article.url)
            except Exception as exc:
                logger.info("Article extraction failed for %s: %s", article.url, exc)
                content = ""
            if content:
                article = article.model_copy(update={"content": content})
                self.storage.update_article_content(article.id, content)
            return article

        enriched = await asyncio.gather(*(enrich(article) for article in selected))
        return list(enriched) + articles[max_articles:]

    @staticmethod
    def _dedupe(articles: list[NewsArticle]) -> list[NewsArticle]:
        seen_urls: set[str] = set()
        seen_titles: list[set[str]] = []
        output: list[NewsArticle] = []
        for article in sorted(articles, key=lambda item: item.published_at, reverse=True):
            if article.url in seen_urls:
                continue
            tokens = title_tokens(article.title)
            if any(jaccard(tokens, existing) >= 0.86 for existing in seen_titles):
                continue
            seen_urls.add(article.url)
            seen_titles.append(tokens)
            output.append(article)
        return output

    def _cluster(self, articles: list[NewsArticle]) -> list[NewsEvent]:
        clusters: list[list[NewsArticle]] = []
        cluster_tokens: list[set[str]] = []
        for article in sorted(articles, key=lambda item: item.published_at, reverse=True):
            tokens = title_tokens(article.title)
            best_index = -1
            best_score = 0.0
            for index, existing in enumerate(cluster_tokens):
                score = jaccard(tokens, existing)
                if score > best_score:
                    best_index, best_score = index, score
            if best_index >= 0 and best_score >= 0.34:
                clusters[best_index].append(article)
                cluster_tokens[best_index] |= tokens
            else:
                clusters.append([article])
                cluster_tokens.append(set(tokens))

        now = datetime.now(UTC)
        events: list[NewsEvent] = []
        for cluster, tokens in zip(clusters, cluster_tokens, strict=True):
            primary = max(cluster, key=lambda item: item.published_at)
            source_names = sorted({item.source_name for item in cluster})
            article_ids = [item.id for item in cluster]
            summary_parts = [item.summary for item in cluster if item.summary]
            summary = clean_text(" ".join(summary_parts))[:2600]
            age_hours = max((now - primary.published_at).total_seconds() / 3600, 0)
            freshness = math.exp(-age_hours / max(self.settings.news_lookback_hours, 6))
            term_hits = sum(
                1 for term in MILITARY_TERMS if term.lower() in f"{primary.title} {summary}".lower()
            )
            authority = min(len(source_names) / 3, 1.0)
            score = round(
                100 * (0.58 * freshness + 0.22 * authority + 0.20 * min(term_hits / 4, 1)), 2
            )
            event_key = "|".join(sorted(article_ids))
            events.append(
                NewsEvent(
                    id=stable_id("event", event_key),
                    title=primary.title,
                    summary=summary or primary.title,
                    published_at=primary.published_at,
                    score=score,
                    article_ids=article_ids,
                    source_names=source_names,
                    keywords=sorted(tokens)[:20],
                )
            )
        return sorted(events, key=lambda event: (event.score, event.published_at), reverse=True)
