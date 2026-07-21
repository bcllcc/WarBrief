from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from warbrief import __version__
from warbrief.bootstrap import AppContext, build_context
from warbrief.config import Settings
from warbrief.models import NewsArticle, NewsEvent, PipelineRequest
from warbrief.utils import stable_id

logger = logging.getLogger(__name__)


def _track(context: AppContext, task: asyncio.Task) -> None:
    context.tasks.add(task)
    task.add_done_callback(context.tasks.discard)


async def _news_scheduler(context: AppContext) -> None:
    interval = max(context.settings.news_poll_interval_minutes * 60, 60)
    while True:
        await asyncio.sleep(interval)
        try:
            await context.news.refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled news refresh failed")


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        context = build_context(settings)
        app.state.context = context
        scheduler_task: asyncio.Task | None = None
        if context.settings.news_refresh_on_startup:
            startup_task = asyncio.create_task(context.news.refresh(), name="news-refresh-startup")
            _track(context, startup_task)
        if context.settings.news_scheduler_enabled:
            scheduler_task = asyncio.create_task(_news_scheduler(context), name="news-scheduler")
            _track(context, scheduler_task)
        yield
        if scheduler_task:
            scheduler_task.cancel()
        for task in list(context.tasks):
            task.cancel()
        for task in list(context.tasks):
            with suppress(asyncio.CancelledError, Exception):
                await task
        context.close()

    app = FastAPI(
        title="WarBrief API",
        version=__version__,
        description="Military news-to-short-video factory demo",
        lifespan=lifespan,
    )
    static_dir = Path(__file__).resolve().parent / "web"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def context() -> AppContext:
        return app.state.context

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    async def health() -> dict:
        ctx = context()
        return {
            "status": "ok",
            "version": __version__,
            "time": datetime.now(UTC).isoformat(),
            "config": ctx.settings.public_summary(),
        }

    @app.get("/api/config")
    async def public_config() -> dict:
        return context().settings.public_summary()

    @app.post("/api/news/refresh")
    async def refresh_news() -> dict:
        result = await context().news.refresh()
        return result.model_dump(mode="json")

    @app.get("/api/events")
    async def list_events(limit: int = Query(30, ge=1, le=100)) -> list[dict]:
        ctx = context()
        recent_hours = max(ctx.settings.news_lookback_hours * 3, 72)
        since = datetime.now(UTC) - timedelta(hours=recent_hours)
        return [
            event.model_dump(mode="json")
            for event in ctx.storage.list_events(limit=limit, since=since)
        ]

    @app.get("/api/events/{event_id}")
    async def get_event(event_id: str) -> dict:
        ctx = context()
        event = ctx.storage.get_event(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        return {
            "event": event.model_dump(mode="json"),
            "articles": [
                article.model_dump(mode="json")
                for article in ctx.storage.get_articles(event.article_ids)
            ],
        }

    @app.post("/api/jobs")
    async def create_job(payload: PipelineRequest) -> dict:
        ctx = context()
        event_id = payload.event_id
        if not event_id:
            if not payload.manual_title:
                raise HTTPException(
                    status_code=422,
                    detail="event_id or manual_title is required",
                )
            article_url = (
                payload.manual_source_url or f"manual://{stable_id('source', payload.manual_title)}"
            )
            article = NewsArticle(
                id=stable_id("article", article_url),
                provider="manual",
                source_name="Manual input",
                title=payload.manual_title,
                summary=payload.manual_summary or payload.manual_title,
                content=payload.manual_summary or payload.manual_title,
                url=article_url,
                published_at=datetime.now(UTC),
            )
            ctx.storage.upsert_articles([article])
            event = NewsEvent(
                id=stable_id("event", article.id),
                title=article.title,
                summary=article.summary,
                published_at=article.published_at,
                score=100,
                article_ids=[article.id],
                source_names=[article.source_name],
                keywords=[],
            )
            ctx.storage.upsert_events([event])
            event_id = event.id
        if not ctx.storage.get_event(event_id):
            raise HTTPException(status_code=404, detail="Event not found")
        job = ctx.pipeline.create_job(event_id)
        task = asyncio.create_task(ctx.pipeline.run_job(job.id), name=f"pipeline-{job.id}")
        _track(ctx, task)
        return job.model_dump(mode="json")

    @app.post("/api/jobs/{job_id}/retry")
    async def retry_job(job_id: str) -> dict:
        ctx = context()
        previous = ctx.storage.get_job(job_id)
        if not previous:
            raise HTTPException(status_code=404, detail="Job not found")
        job = ctx.pipeline.create_job(previous.event_id)
        task = asyncio.create_task(ctx.pipeline.run_job(job.id), name=f"pipeline-{job.id}")
        _track(ctx, task)
        return job.model_dump(mode="json")

    @app.get("/api/jobs")
    async def list_jobs(limit: int = Query(30, ge=1, le=100)) -> list[dict]:
        return [job.model_dump(mode="json") for job in context().storage.list_jobs(limit=limit)]

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict:
        job = context().storage.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        payload = job.model_dump(mode="json")
        if job.video_path and Path(job.video_path).exists():
            payload["video_url"] = f"/api/jobs/{job.id}/video"
        if job.archive_path and Path(job.archive_path).exists():
            payload["download_url"] = f"/api/jobs/{job.id}/download"
        return payload

    @app.get("/api/jobs/{job_id}/video")
    async def get_video(job_id: str) -> FileResponse:
        job = context().storage.get_job(job_id)
        if not job or not job.video_path or not Path(job.video_path).exists():
            raise HTTPException(status_code=404, detail="Video not found")
        return FileResponse(job.video_path, media_type="video/mp4", filename=f"{job.id}.mp4")

    @app.get("/api/jobs/{job_id}/download")
    async def download_artifacts(job_id: str) -> FileResponse:
        job = context().storage.get_job(job_id)
        if not job or not job.archive_path or not Path(job.archive_path).exists():
            raise HTTPException(status_code=404, detail="Artifact archive not found")
        return FileResponse(
            job.archive_path,
            media_type="application/zip",
            filename=f"{job.id}-artifacts.zip",
        )

    @app.get("/api/jobs/{job_id}/artifacts/{artifact_path:path}")
    async def get_artifact(job_id: str, artifact_path: str) -> FileResponse:
        job = context().storage.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        root = Path(job.artifact_dir).resolve()
        candidate = (root / artifact_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid artifact path") from exc
        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found")
        return FileResponse(candidate)

    return app


app = create_app()
