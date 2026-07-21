from __future__ import annotations

import json
import logging
import math
import shutil
from pathlib import Path

from warbrief.config import Settings
from warbrief.models import (
    MediaAsset,
    QCCheck,
    QCReport,
    RightsEntry,
    RightsManifest,
    ScriptManifest,
    ShotRequest,
    Timeline,
    TimelineClip,
    VoiceManifest,
)
from warbrief.utils import run_command

logger = logging.getLogger(__name__)


def build_shot_requests(script: ScriptManifest, voice: VoiceManifest) -> list[ShotRequest]:
    sentence_map = {sentence.sentence_id: sentence for sentence in script.sentences}
    shots: list[ShotRequest] = []
    for segment in voice.segments:
        sentence = sentence_map[segment.sentence_id]
        shots.append(
            ShotRequest(
                sentence_id=segment.sentence_id,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms + segment.pause_after_ms,
                duration_ms=segment.duration_ms + segment.pause_after_ms,
                query=sentence.visual_query,
                visual_type=sentence.visual_type,
            )
        )
    return shots


def build_rights_manifest(event_id: str, assets: list[MediaAsset]) -> RightsManifest:
    entries = [
        RightsEntry(
            asset_id=asset.asset_id,
            provider=asset.provider,
            source_url=asset.source_url,
            page_url=asset.page_url,
            license=asset.license,
            attribution=asset.attribution,
            render_allowed=asset.render_allowed,
            rights_review_required=asset.rights_review_required,
        )
        for asset in assets
    ]
    return RightsManifest(
        event_id=event_id,
        entries=entries,
        all_render_allowed=all(entry.render_allowed for entry in entries),
    )


