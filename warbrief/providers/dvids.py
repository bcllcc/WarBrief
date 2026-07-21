from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
from PIL import Image

from warbrief.models import MediaAsset, ShotRequest
from warbrief.providers.media_base import MediaProvider
from warbrief.utils import clean_text, redact_url_secrets, run_command, stable_id

logger = logging.getLogger(__name__)


class DVIDSMediaProvider(MediaProvider):
    """DVIDS adapter with an asset-level third-party-content screen."""

    name = "dvids"
    search_endpoint = "https://api.dvidshub.net/search"
    asset_endpoint = "https://api.dvidshub.net/asset"
    third_party_markers = (
        "reuters",
        "associated press",
        "getty",
        "afp",
        "copyright",
        "all rights reserved",
        "courtesy video",
        "courtesy photo",
        "courtesy image",
    )

    async def find(
        self, shot: ShotRequest, output_dir: Path, used_source_urls: set[str]
    ) -> MediaAsset | None:
        if not self.settings.dvids_api_key:
            return None
        media_types = ["video", "image"] if self._prefer_video(shot) else ["image", "video"]
        async with httpx.AsyncClient(
            timeout=self.settings.media_download_timeout_seconds,
            follow_redirects=True,
            proxy=self.settings.http_proxy_url or None,
            headers={"User-Agent": "WarBrief/0.1"},
        ) as client:
            for media_type in media_types:
                asset = await self._search_type(
                    client, media_type, shot, output_dir, used_source_urls
                )
                if asset:
                    return asset
        return None

    def _prefer_video(self, shot: ShotRequest) -> bool:
        return self.settings.dvids_prefer_video and shot.visual_type in {
            "event",
            "archive",
            "generic",
        }

    async def _search_type(
        self,
        client: httpx.AsyncClient,
        media_type: str,
        shot: ShotRequest,
        output_dir: Path,
        used_source_urls: set[str],
    ) -> MediaAsset | None:
        params = {
            "q": shot.query,
            "type": media_type,
            "max_results": "12",
            "sort": "date",
            "sortdir": "desc",
            "thumb_width": "1600",
            "short_description_length": "300",
            "api_key": self.settings.dvids_api_key,
        }
        if media_type == "video":
            params["to_duration"] = "900"
        response = await client.get(self.search_endpoint, params=params)
        response.raise_for_status()
        results = response.json().get("results", [])
        if not isinstance(results, list):
            return None

        for item in results:
            asset_id = str(item.get("id", ""))
            if not asset_id:
                continue
            detail_response = await client.get(
                self.asset_endpoint,
                params={
                    "id": asset_id,
                    "api_key": self.settings.dvids_api_key,
                    "thumb_width": "1600",
                },
            )
            if detail_response.status_code >= 400:
                continue
            detail = detail_response.json().get("results", {})
            if not isinstance(detail, dict) or self._contains_third_party_content(detail):
                continue
            try:
                if media_type == "video":
                    asset = await self._video(detail, item, shot, output_dir, used_source_urls)
                else:
                    asset = await self._image(detail, item, shot, output_dir, used_source_urls)
                if asset:
                    return asset
            except Exception as exc:
                logger.info("DVIDS %s asset rejected (%s): %s", media_type, asset_id, exc)
        return None

    def _contains_third_party_content(self, detail: dict) -> bool:
        searchable = clean_text(
            " ".join(
                str(detail.get(key, "")) for key in ("title", "description", "keywords", "category")
            )
        ).lower()
        return any(marker in searchable for marker in self.third_party_markers)

    @staticmethod
    def _attribution(detail: dict, item: dict) -> str:
        credits = detail.get("credit") or []
        if isinstance(credits, list):
            names = [
                clean_text(f"{credit.get('rank', '')} {credit.get('name', '')}")
                for credit in credits
                if isinstance(credit, dict)
            ]
            attribution = ", ".join(name for name in names if name)
        else:
            attribution = clean_text(str(credits))
        return attribution or clean_text(str(item.get("credit", ""))) or "DVIDS"

    async def _image(
        self,
        detail: dict,
        item: dict,
        shot: ShotRequest,
        output_dir: Path,
        used_source_urls: set[str],
    ) -> MediaAsset | None:
        source_url = str(detail.get("image") or item.get("thumbnail") or "")
        if not source_url or source_url in used_source_urls:
            return None
        path = output_dir / f"{shot.sentence_id}-dvids.jpg"
        await self.download(source_url, path)
        with Image.open(path) as image:
            width, height = image.size
        return self._asset(
            detail,
            item,
            shot,
            path,
            source_url,
            "image",
            width=width,
            height=height,
        )

    async def _video(
        self,
        detail: dict,
        item: dict,
        shot: ShotRequest,
        output_dir: Path,
        used_source_urls: set[str],
    ) -> MediaAsset | None:
        candidate = self._select_video_file(detail.get("files"))
        source_url = str(candidate.get("src", "")) if candidate else ""
        if not source_url:
            source_url = str(detail.get("hls_url") or item.get("hls_url") or "")
        public_url = redact_url_secrets(source_url)
        if not source_url or public_url in used_source_urls:
            return None

        wanted_seconds = max(shot.duration_ms / 1000 + 0.75, 2.0)
        source_duration = max(self._number(detail.get("duration")), 0.0)
        start_seconds = max(self._number(detail.get("time_start")), 0.0)
        if source_duration and start_seconds + wanted_seconds > source_duration:
            start_seconds = max(0.0, source_duration - wanted_seconds)
        path = output_dir / f"{shot.sentence_id}-dvids.mp4"
        await asyncio.to_thread(
            self._clip_remote_video,
            source_url,
            path,
            start_seconds,
            wanted_seconds,
        )
        return self._asset(
            detail,
            item,
            shot,
            path,
            public_url,
            "video",
            width=self._integer(candidate.get("width")) if candidate else None,
            height=self._integer(candidate.get("height")) if candidate else None,
            duration_ms=round(wanted_seconds * 1000),
        )

    def _asset(
        self,
        detail: dict,
        item: dict,
        shot: ShotRequest,
        path: Path,
        source_url: str,
        kind: str,
        **dimensions: int | None,
    ) -> MediaAsset:
        raw_id = str(detail.get("id") or item.get("id") or source_url)
        return MediaAsset(
            asset_id=stable_id("asset", raw_id),
            sentence_id=shot.sentence_id,
            provider=self.name,
            kind=kind,
            local_path=str(path),
            source_url=source_url,
            page_url=str(detail.get("url") or item.get("url") or ""),
            title=clean_text(str(detail.get("title") or item.get("title") or "")),
            attribution=self._attribution(detail, item),
            license="DVIDS asset; asset-specific restrictions screened; manual review required",
            render_allowed=True,
            rights_review_required=True,
            **dimensions,
        )

    def _clip_remote_video(
        self,
        source_url: str,
        output_path: Path,
        start_seconds: float,
        duration_seconds: float,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start_seconds:.3f}",
                "-i",
                source_url,
                "-t",
                f"{duration_seconds:.3f}",
                "-map",
                "0:v:0",
                "-an",
                "-vf",
                "scale='min(1280,iw)':-2",
                "-c:v",
                "libx264",
                "-preset",
                self.settings.video_preset,
                "-crf",
                "24",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            timeout=max(self.settings.media_download_timeout_seconds * 4, 180),
        )
        if not output_path.exists() or output_path.stat().st_size < 5_000:
            raise RuntimeError("DVIDS video clip is missing or empty")

    def _select_video_file(self, files: object) -> dict:
        if not isinstance(files, list):
            return {}
        candidates = [
            item
            for item in files
            if isinstance(item, dict)
            and str(item.get("type", "")).lower() == "video/mp4"
            and item.get("src")
        ]
        if not candidates:
            return {}
        limit = max(self.settings.dvids_video_max_bitrate_kbps, 300)
        preferred = [
            item for item in candidates if self._integer(item.get("bitrate"), limit + 1) <= limit
        ]
        return max(
            preferred or candidates,
            key=lambda item: (
                self._integer(item.get("width")),
                self._integer(item.get("height")),
                self._integer(item.get("bitrate")),
            ),
        )

    @staticmethod
    def _number(value: object) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _integer(value: object, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default
