"""Supabase Storage (private bucket, service-role key). Not exercised from the VM: see the ``needs_supabase`` contract."""

from __future__ import annotations

import time
from urllib.parse import quote

import httpx

from scout.ports.storage import StorageObjectMissing, validate_key
from scout.ports.usage import ExternalCallRecord, ExternalService, UsageMeterPort
from scout.shared import Clock, IdGenerator, InfrastructureError
from scout.shared.context import get_context

RETRIES = 3


def _body_status(r: httpx.Response) -> str | None:
    """Supabase Storage answers 400 with ``statusCode: "404"`` for a missing object (and 400 for other errors)."""
    try:
        body = r.json()
    except ValueError:
        return None
    return str(body.get("statusCode")) if isinstance(body, dict) else None


class SupabaseStorage:
    """``FileStoragePort`` over the Storage REST API. Keys never contain filenames; no endpoint metric holds a key."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        service_key: str,
        bucket: str,
        usage: UsageMeterPort,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._http = http
        self._base = base_url.rstrip("/") + "/storage/v1"
        self._headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}
        self._bucket, self._usage, self._clock, self._ids = bucket, usage, clock, ids

    def _record(self, op: str, status: str, http_status: int | None, started: float) -> None:
        ctx = get_context()
        self._usage.record_external_call(
            ExternalCallRecord(
                id=self._ids.new(),
                ts=self._clock.now(),
                account_id=ctx.account_id,
                run_id=ctx.run_id,
                service=ExternalService.STORAGE,
                endpoint=op,
                status="ok" if status == "ok" else "error",
                http_status=http_status,
                latency_ms=round((time.perf_counter() - started) * 1000),
            )  # fmt: skip
        )

    async def _request(
        self,
        op: str,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        json: object = None,
        limit_s: float = 30,
    ) -> httpx.Response:
        last: Exception | None = None
        for _ in range(RETRIES):
            started = time.perf_counter()
            try:
                r = await self._http.request(
                    method, url, headers={**self._headers, **(headers or {})}, content=content, json=json,
                    timeout=limit_s,
                )  # fmt: skip
            except httpx.HTTPError as exc:
                self._record(op, "error", None, started)
                last = exc
                continue
            if r.status_code >= 500 or r.status_code == 429:
                self._record(op, "error", r.status_code, started)
                last = InfrastructureError(f"storage answered {r.status_code}")
                continue
            self._record(op, "ok", r.status_code, started)
            return r
        raise InfrastructureError("Storage unavailable") from last

    def _object(self, key: str) -> str:
        return f"{self._base}/object/{self._bucket}/{quote(validate_key(key), safe='/')}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Upload (upsert)."""
        r = await self._request(
            "put", "POST", self._object(key), content=data, limit_s=60,
            headers={"Content-Type": content_type, "x-upsert": "true"},
        )  # fmt: skip
        if r.status_code >= 400:
            raise InfrastructureError(f"Storage refused the upload ({r.status_code})")

    async def get(self, key: str) -> bytes:
        """Download via the authenticated route."""
        url = f"{self._base}/object/authenticated/{self._bucket}/{quote(validate_key(key), safe='/')}"
        r = await self._request("get", "GET", url)
        if r.status_code == 404 or (r.status_code == 400 and _body_status(r) == "404"):
            raise StorageObjectMissing("Stored file not found")
        if r.status_code >= 400:
            raise InfrastructureError(f"Storage refused the download ({r.status_code})")
        return r.content

    async def delete(self, key: str) -> None:
        """Delete one object (a missing object is fine)."""
        r = await self._request(
            "delete", "DELETE", f"{self._base}/object/{self._bucket}", json={"prefixes": [validate_key(key)]}
        )
        if r.status_code >= 400 and r.status_code != 404:
            raise InfrastructureError(f"Storage refused the delete ({r.status_code})")

    async def exists(self, key: str) -> bool:
        """HEAD the object."""
        url = f"{self._base}/object/authenticated/{self._bucket}/{quote(validate_key(key), safe='/')}"
        r = await self._request("exists", "HEAD", url)
        return r.status_code < 400

    async def delete_prefix(self, prefix: str) -> int:
        """List then delete every object under ``prefix/``."""
        validate_key(prefix)
        r = await self._request(
            "list", "POST", f"{self._base}/object/list/{self._bucket}",
            json={"prefix": prefix, "limit": 1000, "offset": 0},
        )  # fmt: skip
        if r.status_code >= 400:
            raise InfrastructureError(f"Storage refused the listing ({r.status_code})")
        keys = [f"{prefix}/{item['name']}" for item in r.json() if item.get("id")]
        if keys:
            d = await self._request(
                "delete", "DELETE", f"{self._base}/object/{self._bucket}", json={"prefixes": keys}
            )
            if d.status_code >= 400:
                raise InfrastructureError(f"Storage refused the delete ({d.status_code})")
        return len(keys)
