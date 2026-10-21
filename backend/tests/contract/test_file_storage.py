"""FileStoragePort contract: memory and local disk (Supabase is added with ``needs_supabase``)."""

from collections.abc import Callable
from pathlib import Path

import pytest
from tests.fakes.storage import InMemoryFileStorage

from scout.adapters.storage.local import LocalFileStorage
from scout.ports.storage import FileStoragePort, StorageObjectMissing
from scout.shared import SystemClock, UuidGenerator, ValidationFailed

Make = Callable[[Path], FileStoragePort]


def _supabase() -> FileStoragePort:
    """Needs SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and a private bucket named by SUPABASE_STORAGE_BUCKET."""
    import httpx
    from tests.fakes import InMemoryUsageMeter

    from scout.adapters.storage.supabase import SupabaseStorage
    from scout.settings import get_settings

    s = get_settings()
    assert s.supabase_url and s.supabase_service_role_key, "set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"
    return SupabaseStorage(
        httpx.AsyncClient(), s.supabase_url, s.supabase_service_role_key.get_secret_value(),
        s.supabase_storage_bucket, InMemoryUsageMeter(), SystemClock(), UuidGenerator(),
    )  # fmt: skip


FACTORIES = [
    pytest.param(lambda tmp: InMemoryFileStorage(), id="memory"),
    pytest.param(lambda tmp: LocalFileStorage(tmp / "storage"), id="local"),
    pytest.param(lambda tmp: _supabase(), id="supabase", marks=pytest.mark.needs_supabase),
]


@pytest.fixture(params=FACTORIES)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> FileStoragePort:
    make: Make = request.param
    return make(tmp_path)


async def test_round_trip_and_overwrite(store: FileStoragePort) -> None:
    await store.put("vault/a/1", b"one", "application/pdf")
    assert await store.get("vault/a/1") == b"one"
    await store.put("vault/a/1", b"two", "application/pdf")
    assert await store.get("vault/a/1") == b"two"
    assert await store.exists("vault/a/1")


async def test_missing_object(store: FileStoragePort) -> None:
    assert not await store.exists("vault/a/none")
    with pytest.raises(StorageObjectMissing):
        await store.get("vault/a/none")


async def test_delete_is_idempotent(store: FileStoragePort) -> None:
    await store.put("vault/a/1", b"x", "application/pdf")
    await store.delete("vault/a/1")
    await store.delete("vault/a/1")
    assert not await store.exists("vault/a/1")


async def test_delete_prefix_only_removes_that_prefix(store: FileStoragePort) -> None:
    for key in ("vault/a/1", "vault/a/2", "vault/ab/1", "vault/b/1"):
        await store.put(key, b"x", "application/pdf")
    assert await store.delete_prefix("vault/a") == 2
    assert not await store.exists("vault/a/1")
    assert await store.exists("vault/ab/1")
    assert await store.exists("vault/b/1")
    assert await store.delete_prefix("vault/a") == 0


@pytest.mark.parametrize(
    "key", ["", "../x", "/abs", "vault/../x", "vault//x", "Vault/A", "vault/a b", "vault/a/"]
)
async def test_unsafe_keys_are_refused(store: FileStoragePort, key: str) -> None:
    with pytest.raises(ValidationFailed):
        await store.put(key, b"x", "application/pdf")
