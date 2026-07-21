from __future__ import annotations

import html
import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from warbrief.models import MediaAsset, ShotRequest
from warbrief.providers.media_base import MediaProvider
from warbrief.utils import clean_text, stable_id

logger = logging.getLogger(__name__)


class WikimediaMediaProvider(MediaProvider):
    name = "wikimedia"
    endpoint = "https://commons.wikimedia.org/w/api.php"
    allowed = ("public domain", "cc0", "cc by", "creative commons attribution")
    forbidden = (
        "cc by-nc",
        "cc by-nd",
        "noncommercial",
        "non-commercial",
        "no derivatives",
        "fair use",
    )

    async def find(
        self, shot: ShotRequest, output_dir: Path, used_source_urls: set[str]
    ) -> MediaAsset | None:
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": shot.query,
            "gsrnamespace": "6",
            "gsrlimit": "12",
            "prop": "imageinfo",
            "iiprop": "url|mime|size|extmetadata",
            "iiurlwidth": "1920",
            "format": "json",
            "origin": "*",
        }
        async with httpx.AsyncClient(
            timeout=self.settings.media_download_timeout_seconds,
            proxy=self.settings.http_proxy_url or None,
            headers={"User-Agent": "WarBrief/0.1"},
        ) as client:
            response = await client.get(self.endpoint, params=params)
            response.raise_for_status()
            pages = list(response.json().get("query", {}).get("pages", {}).values())

        for page in pages:
            info_list = page.get("imageinfo") or []
            if not info_list:
                continue
            info = info_list[0]
            mime = str(info.get("mime", ""))
            source_url = str(info.get("thumburl") or info.get("url") or "")
            original_url = str(info.get("url") or source_url)
            if not source_url or original_url in used_source_urls:
                continue
            if mime not in {"image/jpeg", "image/png", "image/webp"}:
                continue

            metadata = info.get("extmetadata") or {}
            license_text = clean_text(
                f"{self._meta(metadata, 'LicenseShortName')} {self._meta(metadata, 'UsageTerms')}"
            )
            lowered = license_text.lower()
            if not any(fragment in lowered for fragment in self.allowed):
                continue
            if any(fragment in lowered for fragment in self.forbidden):
                continue

            artist = clean_text(html.unescape(self._meta(metadata, "Artist")))
            credit = clean_text(html.unescape(self._meta(metadata, "Credit")))
            suffix = Path(urlparse(source_url).path).suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                suffix = ".jpg"
            path = output_dir / f"{shot.sentence_id}-wikimedia{suffix}"
            try:
                await self.download(source_url, path)
                with Image.open(path) as image:
                    width, height = image.size
                return MediaAsset(
                    asset_id=stable_id("asset", original_url),
                    sentence_id=shot.sentence_id,
                    provider=self.name,
                    kind="image",
                    local_path=str(path),
                    source_url=original_url,
                    page_url=str(info.get("descriptionurl") or ""),
                    title=clean_text(str(page.get("title", ""))).removeprefix("File:"),
                    attribution=credit or artist or "Wikimedia Commons contributor",
                    license=license_text or "Wikimedia Commons file-level license",
                    render_allowed=True,
                    rights_review_required="cc by-sa" in lowered,
                    width=width,
                    height=height,
                )
            except Exception as exc:
                logger.info("Wikimedia asset rejected: %s", exc)
        return None

    @staticmethod
    def _meta(metadata: dict, key: str) -> str:
        return str((metadata.get(key) or {}).get("value", ""))
