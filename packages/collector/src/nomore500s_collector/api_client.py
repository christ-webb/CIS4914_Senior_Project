from __future__ import annotations

from typing import Any

import httpx


class DeliveryError(Exception):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class LiveRunClient:
    def __init__(self, api_url: str, timeout_seconds: float = 10.0) -> None:
        self._api_url = api_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def post(self, path: str, payload: Any) -> int:
        try:
            with httpx.Client() as client:
                response = client.post(
                    f"{self._api_url}{path}",
                    json=payload,
                    timeout=self._timeout_seconds,
                )
        except httpx.HTTPError as error:
            raise DeliveryError(str(error), retryable=True) from error
        if response.status_code in {200, 201}:
            return response.status_code
        if response.status_code == 409:
            return 409
        retryable = response.status_code >= 500 or response.status_code == 429
        raise DeliveryError(
            f"HTTP {response.status_code}: {response.text[:200]}",
            retryable=retryable,
        )
