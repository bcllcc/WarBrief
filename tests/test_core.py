from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from warbrief.config import Settings
from warbrief.models import MediaAsset, NewsArticle, NewsEvent
from warbrief.providers.media import DVIDSMediaProvider
from warbrief.services.news import NewsService
from warbrief.services.render import build_rights_manifest
from warbrief.storage import Storage
from warbrief.utils import _prepare_command, canonical_url, jaccard, stable_id, title_tokens


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values = {
        "data_dir": tmp_path / "runtime",
        "demo_mode": True,
        "news_scheduler_enabled": False,
        "news_refresh_on_startup": False,
        "video_width": 270,
        "video_height": 480,
        "video_fps": 12,
        "video_preset": "ultrafast",
        "video_max_sentences": 3,
    }
    values.update(overrides)
    settings = Settings(**values)
    settings.ensure_directories()
    return settings


def test_url_tokens_and_stable_id() -> None:
    assert canonical_url("HTTPS://Example.COM/a//b/?utm_source=x#top") == "https://example.com/a/b"
    assert stable_id("event", "same") == stable_id("event", "same")
    assert stable_id("event", "same") != stable_id("event", "other")
    a = title_tokens("NATO launches new military exercise")
    b = title_tokens("New NATO military exercise begins")
    assert jaccard(a, b) > 0.3


def test_windows_ffmpeg_filter_drive_path_is_escaped() -> None:
    command = [
        "ffmpeg",
        "-i",
        r"C:\WarBrief\visual.mp4",
        "-filter_complex",
        "[0:v]ass=C:/WarBrief/subtitles.ass[v]",
        r"C:\WarBrief\video.mp4",
    ]
    prepared = _prepare_command(command, platform="nt")
    assert prepared[2] == command[2]
    assert prepared[4] == r"[0:v]ass=C\\:/WarBrief/subtitles.ass[v]"
    assert prepared[5] == command[5]


def test_storage_and_event_clustering(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    storage = Storage(settings.db_path)
    service = NewsService(settings, storage)
    now = datetime.now(UTC)
    articles = [
        NewsArticle(
            id="article_1",
            provider="test",
            source_name="Source A",
            title="NATO begins major air defense exercise",
            summary="NATO began an air defense exercise involving several units.",
            url="https://example.com/a",
            published_at=now,
        ),
        NewsArticle(
            id="article_2",
            provider="test",
            source_name="Source B",
            title="Major NATO air-defence exercise begins",
            summary="A second outlet reported the same exercise.",
            url="https://example.com/b",
            published_at=now,
        ),
    ]
    storage.upsert_articles(articles)
    events = service._cluster(articles)
    storage.upsert_events(events)
    assert events
    assert storage.get_event(events[0].id) is not None
    assert len(storage.get_articles(["article_2", "article_1"])) == 2
    storage.close()


def test_rights_gate() -> None:
    allowed = MediaAsset(
        asset_id="a",
        sentence_id="s1",
        provider="generated-placeholder",
        local_path="placeholder.png",
        source_url="generated://s1",
        license="Project-generated asset",
        attribution="WarBrief",
        render_allowed=True,
    )
    blocked = allowed.model_copy(
        update={"asset_id": "b", "sentence_id": "s2", "render_allowed": False}
    )
    assert build_rights_manifest("event", [allowed]).all_render_allowed is True
    assert build_rights_manifest("event", [allowed, blocked]).all_render_allowed is False


def test_dvids_file_selection_respects_bitrate(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        dvids_api_key="key-test",
        dvids_video_max_bitrate_kbps=3000,
    )
    provider = DVIDSMediaProvider(settings)
    selected = provider._select_video_file(
        [
            {
                "src": "low.mp4",
                "type": "video/mp4",
                "width": 720,
                "height": 406,
                "bitrate": 800,
            },
            {
                "src": "preferred.mp4",
                "type": "video/mp4",
                "width": 1920,
                "height": 1080,
                "bitrate": 2500,
            },
            {
                "src": "too-large.mp4",
                "type": "video/mp4",
                "width": 3840,
                "height": 2160,
                "bitrate": 8000,
            },
        ]
    )
    assert selected["src"] == "preferred.mp4"


def test_dvids_video_clipper_accepts_mp4_input(tmp_path: Path) -> None:
    import shutil

    import pytest

    from warbrief.utils import run_command

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required")
    settings = make_settings(tmp_path, video_preset="ultrafast")
    provider = DVIDSMediaProvider(settings)
    source = tmp_path / "source.mp4"
    output = tmp_path / "clip.mp4"
    run_command(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=12",
            "-t",
            "2",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ]
    )
    provider._clip_remote_video(str(source), output, 0.2, 1.0)
    assert output.exists() and output.stat().st_size > 5_000


def test_news_retention_prunes_unreferenced_stale_rows(tmp_path: Path) -> None:
    from datetime import timedelta

    settings = make_settings(tmp_path)
    storage = Storage(settings.db_path)
    now = datetime.now(UTC)
    old_article = NewsArticle(
        id="old_article",
        provider="test",
        source_name="Old",
        title="Old military story",
        summary="Old",
        url="https://example.com/old",
        published_at=now - timedelta(days=30),
    )
    old_event = NewsEvent(
        id="old_event",
        title=old_article.title,
        summary=old_article.summary,
        published_at=old_article.published_at,
        article_ids=[old_article.id],
        source_names=[old_article.source_name],
    )
    storage.upsert_articles([old_article])
    storage.upsert_events([old_event])
    deleted_events, deleted_articles = storage.prune_news(now - timedelta(days=14))
    assert (deleted_events, deleted_articles) == (1, 1)
    assert storage.get_event(old_event.id) is None
    assert storage.get_article(old_article.id) is None
    storage.close()
