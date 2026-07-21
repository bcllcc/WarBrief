from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from warbrief.config import Settings
from warbrief.models import MediaAsset, ShotRequest


class MediaProvider(ABC):
    """Common contract for rights-aware renderable-media providers."""

    name: str

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    async def find(
        self, shot: ShotRequest, output_dir: Path, used_source_urls: set[str]
    ) -> MediaAsset | None:
        raise NotImplementedError

    async def download(self, url: str, path: Path, *, limit_mb: int = 40) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(
            timeout=self.settings.media_download_timeout_seconds,
            follow_redirects=True,
            proxy=self.settings.http_proxy_url or None,
            headers={"User-Agent": "WarBrief/0.1"},
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                total = 0
                with path.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > limit_mb * 1024 * 1024:
                            raise RuntimeError(f"Media file exceeds {limit_mb} MB demo limit")
                        handle.write(chunk)
        if not path.exists() or path.stat().st_size < 200:
            raise RuntimeError(f"Downloaded media is empty: {url}")
