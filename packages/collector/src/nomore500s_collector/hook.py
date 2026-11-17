from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HOOK_OK = b"{}\n"


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


def post_hook(payload: dict[str, Any], delivery_id: str) -> None:
    data = json.dumps(payload).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(3):
        request = Request(
            f"{collector_url()}/v1/collector/events",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {collector_token()}",
                "X-NM5-Delivery-Id": delivery_id,
            },
        )
        try:
            with urlopen(request, timeout=1.5) as response:
                response.read()
            return
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            last_error = error
            if isinstance(error, HTTPError) and error.code in {400, 401, 413, 422}:
                return
            _ = attempt
    if last_error is not None:
        return


def run_hook(stdin: str | None = None) -> int:
    raw = sys.stdin.read() if stdin is None else stdin
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        sys.stdout.buffer.write(HOOK_OK)
        return 0
    if not isinstance(payload, dict):
        sys.stdout.buffer.write(HOOK_OK)
        return 0
    delivery_id = str(uuid.uuid4())
    try:
        post_hook(payload, delivery_id)
    except Exception:
        pass
    sys.stdout.buffer.write(HOOK_OK)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_hook())
