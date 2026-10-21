"""The published-only note says what is true for this run (ADR 016)."""

from scout.api.schemas.discovery import PUBLISHED_ONLY_NOTE, PUBLISHED_ONLY_NOTE_NO_DOCUMENTS, basis_note_for


def test_note_mentions_documents_only_when_the_run_had_some() -> None:
    assert basis_note_for("full", True) is None and basis_note_for("full", False) is None
    assert basis_note_for("published_only", True) == PUBLISHED_ONLY_NOTE
    assert basis_note_for("published_only", False) == PUBLISHED_ONLY_NOTE_NO_DOCUMENTS
    assert (
        "documents" not in PUBLISHED_ONLY_NOTE_NO_DOCUMENTS
    )  # a user who uploaded nothing is not told otherwise
    assert "read on our server" in PUBLISHED_ONLY_NOTE  # the one wording used on the vault page too
