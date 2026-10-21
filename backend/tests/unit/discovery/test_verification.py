"""Deterministic evidence and claim checks (``check_ref`` / ``check_claim``).

Covers plan M3 task 3.7 (evidence verification, deterministic checks), ARCHITECTURE section 4.4 (an opportunity is
shown only with verified evidence) and DECISIONS 58-60; design note M3 section 1 ("verification.py") and section 3
(vault evidence). One test per ``CheckCode`` those two functions can produce, then the hostile cases (hallucinated
ids, stitched quotes, another owner's chunk), then a Hypothesis oracle.
"""

# ruff: noqa: RUF001  (typographic characters are the point of these tests)

from __future__ import annotations

from collections.abc import Sequence

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from scout.discovery.domain.enums import CheckCode, CorpusOrigin
from scout.discovery.domain.verification import (
    ChunkMaterial,
    CheckResult,
    EvidenceRef,
    InsightMaterial,
    ProbeMaterial,
    RunMaterialIndex,
    check_claim,
    check_ref,
    claim_passes,
)
from scout.shared import InvariantViolation
from scout.shared.text import norm
from tests.unit.discovery._builders import (
    OTHER_OWNER,
    OWNER,
    discard_chunk,
    index,
    keep_chunk,
    probe,
    work,
)

C = CheckCode
ABSTRACT = "We study lignin based foams and report their thermal conductivity in detail."
GOOD_QUOTE = "report their thermal conductivity in detail"


def w(id_: str = "W1", quote: str | None = GOOD_QUOTE) -> EvidenceRef:
    return EvidenceRef("work", id_, quote)


def c(id_: str = "d1.c1", quote: str | None = None) -> EvidenceRef:
    return EvidenceRef("chunk", id_, quote)


def code(ref: EvidenceRef, idx: RunMaterialIndex) -> CheckCode:
    return check_ref(ref, idx).code


# ================================================================================================================
# check_ref: work
# ================================================================================================================
def test_ok_a_literature_quote_found_in_the_abstract() -> None:
    r = check_ref(w(), index(works=[work()]))
    assert r.code is C.OK and r.ok


def test_unknown_work_for_a_hallucinated_id() -> None:
    idx = index(works=[work("W1")])
    assert code(w("W999"), idx) is C.UNKNOWN_WORK
    assert code(w("W999"), index()) is C.UNKNOWN_WORK
    assert not check_ref(w("W999"), idx).ok


def test_a_work_id_does_not_resolve_to_a_chunk_label_or_a_probe() -> None:
    idx = index(works=[work("W1")], chunks=[keep_chunk("d1.c1")], probes=[probe("n1")])
    assert code(w("d1.c1"), idx) is C.UNKNOWN_WORK
    assert code(w("n1"), idx) is C.UNKNOWN_WORK


def test_quote_missing_for_literature() -> None:
    assert code(w(quote=None), index(works=[work()])) is C.QUOTE_MISSING


def test_resolved_works_are_literature_and_need_a_quote_too() -> None:
    idx = index(works=[work(origin=CorpusOrigin.RESOLVED)])
    assert code(w(quote=None), idx) is C.QUOTE_MISSING
    assert code(w(), idx) is C.OK


def test_quote_too_short_below_15_characters() -> None:
    q14 = "lignin based f"
    q15 = "lignin based fo"
    assert (len(q14), len(q15)) == (14, 15)
    idx = index(works=[work()])
    assert code(w(quote=q14), idx) is C.QUOTE_TOO_SHORT
    assert code(w(quote=q15), idx) is C.OK


def test_quote_length_is_measured_after_normalisation() -> None:
    padded = "lignin      based"  # 18 raw characters, 12 after norm
    assert len(padded) >= 15 and len(norm(padded)) < 15
    assert code(w(quote=padded), index(works=[work()])) is C.QUOTE_TOO_SHORT


def _long_abstract() -> str:
    return " ".join(f"word{i:03d}" for i in range(80))  # 7 chars + space each, well over 300


