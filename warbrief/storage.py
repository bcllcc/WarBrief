from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from warbrief.models import JobRecord, NewsArticle, NewsEvent


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


class Storage:
    """Small SQLite repository used by the demo.

    SQLite is intentional for V0.1: the public contracts stay stable while the
    implementation can later move to PostgreSQL without changing the pipeline.
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    language TEXT NOT NULL DEFAULT '',
                    source_country TEXT NOT NULL DEFAULT '',
                    image_url TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    inserted_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_articles_published
                    ON articles(published_at DESC);

                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    published_at TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0,
                    article_ids_json TEXT NOT NULL,
                    source_names_json TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_score
                    ON events(score DESC, published_at DESC);

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    artifact_dir TEXT NOT NULL DEFAULT '',
                    video_path TEXT NOT NULL DEFAULT '',
                    archive_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_created
                    ON jobs(created_at DESC);
                """
            )

    def upsert_articles(self, articles: Iterable[NewsArticle]) -> int:
        rows = list(articles)
        with self._lock, self._conn:
            self._conn.executemany(
                """
                INSERT INTO articles (
                    id, provider, source_name, title, summary, content, url,
                    published_at, language, source_country, image_url, raw_json,
                    inserted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    provider=excluded.provider,
                    source_name=excluded.source_name,
                    title=excluded.title,
                    summary=excluded.summary,
                    content=CASE
                        WHEN excluded.content != '' THEN excluded.content
                        ELSE articles.content
                    END,
                    url=excluded.url,
                    published_at=excluded.published_at,
                    language=excluded.language,
                    source_country=excluded.source_country,
                    image_url=excluded.image_url,
                    raw_json=excluded.raw_json
                """,
                [
                    (
                        item.id,
                        item.provider,
                        item.source_name,
                        item.title,
                        item.summary,
                        item.content,
                        item.url,
                        _iso_utc(item.published_at),
                        item.language,
                        item.source_country,
                        item.image_url,
                        _json(item.raw),
                        _now(),
                    )
                    for item in rows
                ],
            )
        return len(rows)

    def update_article_content(self, article_id: str, content: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE articles SET content=? WHERE id=?",
                (content, article_id),
            )

    def list_articles(self, limit: int = 200) -> list[NewsArticle]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM articles ORDER BY published_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._article_from_row(row) for row in rows]

    def get_articles(self, article_ids: list[str]) -> list[NewsArticle]:
        if not article_ids:
            return []
        placeholders = ",".join("?" for _ in article_ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM articles WHERE id IN ({placeholders})", article_ids
            ).fetchall()
        order = {item_id: index for index, item_id in enumerate(article_ids)}
        articles = [self._article_from_row(row) for row in rows]
        return sorted(articles, key=lambda item: order.get(item.id, 9999))

    def get_article(self, article_id: str) -> NewsArticle | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
        return self._article_from_row(row) if row else None

    @staticmethod
    def _article_from_row(row: sqlite3.Row) -> NewsArticle:
        return NewsArticle(
            id=row["id"],
            provider=row["provider"],
            source_name=row["source_name"],
            title=row["title"],
            summary=row["summary"],
            content=row["content"],
            url=row["url"],
            published_at=row["published_at"],
            language=row["language"],
            source_country=row["source_country"],
            image_url=row["image_url"],
            raw=json.loads(row["raw_json"] or "{}"),
        )

    def upsert_events(self, events: Iterable[NewsEvent]) -> int:
        rows = list(events)
        with self._lock, self._conn:
            self._conn.executemany(
                """
                INSERT INTO events (
                    id, title, summary, published_at, score,
                    article_ids_json, source_names_json, keywords_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    summary=excluded.summary,
                    published_at=excluded.published_at,
                    score=excluded.score,
                    article_ids_json=excluded.article_ids_json,
                    source_names_json=excluded.source_names_json,
                    keywords_json=excluded.keywords_json,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        event.id,
                        event.title,
                        event.summary,
                        _iso_utc(event.published_at),
                        event.score,
                        _json(event.article_ids),
                        _json(event.source_names),
                        _json(event.keywords),
                        _iso_utc(event.created_at),
                        _now(),
                    )
                    for event in rows
                ],
            )
        return len(rows)

    def list_events(self, limit: int = 50, since: datetime | None = None) -> list[NewsEvent]:
        with self._lock:
            if since is None:
                rows = self._conn.execute(
                    "SELECT * FROM events ORDER BY score DESC, published_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT * FROM events
                    WHERE published_at >= ?
                    ORDER BY score DESC, published_at DESC
                    LIMIT ?
                    """,
                    (_iso_utc(since), limit),
                ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def get_event(self, event_id: str) -> NewsEvent | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return self._event_from_row(row) if row else None

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> NewsEvent:
        return NewsEvent(
            id=row["id"],
            title=row["title"],
            summary=row["summary"],
            published_at=row["published_at"],
            score=row["score"],
            article_ids=json.loads(row["article_ids_json"] or "[]"),
            source_names=json.loads(row["source_names_json"] or "[]"),
            keywords=json.loads(row["keywords_json"] or "[]"),
            created_at=row["created_at"],
        )

    def prune_news(self, before: datetime) -> tuple[int, int]:
        """Delete stale discovery data while preserving events referenced by jobs."""
        cutoff = _iso_utc(before)
        with self._lock, self._conn:
            event_cursor = self._conn.execute(
                """
                DELETE FROM events
                WHERE published_at < ?
                  AND id NOT IN (SELECT DISTINCT event_id FROM jobs)
                """,
                (cutoff,),
            )
            remaining_rows = self._conn.execute("SELECT article_ids_json FROM events").fetchall()
            referenced_article_ids: set[str] = set()
            for row in remaining_rows:
                try:
                    referenced_article_ids.update(
                        str(value) for value in json.loads(row["article_ids_json"] or "[]")
                    )
                except (TypeError, json.JSONDecodeError):
                    continue
            stale_rows = self._conn.execute(
                "SELECT id FROM articles WHERE published_at < ?", (cutoff,)
            ).fetchall()
            stale_ids = [row["id"] for row in stale_rows if row["id"] not in referenced_article_ids]
            if stale_ids:
                self._conn.executemany(
                    "DELETE FROM articles WHERE id=?",
                    [(article_id,) for article_id in stale_ids],
                )
            return max(event_cursor.rowcount, 0), len(stale_ids)

    def create_job(self, job: JobRecord) -> JobRecord:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO jobs (
                    id, event_id, status, stage, progress, error, artifact_dir,
                    video_path, archive_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.event_id,
                    job.status,
                    job.stage,
                    job.progress,
                    job.error,
                    job.artifact_dir,
                    job.video_path,
                    job.archive_path,
                    _iso_utc(job.created_at),
                    _iso_utc(job.updated_at),
                ),
            )
        return job

    def update_job(self, job_id: str, **fields: object) -> JobRecord | None:
        allowed = {
            "status",
            "stage",
            "progress",
            "error",
            "artifact_dir",
            "video_path",
            "archive_path",
        }
        payload = {key: value for key, value in fields.items() if key in allowed}
        payload["updated_at"] = _now()
        assignments = ", ".join(f"{key}=?" for key in payload)
        values = list(payload.values()) + [job_id]
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE jobs SET {assignments} WHERE id=?", values)
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job_from_row(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[JobRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            event_id=row["event_id"],
            status=row["status"],
            stage=row["stage"],
            progress=row["progress"],
            error=row["error"],
            artifact_dir=row["artifact_dir"],
            video_path=row["video_path"],
            archive_path=row["archive_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
