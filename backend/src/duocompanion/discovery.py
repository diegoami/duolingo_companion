"""Helpers for milestone 1: summarize discovery captures and export them as test fixtures."""

import json
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Path segments that look like ids: numbers, hex, UUIDs, long tokens containing a digit.
_ID_SEGMENT = re.compile(
    r"^(\d+|[0-9a-f]{16,}|[0-9a-f-]{32,36}|(?=[A-Za-z_-]*\d)[A-Za-z0-9_-]{20,})$", re.IGNORECASE
)
_JWT = re.compile(r"eyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}")
_SENSITIVE_KEY = re.compile(
    r"^(email|phone|phoneNumber|password|jwt|token|accessToken|refreshToken|authToken|"
    r"authorization|cookie|.*secret.*)$",
    re.IGNORECASE,
)
REDACTED = "[REDACTED]"


def normalize_path(url: str) -> tuple[str, str]:
    """Return (host, path) with id-like segments replaced by {id}."""
    parts = urlsplit(url)
    segments = ["{id}" if _ID_SEGMENT.match(s) else s for s in parts.path.split("/")]
    return parts.netloc, "/".join(segments)


def try_json(text: Any) -> Any:
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def scrub(value: Any) -> Any:
    """Remove secrets and contact details so a capture can be committed as a fixture."""
    if isinstance(value, dict):
        return {
            k: REDACTED if _SENSITIVE_KEY.match(k) and isinstance(v, str) and v else scrub(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return _JWT.sub(REDACTED, value)
    return value


@dataclass
class Capture:
    row_id: int
    envelope: dict
    data: dict

    @property
    def via(self) -> str:
        return str(self.data.get("via", "?"))

    @property
    def method(self) -> str:
        return str(self.data.get("method", "DOM" if self.via == "dom" else "?"))

    @property
    def url(self) -> str:
        return str(self.data.get("url", ""))


def iter_captures(conn: sqlite3.Connection) -> Iterator[Capture]:
    for row in conn.execute("SELECT id, body FROM raw_payloads WHERE kind = 'discovery' ORDER BY id"):
        envelope = try_json(row["body"])
        if not isinstance(envelope, dict):
            continue
        data = envelope.get("data")
        yield Capture(row["id"], envelope, data if isinstance(data, dict) else {})


@dataclass
class EndpointSummary:
    method: str
    host: str
    path: str
    count: int = 0
    statuses: set = field(default_factory=set)
    max_response_bytes: int = 0
    response_keys: list[str] = field(default_factory=list)
    request_keys: list[str] = field(default_factory=list)
    row_ids: list[int] = field(default_factory=list)


def summarize(captures: Iterator[Capture]) -> list[EndpointSummary]:
    groups: dict[tuple[str, str, str], EndpointSummary] = {}
    for cap in captures:
        host, path = normalize_path(cap.url)
        key = (cap.method, host, path)
        summary = groups.setdefault(key, EndpointSummary(*key))
        summary.count += 1
        summary.row_ids.append(cap.row_id)
        if "status" in cap.data:
            summary.statuses.add(cap.data["status"])
        body = cap.data.get("response_body") or cap.data.get("html") or ""
        summary.max_response_bytes = max(summary.max_response_bytes, len(body))
        if not summary.response_keys and isinstance(resp := try_json(body), dict):
            summary.response_keys = sorted(resp)
        if not summary.request_keys and isinstance(req := try_json(cap.data.get("request_body")), dict):
            summary.request_keys = sorted(req)
    return sorted(groups.values(), key=lambda s: (s.host, s.path, s.method))


def format_summary(summaries: list[EndpointSummary]) -> str:
    lines = []
    for s in summaries:
        statuses = ",".join(str(x) for x in sorted(s.statuses, key=str)) or "-"
        lines.append(
            f"{s.count:4d}x {s.method:6} {s.host}{s.path}  status={statuses} "
            f"max={s.max_response_bytes:,}B rows={s.row_ids[0]}..{s.row_ids[-1]}"
        )
        if s.request_keys:
            lines.append(f"        request keys:  {', '.join(s.request_keys)}")
        if s.response_keys:
            lines.append(f"        response keys: {', '.join(s.response_keys)}")
    return "\n".join(lines) if lines else "no discovery captures yet"


def _slug(method: str, url: str) -> str:
    _, path = normalize_path(url)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", path.replace("{id}", "id")).strip("_")
    return f"{method}_{slug or 'root'}"[:80]


def export_fixtures(conn: sqlite3.Connection, out_dir: Path, match: str | None = None) -> list[Path]:
    """Write each discovery capture as a scrubbed, pretty-printed JSON file."""
    pattern = re.compile(match) if match else None
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for cap in iter_captures(conn):
        if pattern and not pattern.search(cap.url):
            continue
        fixture = dict(cap.envelope)
        data = dict(cap.data)
        # Parsed copies make fixtures readable; the original strings stay alongside.
        for text_key, json_key in (("request_body", "request_json"), ("response_body", "response_json")):
            parsed = try_json(data.get(text_key))
            if parsed is not None:
                data[json_key] = parsed
                del data[text_key]
        fixture["data"] = data
        fixture.pop("user_agent", None)
        path = out_dir / f"{cap.row_id:05d}_{_slug(cap.method, cap.url)}.json"
        path.write_text(json.dumps(scrub(fixture), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written