def test_quote_too_long_above_300_characters() -> None:
    abstract = _long_abstract()
    q300 = abstract[:300]
    q301 = abstract[:301]
    assert (len(norm(q300)), len(norm(q301))) == (300, 301)
    idx = index(works=[work(abstract=abstract)])
    assert code(w(quote=q301), idx) is C.QUOTE_TOO_LONG
    assert code(w(quote=q300), idx) is C.OK


def test_no_abstract_when_the_work_has_none() -> None:
    idx = index(works=[work(abstract=None)])
    assert code(w(), idx) is C.NO_ABSTRACT


def test_quote_not_found_when_the_text_is_not_in_the_abstract() -> None:
    idx = index(works=[work()])
    assert code(w(quote="a sentence that the abstract never contains"), idx) is C.QUOTE_NOT_FOUND


def test_a_literature_quote_from_the_title_only_is_not_found() -> None:
    idx = index(works=[work(title="Lignin based foams for insulation")])
    assert code(w(quote="Lignin based foams for insulation"), idx) is C.QUOTE_NOT_FOUND


def test_matching_is_case_sensitive() -> None:
    assert code(w(quote=GOOD_QUOTE.upper()), index(works=[work()])) is C.QUOTE_NOT_FOUND


@pytest.mark.parametrize(
    ("abstract", "quote"),
    [
        ("The “bio-based” foams achieve a 30 – 40% reduction in thermal conductivity.",
         'The "bio-based" foams achieve a 30 - 40% reduction'),  # curly quotes and en dash in the source
        ('The "bio-based" foams achieve a 30 - 40% reduction in thermal conductivity.',
         "The “bio-based” foams achieve a 30 – 40% reduction"),  # ... and in the quote
        ("Ageing was assessed after pro-\nlonged exposure to humidity and heat.",
         "assessed after pro-longed exposure to humidity"),  # hyphenation at a line break
        ("Ageing was assessed after   prolonged\n exposure to humidity and heat.",
         "assessed after prolonged exposure to humidity"),  # whitespace
        ("The foam’s density – measured twice – was low enough.", "The foam's density - measured twice - was low"),
        ("Soft­hyphen words should survive extraction in this text.", "Softhyphen words should survive extraction"),
    ],
)  # fmt: skip
def test_typographic_normalisation_matches_both_ways(abstract: str, quote: str) -> None:
    assert code(w(quote=quote), index(works=[work(abstract=abstract)])) is C.OK


# ---- profile works -----------------------------------------------------------------------------------------------
def test_a_profile_ref_needs_no_quote() -> None:
    idx = index(works=[work("W2", origin=CorpusOrigin.PROFILE)])
    assert code(w("W2", None), idx) is C.OK


def test_a_profile_quote_may_come_from_the_title_even_without_an_abstract() -> None:
    title = "Nanocellulose aerogels from sidestream pulp"
    idx = index(works=[work("W2", title=title, abstract=None, origin=CorpusOrigin.PROFILE)])
    assert code(w("W2", "Nanocellulose aerogels from sidestream"), idx) is C.OK


def test_a_profile_quote_may_come_from_the_abstract() -> None:
    idx = index(works=[work("W2", origin=CorpusOrigin.PROFILE)])
    assert code(w("W2"), idx) is C.OK


def test_a_profile_quote_found_nowhere_is_not_found() -> None:
    idx = index(works=[work("W2", origin=CorpusOrigin.PROFILE)])
    assert code(w("W2", "a claim about something else entirely"), idx) is C.QUOTE_NOT_FOUND


# ================================================================================================================
# check_ref: chunk
# ================================================================================================================
PASSAGE = "The foams must meet EN 13501 fire safety at the proposed bio-content."


def test_unknown_chunk() -> None:
    assert (
        code(c("d1.c99", "some quote that is long enough"), index(chunks=[keep_chunk()])) is C.UNKNOWN_CHUNK
    )
    assert code(c("d1.c1", "some quote that is long enough"), index()) is C.UNKNOWN_CHUNK


def test_a_keep_quote_found_in_the_chunk_text_is_ok() -> None:
    idx = index(chunks=[keep_chunk(text=PASSAGE)])
    assert code(c(quote="meet EN 13501 fire safety at the proposed"), idx) is C.OK


