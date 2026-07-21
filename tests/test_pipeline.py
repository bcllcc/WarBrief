from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from warbrief.bootstrap import build_context
from warbrief.config import Settings

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg is required for the end-to-end test",
)


@pytest.mark.asyncio
async def test_complete_mock_pipeline(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "runtime",
        demo_mode=True,
        news_scheduler_enabled=False,
        news_refresh_on_startup=False,
        news_mock_fallback=True,
        llm_provider="mock",
        tts_provider="mock",
        dvids_enabled=False,
        wikimedia_enabled=False,
        media_allow_placeholder=True,
        video_width=270,
        video_height=480,
        video_fps=12,
        video_crf=28,
        video_preset="ultrafast",
        video_max_sentences=3,
    )
    context = build_context(settings)
    try:
        refresh = await context.news.refresh()
        assert refresh.articles_collected >= 1
        events = context.storage.list_events(limit=1)
        assert events

        job = context.pipeline.create_job(events[0].id)
        final = await context.pipeline.run_job(job.id)
        assert final.status == "completed", final.error

        artifact_dir = Path(final.artifact_dir)
        required = [
            "event.json",
            "sources.json",
            "fact_pack.json",
            "script_manifest.json",
            "voice_manifest.json",
            "shot_requests.json",
            "media_assets.json",
            "rights_manifest.json",
            "subtitles.ass",
            "subtitles.srt",
            "timeline.json",
            "qc_report.json",
            "render/video.mp4",
        ]
        for relative in required:
            path = artifact_dir / relative
            assert path.exists() and path.stat().st_size > 0, relative

        video = Path(final.video_path)
        archive = Path(final.archive_path)
        assert video.exists() and video.stat().st_size > 10_000
        assert archive.exists()
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
        assert "fact_pack.json" in names
        assert "render/video.mp4" in names

        qc = (artifact_dir / "qc_report.json").read_text(encoding="utf-8")
        assert '"passed": true' in qc
    finally:
        context.close()