def _ass_time(milliseconds: int) -> str:
    total_centiseconds = max(milliseconds, 0) // 10
    hours, remainder = divmod(total_centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _srt_time(milliseconds: int) -> str:
    total_ms = max(milliseconds, 0)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def write_srt_subtitles(path: Path, voice: VoiceManifest) -> None:
    blocks: list[str] = []
    for index, segment in enumerate(voice.segments, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{_srt_time(segment.start_ms)} --> {_srt_time(segment.end_ms)}",
                    segment.text.strip(),
                ]
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _wrap_zh(text: str, width: int = 18, max_lines: int = 3) -> str:
    text = text.strip()
    if len(text) <= width:
        return text
    lines: list[str] = []
    current = ""
    punctuation = "，。！？；：,.!?;:"
    for char in text:
        current += char
        if len(current) >= width or (len(current) >= max(width - 4, 1) and char in punctuation):
            lines.append(current)
            current = ""
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    consumed = sum(len(line) for line in lines)
    if consumed < len(text) and lines:
        lines[-1] = lines[-1].rstrip("，。！？；：,.!?;:") + "…"
    return "\n".join(lines)


def write_ass_subtitles(
    path: Path,
    script: ScriptManifest,
    voice: VoiceManifest,
    assets: list[MediaAsset],
    width: int,
    height: int,
) -> None:
    font_size = max(round(height * 0.040), 28)
    title_size = max(round(height * 0.030), 24)
    source_size = max(round(height * 0.017), 14)
    title_chars = max(8, int((width - 100) / max(title_size, 1)))
    body_chars = max(8, int((width - 110) / max(font_size, 1)))
    source_margin = max(round(height * 0.15), 92)
    header = f"""[Script Info]
Title: WarBrief
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Noto Sans CJK SC,{title_size},&H00FFFFFF,&H00FFFFFF,&H00101620,&H70000000,-1,0,0,0,100,100,0,0,1,2.4,0,8,48,48,48,1
Style: Body,Noto Sans CJK SC,{font_size},&H00FFFFFF,&H00FFFFFF,&H00101010,&HA0000000,-1,0,0,0,100,100,0,0,1,3.2,0.5,2,54,54,88,1
Style: Source,Noto Sans CJK SC,{source_size},&H00D8E4EC,&H00D8E4EC,&H00101820,&H78000000,0,0,0,0,100,100,0,0,1,1.6,0,7,28,28,{source_margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    total_end = _ass_time(voice.duration_ms)
    events = [
        f"Dialogue: 0,0:00:00.00,{total_end},Title,,0,0,0,,{_ass_escape(_wrap_zh(script.title, title_chars, 2))}"
    ]
    assets_by_sentence = {asset.sentence_id: asset for asset in assets}
    for segment in voice.segments:
        start = _ass_time(segment.start_ms)
        end = _ass_time(segment.end_ms)
        body = _ass_escape(_wrap_zh(segment.text, body_chars, 3))
        events.append(f"Dialogue: 0,{start},{end},Body,,0,0,0,,{body}")
        asset = assets_by_sentence.get(segment.sentence_id)
        if asset:
            source = f"素材来源：{asset.provider}"
            events.append(f"Dialogue: 0,{start},{end},Source,,0,0,0,,{_ass_escape(source)}")
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def build_timeline(
    settings: Settings,
    script: ScriptManifest,
    voice: VoiceManifest,
    assets: list[MediaAsset],
    subtitle_path: Path,
) -> Timeline:
    asset_map = {asset.sentence_id: asset for asset in assets}
    clips: list[TimelineClip] = []
    for segment in voice.segments:
        asset = asset_map[segment.sentence_id]
        clips.append(
            TimelineClip(
                sentence_id=segment.sentence_id,
                asset_id=asset.asset_id,
                local_path=asset.local_path,
                kind=asset.kind,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms + segment.pause_after_ms,
                attribution=asset.attribution,
            )
        )
    return Timeline(
        event_id=script.event_id,
        width=settings.video_width,
        height=settings.video_height,
        fps=settings.video_fps,
        duration_ms=voice.duration_ms,
        title=script.title,
        clips=clips,
        voice_path=voice.voice_path,
        subtitle_path=str(subtitle_path),
        bgm_path=settings.bgm_path,
    )


class FFmpegRenderer:
    def __init__(self, settings: Settings):
        self.settings = settings
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError("FFmpeg and ffprobe are required")

    def render(self, timeline: Timeline, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        segment_dir = output_dir / "segments"
        segment_dir.mkdir(exist_ok=True)
        segment_paths: list[Path] = []
        for index, clip in enumerate(timeline.clips, start=1):
            segment_path = segment_dir / f"segment-{index:03d}.mp4"
            self._render_clip(clip, timeline, segment_path)
            segment_paths.append(segment_path)

        concat_file = output_dir / "video-concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{path.resolve().as_posix()}'" for path in segment_paths),
            encoding="utf-8",
        )
        visual_path = output_dir / "visual.mp4"
        run_command(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                str(visual_path),
            ],
            timeout=600,
        )
        output_path = output_dir / "video.mp4"
        self._mux(timeline, visual_path, output_path)
        return output_path

    def _render_clip(self, clip: TimelineClip, timeline: Timeline, output_path: Path) -> None:
        duration = max((clip.end_ms - clip.start_ms) / 1000, 0.25)
        width, height, fps = timeline.width, timeline.height, timeline.fps
        common = [
            "-vf",
            (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps={fps},format=yuv420p"
            ),
            "-t",
            f"{duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            self.settings.video_preset,
            "-crf",
            str(self.settings.video_crf),
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            str(output_path),
        ]
        if clip.kind == "video":
            command = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", clip.local_path, *common]
        else:
            frames = max(math.ceil(duration * fps), 1)
            zoom_filter = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                f"zoompan=z='min(zoom+0.00045,1.07)':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={frames}:s={width}x{height}:fps={fps},format=yuv420p"
            )
            command = [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                clip.local_path,
                "-vf",
                zoom_filter,
                "-frames:v",
                str(frames),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                self.settings.video_preset,
                "-crf",
                str(self.settings.video_crf),
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(fps),
                str(output_path),
            ]
        run_command(command, timeout=600)

    def _mux(self, timeline: Timeline, visual_path: Path, output_path: Path) -> None:
        subtitle_filter = f"ass={Path(timeline.subtitle_path).resolve().as_posix()}"
        command = ["ffmpeg", "-y", "-i", str(visual_path), "-i", timeline.voice_path]
        if timeline.bgm_path and Path(timeline.bgm_path).exists():
            command.extend(["-stream_loop", "-1", "-i", timeline.bgm_path])
            command.extend(
                [
                    "-filter_complex",
                    (
                        f"[0:v]{subtitle_filter}[v];"
                        "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice];"
                        "[2:a]volume=0.10[bgm];"
                        "[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2[a]"
                    ),
                    "-map",
                    "[v]",
                    "-map",
                    "[a]",
                ]
            )
        else:
            command.extend(
                [
                    "-filter_complex",
                    f"[0:v]{subtitle_filter}[v];[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]",
                    "-map",
                    "[v]",
                    "-map",
                    "[a]",
                ]
            )
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                self.settings.video_preset,
                "-crf",
                str(self.settings.video_crf),
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        run_command(command, timeout=900)

    def quality_check(
        self,
        job_id: str,
        video_path: Path,
        timeline: Timeline,
        rights: RightsManifest,
    ) -> QCReport:
        checks: list[QCCheck] = []
        if not video_path.exists() or video_path.stat().st_size < 1000:
            checks.append(
                QCCheck(name="video_exists", passed=False, detail="Video missing or empty")
            )
            return QCReport(
                job_id=job_id,
                passed=False,
                video_path=str(video_path),
                checks=checks,
            )
        completed = run_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(video_path),
            ]
        )
        payload = json.loads(completed.stdout)
        streams = payload.get("streams", [])
        video_stream = next(
            (stream for stream in streams if stream.get("codec_type") == "video"), {}
        )
        audio_stream = next(
            (stream for stream in streams if stream.get("codec_type") == "audio"), {}
        )
        width = int(video_stream.get("width", 0) or 0)
        height = int(video_stream.get("height", 0) or 0)
        duration_ms = round(float(payload.get("format", {}).get("duration", 0) or 0) * 1000)
        checks.extend(
            [
                QCCheck(
                    name="resolution",
                    passed=(width, height) == (timeline.width, timeline.height),
                    detail=f"{width}x{height}; expected {timeline.width}x{timeline.height}",
                ),
                QCCheck(
                    name="audio_stream",
                    passed=bool(audio_stream),
                    detail="Audio present" if audio_stream else "Audio missing",
                ),
                QCCheck(
                    name="duration",
                    passed=abs(duration_ms - timeline.duration_ms) <= 1300,
                    detail=f"video={duration_ms}ms timeline={timeline.duration_ms}ms",
                ),
                QCCheck(
                    name="rights_gate",
                    passed=rights.all_render_allowed,
                    detail=(
                        f"{len(rights.entries)} assets checked; "
                        f"{sum(entry.rights_review_required for entry in rights.entries)} require manual review"
                    ),
                ),
                QCCheck(
                    name="subtitle_file",
                    passed=Path(timeline.subtitle_path).exists(),
                    detail=timeline.subtitle_path,
                ),
            ]
        )
        passed = all(check.passed for check in checks)
        return QCReport(
            job_id=job_id,
            passed=passed,
            video_path=str(video_path),
            duration_ms=duration_ms,
            width=width,
            height=height,
            has_audio=bool(audio_stream),
            checks=checks,
        )
