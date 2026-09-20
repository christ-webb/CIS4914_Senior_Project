from typing import Any

import httpx

from nomore500s.models import TraceData


class NoMore500sClient:
    def __init__(
        self,
        api_url: str = "http://localhost:8000",
        timeout_seconds: float = 10,
        *,
        trust_environment: bool = True,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._trust_environment = trust_environment

    def send_trace(self, trace: TraceData) -> dict[str, Any]:
        with httpx.Client(trust_env=self._trust_environment) as client:
            response = client.post(
                f"{self._api_url}/v1/traces",
                json=trace.to_dict(),
                timeout=self._timeout_seconds,
            )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return payload