def test_a_keep_quote_is_normalised_like_the_vault_does() -> None:
    idx = index(
        chunks=[keep_chunk(text="They give “no evidence” of long term dura-\nbility – a serious concern.")]
    )
    assert code(c(quote='give "no evidence" of long term dura-bility - a serious'), idx) is C.OK


def test_a_keep_quote_not_in_the_text_is_not_found() -> None:
    idx = index(chunks=[keep_chunk(text=PASSAGE)])
    assert code(c(quote="meet EN 13501 fire safety at the wrong content"), idx) is C.QUOTE_NOT_FOUND


@pytest.mark.parametrize(("quote", "expected"), [(None, C.QUOTE_MISSING), ("too short", C.QUOTE_TOO_SHORT)])
def test_a_vault_quote_follows_the_same_length_rules(quote: str | None, expected: CheckCode) -> None:
    assert code(c(quote=quote), index(chunks=[keep_chunk(text=PASSAGE)])) is expected


RETAINED = ("Reviewer 2 asks for fire safety evidence", "The applicants give no evidence of durability")


def test_a_discard_quote_that_is_a_substring_of_one_retained_quote_is_ok() -> None:
    idx = index(chunks=[discard_chunk(quotes=RETAINED)])
    assert code(c("d2.c1", "asks for fire safety evidence"), idx) is C.OK  # inside the first
    assert code(c("d2.c1", "give no evidence of durability"), idx) is C.OK  # inside the second
    assert code(c("d2.c1", RETAINED[0]), idx) is C.OK  # the whole retained quote


def test_a_discard_quote_stitched_across_two_retained_quotes_is_not_found() -> None:
    idx = index(chunks=[discard_chunk(quotes=RETAINED)])
    for stitched in (
        "fire safety evidence The applicants give no evidence",  # glued
        "fire safety evidence  …  The applicants give no",  # joined by an ellipsis like the passage text
        "fire safety evidence. The applicants give no evidence",
    ):
        assert code(c("d2.c1", stitched), idx) is C.QUOTE_NOT_FOUND


def test_a_discard_chunk_with_no_retained_quotes_cannot_verify_anything() -> None:
    assert (
        code(c("d2.c1", "any quote that is long enough"), index(chunks=[discard_chunk(quotes=())]))
        is C.QUOTE_NOT_FOUND
    )


@pytest.mark.parametrize(
    "chunk", [keep_chunk(text=PASSAGE, active=False), discard_chunk(quotes=RETAINED, active=False)]
)
def test_source_deleted_when_the_document_is_inactive(chunk: ChunkMaterial) -> None:
    quote = "meet EN 13501 fire safety at the proposed" if chunk.retention == "keep" else RETAINED[0]
    assert code(c(chunk.label, quote), index(chunks=[chunk])) is C.SOURCE_DELETED


@pytest.mark.parametrize(
    "chunk", [keep_chunk(text=PASSAGE, owner=OTHER_OWNER), discard_chunk(quotes=RETAINED, owner=OTHER_OWNER)]
)
def test_a_chunk_of_another_owner_raises_and_never_returns(chunk: ChunkMaterial) -> None:
    quote = "meet EN 13501 fire safety at the proposed" if chunk.retention == "keep" else RETAINED[0]
    with pytest.raises(InvariantViolation):
        check_ref(c(chunk.label, quote), index(chunks=[chunk], owner=OWNER))


def test_a_foreign_chunk_raises_even_with_a_hopeless_quote() -> None:
    with pytest.raises(InvariantViolation):
        check_ref(c("d1.c1", None), index(chunks=[keep_chunk(owner=OTHER_OWNER)]))


# ================================================================================================================
# check_ref: insight and count
# ================================================================================================================
INSIGHT = InsightMaterial(
    ref="d1.aims[0]", text="Develop PFAS-free lignin membranes", chunk_labels=("d1.c1",)
)


def test_an_insight_in_the_index_is_ok_without_a_quote() -> None:
    assert code(EvidenceRef("insight", "d1.aims[0]"), index(insights=[INSIGHT])) is C.OK


