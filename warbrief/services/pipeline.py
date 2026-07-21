from __future__ import annotations

import asyncio
import logging
import shutil
import traceback
import uuid
from pathlib import Path

from warbrief.config import Settings
from warbrief.models import JobRecord
from warbrief.providers.llm import LLMProvider
from warbrief.providers.media import MaterialService
from warbrief.providers.tts import VoiceService
from warbrief.services.news import NewsService
from warbrief.services.render import (
    FFmpegRenderer,
    build_rights_manifest,
    build_shot_requests,
    build_timeline,
    write_ass_subtitles,
    write_srt_subtitles,
)
from warbrief.storage import Storage
from warbrief.utils import json_dump

logger = logging.getLogger(__name__)


class PipelineRunner:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        news_service: NewsService,
        llm: LLMProvider,
        voice_service: VoiceService,
        material_service: MaterialService,
        renderer: FFmpegRenderer,
    ):
        self.settings = settings
        self.storage = storage
        self.news_service = news_service
        self.llm = llm
        self.voice_service = voice_service
        self.material_service = material_service
        self.renderer = renderer
        self._job_locks: dict[str, asyncio.Lock] = {}

    def create_job(self, event_id: str) -> JobRecord:
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        artifact_dir = self.settings.jobs_dir / job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        job = JobRecord(
            id=job_id,
            event_id=event_id,
            artifact_dir=str(artifact_dir),
        )
        return self.storage.create_job(job)

    async def run_job(self, job_id: str) -> JobRecord:
        lock = self._job_locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            job = self.storage.get_job(job_id)
            if not job:
                raise KeyError(f"Unknown job: {job_id}")
            if job.status == "completed":
                return job
            artifact_dir = Path(job.artifact_dir)
            artifact_dir.mkdir(parents=True, exist_ok=True)
            try:
                self._update(job_id, "running", "load_event", 5)
                event = self.storage.get_event(job.event_id)
                if event is None:
                    raise RuntimeError(f"Event not found: {job.event_id}")
                articles = self.storage.get_articles(event.article_ids)
                if not articles:
                    raise RuntimeError("Event has no source articles")
                json_dump(artifact_dir / "event.json", event)
                articles = await self.news_service.enrich_articles(articles)
                json_dump(artifact_dir / "sources.json", articles)

                self._update(job_id, "running", "fact_pack", 15)
                fact_pack = await self.llm.build_fact_pack(event, articles)
                json_dump(artifact_dir / "fact_pack.json", fact_pack)

                self._update(job_id, "running", "script", 28)
                script = await self.llm.build_script(
                    event, fact_pack, self.settings.video_max_sentences
                )
                json_dump(artifact_dir / "script_manifest.json", script)

                self._update(job_id, "running", "voice", 42)
                voice = await self.voice_service.generate(script, artifact_dir / "audio")
                json_dump(artifact_dir / "voice_manifest.json", voice)

                self._update(job_id, "running", "storyboard", 53)
                shots = build_shot_requests(script, voice)
                json_dump(artifact_dir / "shot_requests.json", shots)

                self._update(job_id, "running", "materials", 64)
                assets = await self.material_service.acquire(shots, artifact_dir / "materials")
                json_dump(artifact_dir / "media_assets.json", assets)
                rights = build_rights_manifest(event.id, assets)
                json_dump(artifact_dir / "rights_manifest.json", rights)
                if not rights.all_render_allowed:
                    raise RuntimeError("Rights gate rejected one or more assets")

                self._update(job_id, "running", "timeline", 75)
                subtitle_path = artifact_dir / "subtitles.ass"
                write_ass_subtitles(
                    subtitle_path,
                    script,
                    voice,
                    assets,
                    self.settings.video_width,
                    self.settings.video_height,
                )
                write_srt_subtitles(artifact_dir / "subtitles.srt", voice)
                timeline = build_timeline(self.settings, script, voice, assets, subtitle_path)
                json_dump(artifact_dir / "timeline.json", timeline)

                self._update(job_id, "running", "render", 84)
                video_path = await asyncio.to_thread(
                    self.renderer.render, timeline, artifact_dir / "render"
                )

                self._update(job_id, "running", "quality_control", 94)
                qc = await asyncio.to_thread(
                    self.renderer.quality_check, job_id, video_path, timeline, rights
                )
                json_dump(artifact_dir / "qc_report.json", qc)
                archive_path = await asyncio.to_thread(self._archive, artifact_dir)

                final = self.storage.update_job(
                    job_id,
                    status="completed" if video_path.exists() else "failed",
                    stage="completed" if qc.passed else "completed_with_warnings",
                    progress=100,
                    video_path=str(video_path),
                    archive_path=str(archive_path),
                    error="" if qc.passed else "QC completed with warnings; inspect qc_report.json",
                )
                if final is None:
                    raise RuntimeError("Job disappeared after completion")
                return final
            except Exception as exc:
                logger.exception("Pipeline job %s failed", job_id)
                (artifact_dir / "error.txt").write_text(
                    f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}",
                    encoding="utf-8",
                )
                archive_path = self._archive(artifact_dir)
                failed = self.storage.update_job(
                    job_id,
                    status="failed",
                    stage="failed",
                    progress=100,
                    error=f"{type(exc).__name__}: {exc}",
                    archive_path=str(archive_path),
                )
                if failed is None:
                    raise
                return failed
            finally:
                self._job_locks.pop(job_id, None)

    def _update(self, job_id: str, status: str, stage: str, progress: int) -> None:
        self.storage.update_job(
            job_id,
            status=status,
            stage=stage,
            progress=progress,
            error="",
        )

    @staticmethod
    def _archive(artifact_dir: Path) -> Path:
        archive_base = artifact_dir.parent / f"{artifact_dir.name}-artifacts"
        archive_file = Path(shutil.make_archive(str(archive_base), "zip", artifact_dir))
        return archive_file
