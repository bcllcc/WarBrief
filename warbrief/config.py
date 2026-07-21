from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw is not None else default
    except ValueError:
        return default


def _csv(raw: str | None, fallback: Iterable[str] = ()) -> list[str]:
    if raw is None or not raw.strip():
        return list(fallback)
    return [part.strip() for part in raw.split(",") if part.strip()]


def load_env_file(path: Path | str = ".env") -> None:
    """Load a local .env without adding a runtime dependency.

    Existing environment variables always win.
    """
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


DEFAULT_RSS_URLS = [
    "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=20",
    "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=20",
]


@dataclass(slots=True)
class Settings:
    app_env: str = "development"
    demo_mode: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = Path("./data/runtime")

    news_scheduler_enabled: bool = True
    news_refresh_on_startup: bool = True
    news_poll_interval_minutes: int = 30
    news_lookback_hours: int = 24
    news_max_items: int = 60
    news_retention_days: int = 14
    news_gdelt_enabled: bool = True
    news_gdelt_query: str = "(military OR defense OR missile OR navy OR airforce OR NATO OR drone)"
    news_rss_urls: list[str] = field(default_factory=lambda: list(DEFAULT_RSS_URLS))
    news_mock_fallback: bool = True

    llm_provider: str = "mock"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 120

    tts_provider: str = "mock"
    tts_voice: str = "zh-CN-YunxiNeural"
    tts_rate: str = "+0%"
    tts_base_url: str = ""
    tts_api_key: str = ""
    tts_model: str = "tts-1"
    tts_timeout_seconds: int = 180

    dvids_api_key: str = ""
    dvids_enabled: bool = True
    dvids_prefer_video: bool = True
    dvids_video_max_bitrate_kbps: int = 3000
    wikimedia_enabled: bool = True
    media_allow_placeholder: bool = True
    media_download_timeout_seconds: int = 60

    video_width: int = 720
    video_height: int = 1280
    video_fps: int = 25
    video_crf: int = 22
    video_preset: str = "veryfast"
    video_max_sentences: int = 8
    video_min_sentence_seconds: float = 2.2
    video_max_sentence_seconds: float = 7.0
    bgm_path: str = ""

    http_proxy_url: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        load_env_file()
        settings = cls(
            app_env=os.getenv("APP_ENV", "development"),
            demo_mode=_bool("DEMO_MODE", True),
            host=os.getenv("HOST", "0.0.0.0"),
            port=_int("PORT", 8000),
            data_dir=Path(os.getenv("WARBRIEF_DATA_DIR", "./data/runtime")),
            news_scheduler_enabled=_bool("NEWS_SCHEDULER_ENABLED", True),
            news_refresh_on_startup=_bool("NEWS_REFRESH_ON_STARTUP", True),
            news_poll_interval_minutes=_int("NEWS_POLL_INTERVAL_MINUTES", 30),
            news_lookback_hours=_int("NEWS_LOOKBACK_HOURS", 24),
            news_max_items=_int("NEWS_MAX_ITEMS", 60),
            news_retention_days=_int("NEWS_RETENTION_DAYS", 14),
            news_gdelt_enabled=_bool("NEWS_GDELT_ENABLED", True),
            news_gdelt_query=os.getenv(
                "NEWS_GDELT_QUERY",
                "(military OR defense OR missile OR navy OR airforce OR NATO OR drone)",
            ),
            news_rss_urls=_csv(os.getenv("NEWS_RSS_URLS"), DEFAULT_RSS_URLS),
            news_mock_fallback=_bool("NEWS_MOCK_FALLBACK", True),
            llm_provider=os.getenv("LLM_PROVIDER", "mock").strip().lower(),
            llm_base_url=os.getenv("LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", ""),
            llm_timeout_seconds=_int("LLM_TIMEOUT_SECONDS", 120),
            tts_provider=os.getenv("TTS_PROVIDER", "mock").strip().lower(),
            tts_voice=os.getenv("TTS_VOICE", "zh-CN-YunxiNeural"),
            tts_rate=os.getenv("TTS_RATE", "+0%"),
            tts_base_url=os.getenv("TTS_BASE_URL", "").rstrip("/"),
            tts_api_key=os.getenv("TTS_API_KEY", ""),
            tts_model=os.getenv("TTS_MODEL", "tts-1"),
            tts_timeout_seconds=_int("TTS_TIMEOUT_SECONDS", 180),
            dvids_api_key=os.getenv("DVIDS_API_KEY", ""),
            dvids_enabled=_bool("DVIDS_ENABLED", True),
            dvids_prefer_video=_bool("DVIDS_PREFER_VIDEO", True),
            dvids_video_max_bitrate_kbps=_int("DVIDS_VIDEO_MAX_BITRATE_KBPS", 3000),
            wikimedia_enabled=_bool("WIKIMEDIA_ENABLED", True),
            media_allow_placeholder=_bool("MEDIA_ALLOW_PLACEHOLDER", True),
            media_download_timeout_seconds=_int("MEDIA_DOWNLOAD_TIMEOUT_SECONDS", 60),
            video_width=_int("VIDEO_WIDTH", 720),
            video_height=_int("VIDEO_HEIGHT", 1280),
            video_fps=_int("VIDEO_FPS", 25),
            video_crf=_int("VIDEO_CRF", 22),
            video_preset=os.getenv("VIDEO_PRESET", "veryfast"),
            video_max_sentences=_int("VIDEO_MAX_SENTENCES", 8),
            video_min_sentence_seconds=_float("VIDEO_MIN_SENTENCE_SECONDS", 2.2),
            video_max_sentence_seconds=_float("VIDEO_MAX_SENTENCE_SECONDS", 7.0),
            bgm_path=os.getenv("BGM_PATH", ""),
            http_proxy_url=os.getenv("HTTP_PROXY_URL", ""),
        )
        settings.ensure_directories()
        return settings

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "warbrief.sqlite3"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def use_real_llm(self) -> bool:
        return (
            not self.demo_mode
            and self.llm_provider != "mock"
            and bool(self.llm_base_url and self.llm_model)
        )

    def public_summary(self) -> dict[str, object]:
        return {
            "app_env": self.app_env,
            "demo_mode": self.demo_mode,
            "llm_provider": self.llm_provider,
            "llm_configured": self.use_real_llm,
            "tts_provider": self.tts_provider,
            "dvids_configured": bool(self.dvids_api_key),
            "dvids_prefer_video": self.dvids_prefer_video,
            "wikimedia_enabled": self.wikimedia_enabled,
            "news_scheduler_enabled": self.news_scheduler_enabled,
            "news_poll_interval_minutes": self.news_poll_interval_minutes,
            "news_retention_days": self.news_retention_days,
            "video_resolution": f"{self.video_width}x{self.video_height}",
        }
