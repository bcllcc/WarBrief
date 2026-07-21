from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from warbrief.api import create_app
from warbrief.config import Settings


def test_health_refresh_and_events(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "runtime",
        demo_mode=True,
        news_scheduler_enabled=False,
        news_refresh_on_startup=False,
        dvids_enabled=False,
        wikimedia_enabled=False,
        video_width=270,
        video_height=480,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        refreshed = client.post("/api/news/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["articles_collected"] >= 1

        events = client.get("/api/events")
        assert events.status_code == 200
        assert events.json()

        event_id = events.json()[0]["id"]
        detail = client.get(f"/api/events/{event_id}")
        assert detail.status_code == 200
        assert detail.json()["articles"]
