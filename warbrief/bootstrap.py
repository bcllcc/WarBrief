from __future__ import annotations

from dataclasses import dataclass, field

from warbrief.config import Settings
from warbrief.providers.llm import create_llm_provider
from warbrief.providers.media import MaterialService
from warbrief.providers.tts import VoiceService, create_tts_provider
from warbrief.services.news import NewsService
from warbrief.services.pipeline import PipelineRunner
from warbrief.services.render import FFmpegRenderer
from warbrief.storage import Storage


@dataclass(slots=True)
class AppContext:
    settings: Settings
    storage: Storage
    news: NewsService
    pipeline: PipelineRunner
    tasks: set = field(default_factory=set)

    def close(self) -> None:
        self.storage.close()


def build_context(settings: Settings | None = None) -> AppContext:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    storage = Storage(settings.db_path)
    news = NewsService(settings, storage)
    llm = create_llm_provider(settings)
    voice = VoiceService(settings, create_tts_provider(settings))
    material = MaterialService(settings)
    renderer = FFmpegRenderer(settings)
    pipeline = PipelineRunner(
        settings=settings,
        storage=storage,
        news_service=news,
        llm=llm,
        voice_service=voice,
        material_service=material,
        renderer=renderer,
    )
    return AppContext(settings=settings, storage=storage, news=news, pipeline=pipeline)
