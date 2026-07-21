from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def stable_id(prefix: str, value: str, length: int = 16) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        host = parts.netloc.lower()
        path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower() or "https", host, path, "", ""))
    except Exception:
        return url.strip()


def redact_url_secrets(url: str, secret_keys: set[str] | None = None) -> str:
    """Remove credential-like query parameters before persisting a URL."""
    keys = {key.lower() for key in (secret_keys or {"api_key", "key", "token", "access_token"})}
    try:
        parts = urlsplit(url)
        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in keys
        ]
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )
    except Exception:
        return url


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = value.replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def title_tokens(text: str) -> set[str]:
    text = clean_text(text).lower()
    latin = set(re.findall(r"[a-z0-9][a-z0-9\-]{2,}", text))
    cjk_chunks = re.findall(r"[\u4e00-\u9fff]+", text)
    cjk: set[str] = set()
    for chunk in cjk_chunks:
        if len(chunk) <= 2:
            cjk.add(chunk)
        else:
            cjk.update(chunk[i : i + 2] for i in range(len(chunk) - 1))
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "new",
        "latest",
        "news",
    }
    return {token for token in latin | cjk if token not in stop}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    raw = value.strip()
    variants = [raw, raw.replace("Z", "+00:00")]
    for candidate in variants:
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        return datetime.now(UTC)


def run_command(command: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def safe_filename(value: str, fallback: str = "asset") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.")
    return cleaned[:100] or fallback


def normalize_base_url(base_url: str, suffix: str) -> str:
    base = base_url.rstrip("/")
    suffix = suffix.lstrip("/")
    if base.endswith(suffix):
        return base
    return f"{base}/{suffix}"
