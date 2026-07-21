from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class NewsArticle(Model):
    id: str
    provider: str
    source_name: str
    title: str
    summary: str = ""
    content: str = ""
    url: str
    published_at: datetime = Field(default_factory=utcnow)
    language: str = ""
    source_country: str = ""
    image_url: str = ""
    raw: dict = Field(default_factory=dict)


class NewsEvent(Model):
    id: str
    title: str
    summary: str
    published_at: datetime
    score: float = 0.0
    article_ids: list[str] = Field(default_factory=list)
    source_names: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class SourceEvidence(Model):
    source_id: str
    article_id: str
    title: str
    url: str
    published_at: datetime
    quote: str


class Claim(Model):
    claim_id: str
    statement: str
    evidence_source_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.7
    disputed: bool = False


class FactPack(Model):
    event_id: str
    event_title: str
    sources: list[SourceEvidence]
    claims: list[Claim]
    generated_at: datetime = Field(default_factory=utcnow)
    model: str = "mock"


class ScriptSentence(Model):
    sentence_id: str
    text: str
    claim_ids: list[str] = Field(default_factory=list)
    visual_query: str
    visual_type: Literal["event", "archive", "map", "document", "generic"] = "generic"


class ScriptManifest(Model):
    event_id: str
    title: str
    hook: str = ""
    sentences: list[ScriptSentence]
    closing: str = ""
    model: str = "mock"
    generated_at: datetime = Field(default_factory=utcnow)


class VoiceSegment(Model):
    sentence_id: str
    text: str
    audio_path: str
    start_ms: int
    end_ms: int
    duration_ms: int
    pause_after_ms: int = 220


class VoiceManifest(Model):
    voice_path: str
    provider: str
    voice: str
    duration_ms: int
    segments: list[VoiceSegment]


class ShotRequest(Model):
    sentence_id: str
    start_ms: int
    end_ms: int
    duration_ms: int
    query: str
    visual_type: str = "generic"


class MediaAsset(Model):
    asset_id: str
    sentence_id: str
    provider: str
    kind: Literal["image", "video"] = "image"
    local_path: str
    source_url: str = ""
    page_url: str = ""
    title: str = ""
    attribution: str = ""
    license: str = ""
    render_allowed: bool = True
    rights_review_required: bool = False
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None


class TimelineClip(Model):
    sentence_id: str
    asset_id: str
    local_path: str
    kind: Literal["image", "video"]
    start_ms: int
    end_ms: int
    source_in_ms: int = 0
    attribution: str = ""


class Timeline(Model):
    event_id: str
    width: int
    height: int
    fps: int
    duration_ms: int
    title: str
    clips: list[TimelineClip]
    voice_path: str
    subtitle_path: str
    bgm_path: str = ""


class RightsEntry(Model):
    asset_id: str
    provider: str
    source_url: str
    page_url: str
    license: str
    attribution: str
    render_allowed: bool
    rights_review_required: bool


class RightsManifest(Model):
    event_id: str
    entries: list[RightsEntry]
    all_render_allowed: bool


class QCCheck(Model):
    name: str
    passed: bool
    detail: str = ""


class QCReport(Model):
    job_id: str
    passed: bool
    video_path: str
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    has_audio: bool = False
    checks: list[QCCheck] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utcnow)


JobStatus = Literal["queued", "running", "completed", "failed"]


class JobRecord(Model):
    id: str
    event_id: str
    status: JobStatus = "queued"
    stage: str = "queued"
    progress: int = 0
    error: str = ""
    artifact_dir: str = ""
    video_path: str = ""
    archive_path: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PipelineRequest(Model):
    event_id: str | None = None
    manual_title: str | None = None
    manual_summary: str | None = None
    manual_source_url: str | None = None


class RefreshResult(Model):
    articles_collected: int
    events_created: int
    providers_ok: list[str] = Field(default_factory=list)
    providers_failed: dict[str, str] = Field(default_factory=dict)
    refreshed_at: datetime = Field(default_factory=utcnow)