def test_unknown_insight_when_the_ref_is_not_in_the_index() -> None:
    idx = index(insights=[INSIGHT], chunks=[keep_chunk("d1.c1")])
    assert code(EvidenceRef("insight", "d1.aims[3]"), idx) is C.UNKNOWN_INSIGHT
    assert code(EvidenceRef("insight", "d1.c1"), idx) is C.UNKNOWN_INSIGHT  # a chunk label is not an insight
    assert code(EvidenceRef("insight", "d1.aims[0]"), index()) is C.UNKNOWN_INSIGHT


def test_a_measured_count_is_ok() -> None:
    assert code(EvidenceRef("count", "n1"), index(probes=[probe("n1", 427)])) is C.OK


def test_a_measured_count_of_zero_is_still_a_count() -> None:
    assert code(EvidenceRef("count", "n1"), index(probes=[probe("n1", 0)])) is C.OK


@pytest.mark.parametrize(
    "p",
    [
        probe("n2", 427),
        probe("n1", 427, measured=False),
        probe("n1", None),
        probe("n1", None, measured=False),
    ],
)
def test_unknown_count_for_a_missing_unmeasured_or_null_probe(p: ProbeMaterial) -> None:
    assert code(EvidenceRef("count", "n1"), index(probes=[p])) is C.UNKNOWN_COUNT
    assert code(EvidenceRef("count", "n9"), index()) is C.UNKNOWN_COUNT


# ================================================================================================================
# check_claim
# ================================================================================================================
def ok(ref: EvidenceRef) -> tuple[EvidenceRef, CheckResult]:
    return ref, CheckResult(C.OK)


def bad(ref: EvidenceRef, why: CheckCode) -> tuple[EvidenceRef, CheckResult]:
    return ref, CheckResult(why)


WORK_OK = ok(w())
COUNT_REF = EvidenceRef("count", "n1")
IDX = index(works=[work()], probes=[probe("n1", 1000)])
PLAIN = "Lignin foams show a thermal conductivity advantage over polystyrene."


def claim_code(
    text: str, results: Sequence[tuple[EvidenceRef, CheckResult]], idx: RunMaterialIndex = IDX
) -> CheckCode:
    return check_claim(text, results, idx)


def test_claim_ok_when_one_reference_is_ok() -> None:
    assert claim_code(PLAIN, [WORK_OK]) is C.OK


def test_claim_ok_when_at_least_one_reference_is_ok_among_failures() -> None:
    results = [bad(w("W9"), C.UNKNOWN_WORK), WORK_OK, bad(c("d1.c1", "x"), C.QUOTE_NOT_FOUND)]
    assert claim_code(PLAIN, results) is C.OK


def test_no_evidence_when_there_are_no_references() -> None:
    assert claim_code(PLAIN, []) is C.NO_EVIDENCE


def test_a_failing_evidence_code_when_no_reference_is_ok() -> None:
    assert claim_code(PLAIN, [bad(w("W9"), C.QUOTE_NOT_FOUND)]) is C.QUOTE_NOT_FOUND
    assert claim_code(PLAIN, [bad(w("W9"), C.UNKNOWN_WORK)]) is C.UNKNOWN_WORK
    assert claim_code(PLAIN, [bad(c(), C.SOURCE_DELETED)]) is C.SOURCE_DELETED
    two = claim_code(PLAIN, [bad(w("W9"), C.UNKNOWN_WORK), bad(c(), C.UNKNOWN_CHUNK)])
    assert two in (C.UNKNOWN_WORK, C.UNKNOWN_CHUNK)


@pytest.mark.parametrize("text", [
    "Contact jane.doe@example.edu for the data.",
    "The PI (a.b-c@lab.example.org) reported the result.",
    "mail me: x@y.io",
])  # fmt: skip
def test_personal_data_for_an_email_in_the_claim_text(text: str) -> None:
    assert claim_code(text, [WORK_OK]) is C.PERSONAL_DATA


