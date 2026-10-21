"""Discovery identifiers. Ids derived inside a run are uuid5 values, so a re-run step writes the same rows."""

from __future__ import annotations

from typing import NewType
from uuid import UUID, uuid5

RunId = NewType("RunId", UUID)
OpportunityId = NewType("OpportunityId", UUID)
ClaimId = NewType("ClaimId", UUID)
EvidenceId = NewType("EvidenceId", UUID)
ClusterId = NewType("ClusterId", UUID)
CorpusWorkId = NewType("CorpusWorkId", UUID)
ProbeId = NewType("ProbeId", UUID)

DISCOVERY_NS = UUID("3b7f6c1e-8a42-5d0b-9e17-c4d2a9f05e63")


def corpus_work_id(run: UUID, openalex_id: str) -> CorpusWorkId:
    """Id of a corpus work inside a run."""
    return CorpusWorkId(uuid5(DISCOVERY_NS, f"{run}:work:{openalex_id}"))


def cluster_id(run: UUID, key: str) -> ClusterId:
    """Id of a topic cluster inside a run."""
    return ClusterId(uuid5(DISCOVERY_NS, f"{run}:cluster:{key}"))


def probe_id(run: UUID, label: str) -> ProbeId:
    """Id of a novelty probe (``n1``...) inside a run."""
    return ProbeId(uuid5(DISCOVERY_NS, f"{run}:probe:{label}"))


def opportunity_id(run: UUID, batch: int, ord_: int) -> OpportunityId:
    """Id of the ``ord_``-th opportunity of analyse batch ``batch``."""
    return OpportunityId(uuid5(DISCOVERY_NS, f"{run}:opp:{batch}:{ord_}"))


def claim_id(opportunity: UUID, ord_: int) -> ClaimId:
    """Id of a claim of an opportunity."""
    return ClaimId(uuid5(DISCOVERY_NS, f"{opportunity}:claim:{ord_}"))


def evidence_id(claim: UUID, ord_: int) -> EvidenceId:
    """Id of an evidence item of a claim."""
    return EvidenceId(uuid5(DISCOVERY_NS, f"{claim}:evidence:{ord_}"))
