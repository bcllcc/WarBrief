from __future__ import annotations

import asyncio
import logging
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from warbrief.config import Settings
from warbrief.models import ScriptManifest, VoiceManifest, VoiceSegment
from warbrief.utils import normalize_base_url, run_command

logger = logging.getLogger(__name__)


class TTSProvider(ABC):
    name: str
    voice_name: str

    def __init__(self, settings: Settings):
        self.settings = settings
        self.voice_name = settings.tts_voice

    @abstractmethod
    async def synthesize(self, text: str, output_wav: Path) -> None:
        raise NotImplementedError

    def _normalize(self, source: Path, output_wav: Path) -> None:
        run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-ar",
                "24000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(output_wav),
            ],
            timeout=self.settings.tts_timeout_seconds,
        )

    def _silence(self, output_wav: Path, duration_seconds: float) -> None:
        run_command(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-t",
                f"{duration_seconds:.3f}",
                "-c:a",
                "pcm_s16le",
                str(output_wav),
            ]
        )


class MockTTSProvider(TTSProvider):
    name = "mock-espeak"

    async def synthesize(self, text: str, output_wav: Path) -> None:
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        raw_path = output_wav.with_suffix(".raw.wav")
        if espeak:
            try:
                process = await asyncio.create_subprocess_exec(
                    espeak,
                    "-v",
                    "zh",
                    "-s",
                    "165",
                    "-w",
                    str(raw_path),
                    text,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=90)
                if process.returncode == 0 and raw_path.exists() and raw_path.stat().st_size > 100:
                    self._normalize(raw_path, output_wav)
                    raw_path.unlink(missing_ok=True)
                    return
                logger.warning("espeak failed: %s", stderr.decode(errors="ignore"))
            except Exception as exc:
                logger.warning("espeak unavailable, using silence: %s", exc)
        estimated = max(
            self.settings.video_min_sentence_seconds,
            min(len(text) / 5.2, self.settings.video_max_sentence_seconds),
        )
        self._silence(output_wav, estimated)


class EdgeTTSProvider(TTSProvider):
    name = "edge-tts"

    async def synthesize(self, text: str, output_wav: Path) -> None:
        try:
            import edge_tts
        except ImportError as exc:
            raise RuntimeError("edge-tts is not installed; run `pip install -e .`") from exc
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        mp3_path = output_wav.with_suffix(".mp3")
        communicator = edge_tts.Communicate(
            text=text,
            voice=self.settings.tts_voice,
            rate=self.settings.tts_rate,
        )
        await communicator.save(str(mp3_path))
        self._normalize(mp3_path, output_wav)
        mp3_path.unlink(missing_ok=True)


class OpenAICompatibleTTSProvider(TTSProvider):
    name = "openai-compatible-tts"

    async def synthesize(self, text: str, output_wav: Path) -> None:
        endpoint = normalize_base_url(self.settings.tts_base_url, "audio/speech")
        headers = {"Content-Type": "application/json"}
        if self.settings.tts_api_key:
            headers["Authorization"] = f"Bearer {self.settings.tts_api_key}"
        body = {
            "model": self.settings.tts_model,
            "input": text,
            "voice": self.settings.tts_voice,
            "response_format": "mp3",
        }
        async with httpx.AsyncClient(
            timeout=self.settings.tts_timeout_seconds,
            proxy=self.settings.http_proxy_url or None,
        ) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
        mp3_path = output_wav.with_suffix(".mp3")
        mp3_path.write_bytes(response.content)
        self._normalize(mp3_path, output_wav)
        mp3_path.unlink(missing_ok=True)


def probe_duration_ms(path: Path) -> int:
    completed = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    try:
        return max(1, round(float(completed.stdout.strip()) * 1000))
    except ValueError as exc:
        raise RuntimeError(f"Cannot read audio duration for {path}") from exc


class VoiceService:
    def __init__(self, settings: Settings, provider: TTSProvider):
        self.settings = settings
        self.provider = provider

    async def generate(self, script: ScriptManifest, output_dir: Path) -> VoiceManifest:
        output_dir.mkdir(parents=True, exist_ok=True)
        normalized_files: list[Path] = []
        raw_segments: list[tuple[str, str, Path, int]] = []
        for sentence in script.sentences:
            path = output_dir / f"{sentence.sentence_id}.wav"
            try:
                await self.provider.synthesize(sentence.text, path)
            except Exception as exc:
                logger.exception(
                    "TTS failed for %s: %s; using mock fallback", sentence.sentence_id, exc
                )
                fallback = MockTTSProvider(self.settings)
                await fallback.synthesize(sentence.text, path)
            duration_ms = probe_duration_ms(path)
            normalized_files.append(path)
            raw_segments.append((sentence.sentence_id, sentence.text, path, duration_ms))

        pause_ms = 220
        pause_path = output_dir / "pause.wav"
        run_command(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-t",
                f"{pause_ms / 1000:.3f}",
                "-c:a",
                "pcm_s16le",
                str(pause_path),
            ]
        )
        concat_paths: list[Path] = []
        for index, path in enumerate(normalized_files):
            concat_paths.append(path)
            if index < len(normalized_files) - 1:
                concat_paths.append(pause_path)
        concat_file = output_dir / "concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{path.resolve().as_posix()}'" for path in concat_paths),
            encoding="utf-8",
        )
        voice_path = output_dir / "voice.wav"
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
                "-ar",
                "24000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(voice_path),
            ]
        )

        segments: list[VoiceSegment] = []
        cursor_ms = 0
        for index, (sentence_id, text, path, duration_ms) in enumerate(raw_segments):
            start_ms = cursor_ms
            end_ms = start_ms + duration_ms
            actual_pause = pause_ms if index < len(raw_segments) - 1 else 0
            segments.append(
                VoiceSegment(
                    sentence_id=sentence_id,
                    text=text,
                    audio_path=str(path),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    duration_ms=duration_ms,
                    pause_after_ms=actual_pause,
                )
            )
            cursor_ms = end_ms + actual_pause
        final_duration = probe_duration_ms(voice_path)
        return VoiceManifest(
            voice_path=str(voice_path),
            provider=self.provider.name,
            voice=self.provider.voice_name,
            duration_ms=final_duration,
            segments=segments,
        )


def create_tts_provider(settings: Settings) -> TTSProvider:
    provider = settings.tts_provider.lower()
    if provider == "edge":
        return EdgeTTSProvider(settings)
    if provider in {"openai", "openai_compatible", "openai-compatible"}:
        if settings.tts_base_url:
            return OpenAICompatibleTTSProvider(settings)
        logger.warning("TTS_BASE_URL missing; falling back to mock TTS")
    return MockTTSProvider(settings)