def test_no_email_no_personal_data() -> None:
    assert claim_code("Meet at the 3 pm seminar @ the lab, see section 2.", [WORK_OK]) is C.OK


NOVELTY_TEXTS = [
    "There are few studies on lignin foams with fire retardants.",
    "Currently no papers combine lignin with PFAS-free membranes.",
    "Only 12 works address nanocellulose aerogel drying.",
    "This shows a lack of research on bio-based flame retardants.",
    "There is little work on enzymatic delignification at pilot scale.",
    "Evidence is scarce in the research on this process.",
    "This route has not been studied for wet-laid foams.",
    "Few studies address this question.",  # sentence case
]


@pytest.mark.parametrize("text", NOVELTY_TEXTS)
def test_novelty_wording_without_a_count_reference_is_refused(text: str) -> None:
    assert claim_code(text, [WORK_OK]) is C.NOVELTY_WITHOUT_COUNT


@pytest.mark.parametrize("text", [
    "There are few studies on lignin foams with fire retardants.",
    "Currently no papers combine lignin with PFAS-free membranes.",
    "This shows a lack of research on bio-based flame retardants.",
])  # fmt: skip
def test_novelty_wording_with_a_cited_count_passes(text: str) -> None:
    assert claim_code(text, [WORK_OK, ok(COUNT_REF)]) is C.OK


def test_novelty_wording_with_a_cited_count_and_a_matching_number_passes() -> None:
    assert claim_code("Only 1000 works match this query since 2021.", [ok(COUNT_REF)]) is C.OK


def test_plain_text_without_novelty_wording_needs_no_count() -> None:
    assert claim_code(PLAIN, [WORK_OK]) is C.OK
    assert claim_code("Lignin and cellulose are complementary feedstocks.", [WORK_OK]) is C.OK


@pytest.mark.parametrize(("number", "expected"), [
    (1049, C.OK), (951, C.OK), (1000, C.OK),  # 4.9 %, 4.9 %, exact
    (1051, C.COUNT_MISMATCH), (949, C.COUNT_MISMATCH),  # 5.1 %
    (700, C.COUNT_MISMATCH), (5000, C.COUNT_MISMATCH),
])  # fmt: skip
def test_count_tolerance_is_five_percent(number: int, expected: CheckCode) -> None:
    text = f"About {number} works match this query."
    assert claim_code(text, [WORK_OK, ok(COUNT_REF)]) is expected


def test_one_matching_integer_is_enough() -> None:
    text = "Of 120 candidate groups, 1000 works match since 2021."
    assert claim_code(text, [ok(COUNT_REF)]) is C.OK


def test_integers_below_ten_are_ignored() -> None:
    assert claim_code("In 3 of 5 clusters, 1000 works match.", [ok(COUNT_REF)]) is C.OK
    assert (
        claim_code("The 3 clusters share 7 topics.", [WORK_OK, ok(COUNT_REF)]) is C.OK
    )  # nothing to compare


def test_numbers_are_not_compared_when_no_count_is_cited() -> None:
    assert claim_code("The 2021 survey covered 700 groups.", [WORK_OK]) is C.OK


def test_the_count_comes_from_the_probe_in_the_index() -> None:
    idx = index(works=[work()], probes=[probe("n1", 427)])
    assert claim_code("427 works match.", [ok(COUNT_REF)], idx) is C.OK
    assert claim_code("1000 works match.", [ok(COUNT_REF)], idx) is C.COUNT_MISMATCH


def test_the_claim_level_codes_come_before_the_evidence_code_in_order() -> None:
    both_bad = [bad(w("W9"), C.UNKNOWN_WORK)]
    mail = "Ask jane@example.edu about few studies on foams."
    assert claim_code(mail, []) is C.NO_EVIDENCE  # no references beats the e-mail
    assert claim_code(mail, both_bad) is C.PERSONAL_DATA  # e-mail beats novelty wording and the evidence code
    assert claim_code("There are few studies on foams.", both_bad) is C.NOVELTY_WITHOUT_COUNT
    assert claim_code("About 700 works match.", [WORK_OK, ok(COUNT_REF)]) is C.COUNT_MISMATCH


