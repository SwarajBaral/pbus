import asyncio
import itertools
import json

import httpx
import respx

BATCH = r"/hospitals/batch/(?P<batch_id>[0-9a-f-]+)"


class FakeDirectory:
    """In-memory stand-in for the Hospital Directory API with failure injection."""

    def __init__(self) -> None:
        self.hospitals: dict[int, dict] = {}
        self.rejected_names: set[str] = set()
        self.timeout_after_create: set[str] = set()
        self.activation_failures: list[int] = []
        self.delay = 0.0
        self.in_flight = 0
        self.peak_in_flight = 0
        self._ids = itertools.count(1)

    def install(self, router: respx.Router) -> None:
        router.post("/hospitals/").mock(side_effect=self._create)
        router.get(path__regex=f"{BATCH}$").mock(side_effect=self._list)
        router.patch(path__regex=f"{BATCH}/activate$").mock(side_effect=self._activate)
        router.delete(path__regex=f"{BATCH}$").mock(side_effect=self._delete)

    def batch(self, batch_id: str) -> list[dict]:
        return [h for h in self.hospitals.values() if h["creation_batch_id"] == str(batch_id)]

    async def _create(self, request: httpx.Request) -> httpx.Response:
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            body = json.loads(request.content)
            if body["name"] in self.rejected_names:
                return httpx.Response(422, json={"detail": "rejected by fake directory"})

            hospital = {"id": next(self._ids), "active": False, **body}
            self.hospitals[hospital["id"]] = hospital
            if body["name"] in self.timeout_after_create:
                self.timeout_after_create.discard(body["name"])
                raise httpx.ReadTimeout("response lost", request=request)
            return httpx.Response(200, json=hospital)
        finally:
            self.in_flight -= 1

    def _list(self, request: httpx.Request, batch_id: str) -> httpx.Response:
        found = self.batch(batch_id)
        return httpx.Response(200, json=found) if found else _missing()

    def _activate(self, request: httpx.Request, batch_id: str) -> httpx.Response:
        if self.activation_failures:
            return httpx.Response(self.activation_failures.pop(0), json={"detail": "boom"})
        found = self.batch(batch_id)
        for hospital in found:
            hospital["active"] = True
        return httpx.Response(200, json={"activated_count": len(found)}) if found else _missing()

    def _delete(self, request: httpx.Request, batch_id: str) -> httpx.Response:
        found = self.batch(batch_id)
        for hospital in found:
            del self.hospitals[hospital["id"]]
        return httpx.Response(200, json={"deleted_count": len(found)}) if found else _missing()


def _missing() -> httpx.Response:
    return httpx.Response(404, json={"detail": "No hospitals found with the specified batch ID"})
