"""``/documents`` endpoints (private vault)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile

from scout.api.deps import current_account, get_container
from scout.api.schemas.vault import (
    ClearedOut,
    ClearVaultIn,
    DatasetOut,
    DocumentDetailOut,
    DocumentListOut,
    DocumentOut,
    DocumentPatchIn,
    InsightsOut,
    ItemOut,
    MethodOut,
    ObjectionOut,
    RefOut,
)
from scout.identity.application.service import ConfirmationMismatch
from scout.identity.domain.account import Account
from scout.shared.context import get_context
from scout.vault.application.service import DocumentDetail
from scout.vault.domain.document import Document
from scout.vault.domain.enums import DocKind
from scout.vault.domain.ids import ChunkId
from scout.vault.domain.insights import InsightItem, Section

router = APIRouter(prefix="/api/v1/documents", tags=["vault"])


def _doc_out(d: Document) -> DocumentOut:
    return DocumentOut(
        id=d.id, filename=d.filename, mime=d.mime, size_bytes=d.size_bytes, status=d.status,
        failure_code=d.failure_code, kind=d.effective_kind, kind_source="user" if d.kind else ("scout" if d.kind_suggested else None),
        kind_suggested=d.kind_suggested, retention=d.retention, original_available=d.original_available,
        pages=d.pages, chunk_count=d.chunk_count, created_at=d.created_at, updated_at=d.updated_at, ready_at=d.ready_at,
    )  # fmt: skip


def _refs(item: InsightItem, pages: dict[ChunkId, tuple[int, int]]) -> list[RefOut]:
    return [
        RefOut(chunk_id=cid, page_from=pages[cid][0], page_to=pages[cid][1])
        for cid in item.chunk_ids
        if cid in pages
    ]


def _insights_out(detail: DocumentDetail) -> InsightsOut | None:
    stored = detail.insights
    if stored is None:
        return None
    visible = stored.visible()  # unverified items are never returned
    by: dict[Section, list[InsightItem]] = {s: [] for s in Section}
    for item in visible:
        by[item.section].append(item)

    def items(section: Section) -> list[ItemOut]:
        return [ItemOut(summary=i.data["summary"], refs=_refs(i, detail.pages)) for i in by[section]]

    return InsightsOut(
        title=stored.title,
        aims=items(Section.AIMS), findings=items(Section.FINDINGS), open_questions=items(Section.OPEN_QUESTIONS),
        methods=[MethodOut(name=i.data["name"], detail=i.data.get("detail"), refs=_refs(i, detail.pages)) for i in by[Section.METHODS]],
        datasets=[DatasetOut(name=i.data["name"], status=i.data["status"], refs=_refs(i, detail.pages)) for i in by[Section.DATASETS]],
        reviewer_objections=[
            ObjectionOut(quote=i.data["quote"], summary=i.data["summary"], refs=_refs(i, detail.pages))
            for i in by[Section.REVIEWER_OBJECTIONS]
        ],
        keywords=stored.keywords, prompt_version=stored.prompt_version,
    )  # fmt: skip


@router.post(
    "",
    summary="Upload a PDF or DOCX to your private vault",
    status_code=201,
    response_model=DocumentOut,
    responses={
        200: {"description": "The same file is already in your vault; nothing was created."},
        401: {"description": "Missing or invalid token (checked before the body is read)."},
        409: {"description": "`vault_limit_reached`: delete a document first."},
        413: {"description": "`file_too_large` (the limit is 25 MB)."},
        415: {"description": "`unsupported_file_type`: only PDF and DOCX."},
        422: {"description": "`file_rejected` (empty, damaged or an unsafe archive) or `validation_failed`."},
    },
)
async def upload_document(
    request: Request,
    response: Response,
    file: UploadFile = File(description="PDF or DOCX, at most 25 MB."),  # noqa: B008
    keep_original: bool = Form(
        default=False, description="Keep the original file (honoured when the policy allows)."
    ),
    kind: DocKind | None = Form(default=None, description="Optional classification hint."),  # noqa: B008
    account: Account = Depends(current_account),  # noqa: B008
) -> DocumentOut:
    """The type is detected from the content. Re-uploading the same bytes returns the existing document (200)."""
    c = get_container(request)
    limit = c.settings.vault_max_bytes
    data = await file.read(limit + 1)
    session = get_context().session_id
    result = await c.upload_document.upload(
        account.id, file.filename or "document", data, keep_original, kind,
        UUID(session) if session else None,
    )  # fmt: skip
    if result.created:
        response.headers["Location"] = f"/api/v1/documents/{result.document.id}"
    else:
        response.status_code = 200
    return _doc_out(result.document)


@router.get("", summary="List your documents", response_model=DocumentListOut)
async def list_documents(request: Request, account: Account = Depends(current_account)) -> DocumentListOut:  # noqa: B008
    """Poll this every couple of seconds while a document is between ``uploaded`` and ``analysing``."""
    docs = await get_container(request).vault.list(account.id)
    return DocumentListOut(items=[_doc_out(d) for d in docs])


@router.get(
    "/{document_id}", summary="One document with its extracted insights", response_model=DocumentDetailOut
)
async def get_document(
    document_id: UUID,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> DocumentDetailOut:
    """Only verified insight items are returned. Another user's document answers 404."""
    detail = await get_container(request).vault.get_detail(account.id, document_id)
    return DocumentDetailOut(**_doc_out(detail.document).model_dump(), insights=_insights_out(detail))


@router.patch("/{document_id}", summary="Set what kind of document this is", response_model=DocumentOut)
async def patch_document(
    document_id: UUID,
    body: DocumentPatchIn,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> DocumentOut:
    """Your choice overrides Scout's suggestion."""
    doc = await get_container(request).vault.set_kind(account.id, document_id, body.kind)
    return _doc_out(doc)


@router.post(
    "/delete-all", summary="Delete every document in the vault", status_code=202, response_model=ClearedOut
)
async def delete_all_documents(
    body: ClearVaultIn,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> ClearedOut:
    """Needs the typed word ``DELETE``. Extracted text and insights go at once, stored files right after."""
    if body.confirm != "DELETE":
        raise ConfirmationMismatch()
    return ClearedOut(deleting=await get_container(request).vault.delete_all(account.id))


@router.delete(
    "/{document_id}", summary="Delete a document and everything extracted from it", status_code=204
)
async def delete_document(
    document_id: UUID,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> Response:
    """Extracted text and insights are removed immediately; the stored file is removed right after."""
    await get_container(request).vault.delete(account.id, document_id)
    return Response(status_code=204)