def test_claim_passes_only_for_ok() -> None:
    assert claim_passes(C.OK)
    assert not any(claim_passes(x) for x in CheckCode if x is not C.OK)


# ================================================================================================================
# Hypothesis: check_ref is ok only when the normalised quote is verbatim in the cited text
# ================================================================================================================
ALPHA = "ab cd-ef\n ­–—“”‘’\"'12"  # words, hyphens, dashes, curly quotes, soft hyphen, newline
texts = st.text(alphabet=ALPHA, min_size=20, max_size=350)
kinds = st.sampled_from(["literature", "profile", "keep", "discard"])


def _cited_texts(kind: str, title: str, body: str, parts: tuple[str, ...]) -> list[str]:
    """The oracle: every text the quote may legitimately be found in."""
    return {"literature": [body], "profile": [title, body], "keep": [body], "discard": list(parts)}[kind]


@st.composite
def scenario(draw: st.DrawFn) -> tuple[EvidenceRef, RunMaterialIndex, str, list[str], bool]:
    kind = draw(kinds)
    title, body = draw(texts), draw(texts)
    parts = (draw(texts), draw(texts))
    active = draw(st.booleans())
    source = draw(st.sampled_from(_cited_texts(kind, title, body, parts)))
    start = draw(st.integers(0, max(0, len(source) - 1)))
    end = draw(st.integers(start, len(source)))
    quote = draw(
        st.sampled_from(
            [source[start:end], source[start:end] + draw(st.text(alphabet=ALPHA, max_size=3)), draw(texts)]
        )
    )
    if kind in ("literature", "profile"):
        origin = CorpusOrigin.SEARCH if kind == "literature" else CorpusOrigin.PROFILE
        idx = index(works=[work("W1", title=title, abstract=body, origin=origin)])
        ref = EvidenceRef("work", "W1", quote)
    else:
        chunk = (
            keep_chunk("d1.c1", body, active=active)
            if kind == "keep"
            else discard_chunk("d1.c1", parts, active=active)
        )
        idx = index(chunks=[chunk])
        ref = EvidenceRef("chunk", "d1.c1", quote)
    return ref, idx, kind, _cited_texts(kind, title, body, parts), active or kind in ("literature", "profile")


@settings(max_examples=400, deadline=None)
@given(scenario())
def test_ok_only_if_the_normalised_quote_is_a_substring_of_the_cited_text(
    s: tuple[EvidenceRef, RunMaterialIndex, str, list[str], bool],
) -> None:
    ref, idx, kind, cited, active = s
    assert ref.quote is not None
    result = check_ref(ref, idx)
    q = norm(ref.quote)
    found = any(q in norm(t) for t in cited)
    if result.ok:
        assert found and active
        if kind != "profile":
            assert 15 <= len(q) <= 300
    if found and active and 15 <= len(q) <= 300:
        assert result.ok, result  # and nothing verbatim of a sensible length is refused


@settings(max_examples=150, deadline=None)
@given(s=scenario(), ghost=st.text(alphabet="W0123456789dc.", min_size=1, max_size=8))
def test_an_id_that_is_not_in_the_index_is_never_ok(
    s: tuple[EvidenceRef, RunMaterialIndex, str, list[str], bool], ghost: str
) -> None:
    ref, idx, *_ = s
    if ghost in idx.works or ghost in idx.chunks:
        return
    assert not check_ref(EvidenceRef(ref.type, ghost, ref.quote), idx).ok


@given(
    labels=st.lists(st.sampled_from(["n1", "n2", "n3"]), min_size=1, max_size=3, unique=True),
    count=st.one_of(st.none(), st.integers(0, 10_000)),
    measured=st.booleans(),
)
def test_a_count_ref_is_ok_exactly_when_the_probe_is_measured_with_a_number(
    labels: list[str], count: int | None, measured: bool
) -> None:
    idx = index(probes=[probe(label, count, measured=measured) for label in labels])
    for label in ("n1", "n2", "n3"):
        res = check_ref(EvidenceRef("count", label), idx).ok
        assert res == (label in labels and measured and count is not None)
