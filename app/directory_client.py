import asyncio
from typing import Any
from uuid import UUID

import httpx
from pydantic import ValidationError

from app.schemas import HospitalRow, UpstreamHospital

RETRYABLE_STATUSES = {429, 502, 503, 504}

# POST is not idempotent: only retry failures where the request never reached the server.
UNSENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)


class DirectoryError(Exception):
    pass


class DirectoryClient:
    def __init__(self, http: httpx.AsyncClient, max_attempts: int, backoff_base: float):
        self._http = http
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base

    async def create_hospital(self, row: HospitalRow, batch_id: UUID) -> int:
        payload = {
            "name": row.name,
            "address": row.address,
            "phone": row.phone,
            "creation_batch_id": str(batch_id),
        }
        response = await self._request("POST", "/hospitals/", json=payload)
        return _hospital(_json(response)).id

    async def list_batch(self, batch_id: UUID) -> list[UpstreamHospital]:
        response = await self._request("GET", f"/hospitals/batch/{batch_id}", missing_ok=True)
        if response.status_code == 404:
            return []
        return [_hospital(item) for item in _json(response)]

    async def activate_batch(self, batch_id: UUID) -> None:
        await self._request("PATCH", f"/hospitals/batch/{batch_id}/activate")

    async def delete_batch(self, batch_id: UUID) -> None:
        await self._request("DELETE", f"/hospitals/batch/{batch_id}", missing_ok=True)

    async def _request(
        self, method: str, path: str, missing_ok: bool = False, **kwargs: Any
    ) -> httpx.Response:
        retryable = UNSENT_ERRORS if method == "POST" else httpx.TransportError
        failure = DirectoryError(f"{method} {path} was not attempted")

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._http.request(method, path, **kwargs)
            except retryable as exc:
                failure = DirectoryError(f"{method} {path} failed: {exc!r}")
            except httpx.HTTPError as exc:
                raise DirectoryError(f"{method} {path} failed: {exc!r}") from exc
            else:
                if response.status_code not in RETRYABLE_STATUSES:
                    return _ensure_success(response, missing_ok)
                failure = DirectoryError(f"{method} {path} returned {response.status_code}")

            if attempt < self._max_attempts:
                await asyncio.sleep(self._backoff_base * 2 ** (attempt - 1))

        raise failure


def _ensure_success(response: httpx.Response, missing_ok: bool) -> httpx.Response:
    if response.is_success or (missing_ok and response.status_code == 404):
        return response
    request = response.request
    raise DirectoryError(
        f"{request.method} {request.url.path} returned {response.status_code}: "
        f"{response.text[:300]}"
    )


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise DirectoryError(f"directory returned invalid JSON: {response.text[:300]}") from exc


def _hospital(payload: Any) -> UpstreamHospital:
    try:
        return UpstreamHospital.model_validate(payload)
    except ValidationError as exc:
        raise DirectoryError(f"unexpected hospital payload from directory: {exc}") from exc
