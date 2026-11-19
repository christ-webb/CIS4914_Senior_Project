from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from nomore500s_collector.creation_evidence import stamp

HOOK_OK = b"{}\n"
MAX_HOOK_LOG_BYTES = 256 * 1024


def collector_url() -> str:
    return os.environ.get("NM5_COLLECTOR_URL", "http://127.0.0.1:8788").rstrip("/")


def collector_token() -> str:
    env = os.environ.get("NM5_COLLECTOR_TOKEN")
    if env:
        return env
    home = os.environ.get("NM5_COLLECTOR_HOME")
    path = (
        os.path.join(home, "token")
        if home
        else os.path.expanduser("~/.nomore500s/collector/token")
    )
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def hook_debug_enabled() -> bool:
    return os.environ.get("NM5_HOOK_DEBUG", "").strip().lower() in {"1", "true", "yes"}


def hook_log_path() -> Path:
    home = os.environ.get("NM5_COLLECTOR_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".nomore500s" / "collector"
    return base / "hook.log"


def append_hook_debug(message: str) -> None:
    if not hook_debug_enabled():
        return
    path = hook_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (message.rstrip() + "\n").encode("utf-8")
        existing = path.read_bytes() if path.exists() else b""
        data = existing + payload
        if len(data) > MAX_HOOK_LOG_BYTES:
            data = data[-MAX_HOOK_LOG_BYTES // 2 :]
        path.write_bytes(data)
    except OSError:
        return


def _parse_collector_result(body: bytes) -> tuple[str | None, str | None]:
    try:
        parsed = json.loads(body.decode("utf-8") or "null")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    status = parsed.get("status")
    reason = parsed.get("reason")
    return (str(status) if status else None, str(reason) if reason else None)


def _read_http_error_body(error: HTTPError) -> bytes:
    try:
        return error.read()
    except OSError:
        return b""


def _sanitized_delivery(
    payload: dict[str, Any],
    *,
    source: str | None,
    status: str,
    http_status: int | None = None,
    result: str | None = None,
    reason: str | None = None,
) -> str:
    event = str(payload.get("hook_event_name") or payload.get("hookEventName") or "unknown")
    has_session = bool(
        payload.get("session_id")
        or payload.get("sessionId")
        or payload.get("conversation_id")
        or payload.get("conversationId")
    )
    host = urlparse(collector_url()).netloc or "unknown"
    parts = [
        "python_hook",
        f"event={event}",
        f"has_session_id={str(has_session).lower()}",
        f"status={status}",
        f"collector_host={host}",
    ]
    if source:
        parts.append(f"source={source}")
    if http_status is not None:
        parts.append(f"http_status={http_status}")
    if result:
        parts.append(f"result={result}")
    if reason:
        parts.append(f"reason={reason}")
    return " ".join(parts)


def post_hook(payload: dict[str, Any], delivery_id: str, *, source: str | None = None) -> str:
    data = json.dumps(payload).encode("utf-8")
    last_error: Exception | None = None
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {collector_token()}",
        "X-NM5-Delivery-Id": delivery_id,
    }
    if source:
        headers["X-NM5-Source"] = source
    for attempt in range(3):
        request = Request(
            f"{collector_url()}/v1/collector/events",
            data=data,
            method="POST",
            headers=headers,
        )
        try:
            with urlopen(request, timeout=1.5) as response:
                http_status = getattr(response, "status", 200)
                result, reason = _parse_collector_result(response.read())
            append_hook_debug(
                _sanitized_delivery(
                    payload,
                    source=source,
                    status="delivered",
                    http_status=http_status,
                    result=result,
                    reason=reason,
                )
            )
            return "delivered"
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            last_error = error
            if isinstance(error, HTTPError) and error.code in {400, 401, 409, 413, 422}:
                result, reason = _parse_collector_result(_read_http_error_body(error))
                append_hook_debug(
                    _sanitized_delivery(
                        payload,
                        source=source,
                        status=type(error).__name__,
                        http_status=error.code,
                        result=result,
                        reason=reason,
                    )
                )
                return f"http_{error.code}"
            _ = attempt
    if last_error is not None:
        http_status = last_error.code if isinstance(last_error, HTTPError) else None
        result = reason = None
        if isinstance(last_error, HTTPError):
            result, reason = _parse_collector_result(_read_http_error_body(last_error))
        append_hook_debug(
            _sanitized_delivery(
                payload,
                source=source,
                status=type(last_error).__name__,
                http_status=http_status,
                result=result,
                reason=reason,
            )
        )
        return type(last_error).__name__
    return "undelivered"


def run_hook(stdin: str | None = None, *, source: str | None = None) -> int:
    raw = sys.stdin.read() if stdin is None else stdin
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        append_hook_debug("python_hook event=invalid_json status=ignored")
        sys.stdout.buffer.write(HOOK_OK)
        return 0
    if not isinstance(payload, dict):
        append_hook_debug("python_hook event=non_object status=ignored")
        sys.stdout.buffer.write(HOOK_OK)
        return 0
    stamp(payload, source)
    delivery_id = str(uuid.uuid4())
    try:
        post_hook(payload, delivery_id, source=source)
    except Exception as error:
        append_hook_debug(
            _sanitized_delivery(payload, source=source, status=type(error).__name__)
        )
    sys.stdout.buffer.write(HOOK_OK)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_hook())
