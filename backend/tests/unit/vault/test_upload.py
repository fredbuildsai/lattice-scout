"""Upload checks."""

import io
import zipfile

import pytest

from scout.vault.domain.enums import DocumentMime, Retention, RetentionPolicy
from scout.vault.domain.upload import FileRejected, check_zip, clean_filename, resolve_retention, sniff_mime


def docx_bytes(extra: dict[str, bytes] | None = None, *, minimal: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if minimal:
            z.writestr("[Content_Types].xml", "<Types/>")
            z.writestr("word/document.xml", "<w:document/>")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def test_sniff() -> None:
    assert sniff_mime(b"%PDF-1.7\n...") is DocumentMime.PDF
    assert sniff_mime(b"junk" * 10 + b"%PDF-1.4") is DocumentMime.PDF
    assert sniff_mime(docx_bytes()) is DocumentMime.DOCX
    assert sniff_mime(docx_bytes(minimal=False, extra={"x.txt": b"x"})) is None  # a zip that is not a docx
    assert sniff_mime(b"PK\x03\x04truncated") is None
    assert sniff_mime(b"\xd0\xcf\x11\xe0" + b"0" * 100) is None  # legacy .doc
    assert sniff_mime(b"") is None


def test_zip_checks() -> None:
    check_zip(docx_bytes())
    with pytest.raises(FileRejected, match="zip_bomb"):
        check_zip(docx_bytes({"big.bin": b"\0" * (5 * 1024 * 1024)}))  # ratio far above 100
    with pytest.raises(FileRejected, match="zip_bomb"):
        check_zip(docx_bytes({f"f{i}.txt": b"x" for i in range(1001)}))
    with pytest.raises(FileRejected, match="corrupt_file"):
        check_zip(b"PK\x03\x04nonsense")


@pytest.mark.parametrize(
    ("raw", "clean"),
    [("a.pdf", "a.pdf"), ("../../etc/passwd", "passwd"), ("C:\\x\\y.docx", "y.docx"), ("a\x00b\n.pdf", "ab.pdf"),
     ("", "document"), ("   ", "document"), ("e\u0301.pdf", "\u00e9.pdf"), ("x" * 400, "x" * 255)],
)  # fmt: skip
def test_clean_filename(raw: str, clean: str) -> None:
    assert clean_filename(raw) == clean


@pytest.mark.parametrize("keep", [None, True, False])
@pytest.mark.parametrize(
    ("policy", "expected"),
    [(RetentionPolicy.KEEP, Retention.KEEP), (RetentionPolicy.DISCARD, Retention.DISCARD), (RetentionPolicy.USER_CHOICE, None)],
)  # fmt: skip
def test_resolve_retention(policy: RetentionPolicy, expected: Retention | None, keep: bool | None) -> None:
    got = resolve_retention(policy, keep)
    if expected is not None:
        assert got is expected
    else:
        assert got is (Retention.KEEP if keep is True else Retention.DISCARD)  # unticked = discard
