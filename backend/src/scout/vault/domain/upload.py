"""Pure upload checks: type sniffing, zip-bomb guard, filename cleaning, retention resolution."""

from __future__ import annotations

import io
import unicodedata
import zipfile

from scout.shared import ScoutError
from scout.vault.domain.enums import DocumentMime, Retention, RetentionPolicy

MAX_ZIP_ENTRIES = 1000
MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024
MAX_ZIP_RATIO = 100


class FileRejected(ScoutError):
    """The file is not acceptable (permanent)."""

    code = "file_rejected"
    http_status = 422
    title = "File rejected"

    def __init__(self, reason: str) -> None:
        super().__init__(reason, extra={"reason": reason})
        self.reason = reason


class FileTooLarge(ScoutError):
    """The upload exceeds the size limit."""

    code = "file_too_large"
    http_status = 413
    title = "File too large"


class UnsupportedFileType(ScoutError):
    """Only PDF and DOCX are accepted."""

    code = "unsupported_file_type"
    http_status = 415
    title = "Unsupported file type"


def sniff_mime(data: bytes) -> DocumentMime | None:
    """Detect PDF or DOCX from the bytes. The declared Content-Type and the extension are ignored."""
    if b"%PDF-" in data[:1024]:
        return DocumentMime.PDF
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
        except (zipfile.BadZipFile, ValueError, OSError):
            return None
        if "[Content_Types].xml" in names and "word/document.xml" in names:
            return DocumentMime.DOCX
    return None


def check_zip(data: bytes) -> None:
    """Refuse archives with too many entries, too much uncompressed data or an extreme compression ratio.

    Raises:
        FileRejected: ``zip_bomb`` or ``corrupt_file``.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
    except (zipfile.BadZipFile, ValueError, OSError):
        raise FileRejected("corrupt_file") from None
    if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_ZIP_UNCOMPRESSED:
        raise FileRejected("zip_bomb")
    for i in infos:
        if (
            i.file_size > 1024 * 1024
            and i.compress_size > 0
            and i.file_size / i.compress_size > MAX_ZIP_RATIO
        ):
            raise FileRejected("zip_bomb")


def clean_filename(name: str) -> str:
    """Keep the basename only, NFC-normalised, control characters removed, at most 255 characters."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFC", base)
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C").strip()
    return base[:255] or "document"


def resolve_retention(policy: RetentionPolicy, keep_original: bool | None) -> Retention:
    """Per-document retention from the deployment policy and the user's choice."""
    if policy is RetentionPolicy.KEEP:
        return Retention.KEEP
    if policy is RetentionPolicy.DISCARD:
        return Retention.DISCARD
    return Retention.KEEP if keep_original is True else Retention.DISCARD
