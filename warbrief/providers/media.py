from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from warbrief.config import Settings
from warbrief.models import MediaAsset, ShotRequest
from warbrief.providers.dvids import DVIDSMediaProvider
from warbrief.providers.media_base import MediaProvider
from warbrief.providers.wikimedia import WikimediaMediaProvider
from warbrief.utils import stable_id

logger = logging.getLogger(__name__)

# Re-exported so existing imports and tests remain stable.
__all__ = [
    "DVIDSMediaProvider",
    "MaterialService",
    "MediaProvider",
    "PlaceholderMediaProvider",
    "WikimediaMediaProvider",
]


class PlaceholderMediaProvider(MediaProvider):
    name = "generated-placeholder"

    async def find(
        self, shot: ShotRequest, output_dir: Path, used_source_urls: set[str]
    ) -> MediaAsset | None:
        path = output_dir / f"{shot.sentence_id}-placeholder.png"
        self._render_card(path, shot)
        source_url = f"generated://{shot.sentence_id}"
        return MediaAsset(
            asset_id=stable_id("asset", source_url),
            sentence_id=shot.sentence_id,
            provider=self.name,
            kind="image",
            local_path=str(path),
            source_url=source_url,
            title=shot.query,
            attribution="Generated locally by WarBrief",
            license="Project-generated asset",
            render_allowed=True,
            rights_review_required=False,
            width=self.settings.video_width,
            height=self.settings.video_height,
        )

    def _font(self, size: int, *, bold: bool = False) -> ImageFont.ImageFont:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ]
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _render_card(self, path: Path, shot: ShotRequest) -> None:
        width, height = self.settings.video_width, self.settings.video_height
        image = Image.new("RGB", (width, height), (9, 14, 24))
        draw = ImageDraw.Draw(image)
        for y in range(height):
            t = y / max(height - 1, 1)
            draw.line((0, y, width, y), fill=(int(9 + 12 * t), int(14 + 22 * t), int(24 + 38 * t)))
        grid = max(width // 12, 48)
        for x in range(0, width, grid):
            draw.line((x, 0, x, height), fill=(28, 44, 65))
        for y in range(0, height, grid):
            draw.line((0, y, width, y), fill=(28, 44, 65))

        margin = int(width * 0.075)
        draw.rounded_rectangle(
            (margin, int(height * 0.13), width - margin, int(height * 0.84)),
            radius=max(width // 35, 18),
            fill=(12, 22, 35),
            outline=(63, 97, 126),
            width=max(width // 240, 2),
        )
        left = margin + int(width * 0.04)
        accent_y = int(height * 0.19)
        draw.rectangle(
            (left, accent_y, margin + int(width * 0.15), accent_y + 8), fill=(211, 171, 86)
        )
        label_font = self._font(max(width // 34, 22), bold=True)
        title_font = self._font(max(width // 20, 34), bold=True)
        body_font = self._font(max(width // 34, 23))
        draw.text(
            (left, accent_y + 28), "WARBRIEF · AUTO B-ROLL", font=label_font, fill=(172, 193, 210)
        )

        y = int(height * 0.34)
        for line in self._wrap(
            draw,
            shot.query or "military news visual",
            title_font,
            width - 2 * margin - int(width * 0.08),
        )[:6]:
            draw.text((left, y), line, font=title_font, fill=(239, 244, 248))
            y += int(getattr(title_font, "size", 36) * 1.35)
        footer = f"镜头需求 · {shot.visual_type.upper()} · {shot.duration_ms / 1000:.1f}s"
        draw.text((left, int(height * 0.69)), footer, font=body_font, fill=(132, 159, 180))
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, quality=95)

    @staticmethod
    def _wrap(
        draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int
    ) -> list[str]:
        words = text.split()
        separator = " "
        if len(words) <= 1:
            words, separator = list(text), ""
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current}{separator if current else ''}{word}"
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if current and bbox[2] - bbox[0] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines


class MaterialService:
    def __init__(self, settings: Settings):
        self.providers: list[MediaProvider] = []
        if settings.dvids_enabled and settings.dvids_api_key:
            self.providers.append(DVIDSMediaProvider(settings))
        if settings.wikimedia_enabled and not settings.demo_mode:
            self.providers.append(WikimediaMediaProvider(settings))
        if settings.media_allow_placeholder:
            self.providers.append(PlaceholderMediaProvider(settings))

    async def acquire(self, shots: list[ShotRequest], output_dir: Path) -> list[MediaAsset]:
        output_dir.mkdir(parents=True, exist_ok=True)
        used_urls: set[str] = set()
        assets: list[MediaAsset] = []
        for shot in shots:
            selected: MediaAsset | None = None
            for provider in self.providers:
                try:
                    selected = await provider.find(shot, output_dir, used_urls)
                except Exception as exc:
                    logger.warning(
                        "Media provider %s failed (%s)", provider.name, type(exc).__name__
                    )
                    selected = None
                if selected and selected.render_allowed:
                    break
            if selected is None:
                raise RuntimeError(
                    f"No renderable material found for {shot.sentence_id}; enable placeholders"
                )
            used_urls.add(selected.source_url)
            assets.append(selected)
        return assets
