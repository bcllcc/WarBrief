from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import uvicorn

from warbrief.bootstrap import build_context
from warbrief.config import Settings


def _logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def _refresh(settings: Settings) -> int:
    context = build_context(settings)
    try:
        result = await context.news.refresh()
        print(result.model_dump_json(indent=2))
        return 0
    finally:
        context.close()


async def _demo(settings: Settings, title: str | None) -> int:
    context = build_context(settings)
    try:
        result = await context.news.refresh()
        events = context.storage.list_events(limit=10)
        if not events:
            print("No events were collected", file=sys.stderr)
            return 1
        event = events[0]
        if title:
            from datetime import UTC, datetime

            from warbrief.models import NewsArticle, NewsEvent
            from warbrief.utils import stable_id

            article = NewsArticle(
                id=stable_id("article", title),
                provider="manual",
                source_name="CLI demo",
                title=title,
                summary=title,
                content=title,
                url=f"manual://{stable_id('source', title)}",
                published_at=datetime.now(UTC),
            )
            context.storage.upsert_articles([article])
            event = NewsEvent(
                id=stable_id("event", article.id),
                title=title,
                summary=title,
                published_at=article.published_at,
                score=100,
                article_ids=[article.id],
                source_names=[article.source_name],
            )
            context.storage.upsert_events([event])
        print(f"Collected {result.articles_collected} articles; generating: {event.title}")
        job = context.pipeline.create_job(event.id)
        final = await context.pipeline.run_job(job.id)
        print(final.model_dump_json(indent=2))
        if final.video_path:
            print(f"\nVideo: {Path(final.video_path).resolve()}")
        if final.archive_path:
            print(f"Artifacts: {Path(final.archive_path).resolve()}")
        return 0 if final.status == "completed" else 1
    finally:
        context.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="warbrief", description="WarBrief video factory")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the FastAPI web console")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--reload", action="store_true")

    subparsers.add_parser("refresh-news", help="Collect and cluster news once")

    demo = subparsers.add_parser("demo", help="Run the complete local demo pipeline")
    demo.add_argument("--title", default=None, help="Optional manual event title")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    _logging(args.verbose)
    settings = Settings.from_env()
    if args.command == "serve":
        uvicorn.run(
            "warbrief.api:app",
            host=args.host or settings.host,
            port=args.port or settings.port,
            reload=args.reload,
        )
        return
    if args.command == "refresh-news":
        raise SystemExit(asyncio.run(_refresh(settings)))
    if args.command == "demo":
        # `demo` 必须无条件走离线闭环，避免本地 .env 中的真实配置使演示不可复现。
        os.environ["DEMO_MODE"] = "true"
        os.environ["LLM_PROVIDER"] = "mock"
        os.environ["TTS_PROVIDER"] = "mock"
        demo_settings = Settings.from_env()
        raise SystemExit(asyncio.run(_demo(demo_settings, args.title)))


if __name__ == "__main__":
    main()
