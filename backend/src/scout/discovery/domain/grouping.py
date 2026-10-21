"""Group corpus works into topic clusters (pure, deterministic; no model involved, DECISIONS 64)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class GroupInput:
    """What grouping needs to know about a work."""

    work_id: str
    topic_ids: tuple[str, ...]  # primary topic first
    subfield: str | None = None  # subfield of the primary topic


@dataclass(frozen=True)
class ClusterDraft:
    """A cluster before it is labelled. ``label_key`` (``k1``...) is the id the agents see."""

    key: str
    label_key: str
    work_ids: tuple[str, ...]
    topic_ids: tuple[str, ...]
    subfield: str | None
    is_misc: bool

    @property
    def size(self) -> int:
        """Number of works."""
        return len(self.work_ids)


MISC = "misc"


def _topic_ids(members: Sequence[GroupInput]) -> tuple[str, ...]:
    """Topics of a group, most frequent first (ties by id)."""
    counts: dict[str, int] = defaultdict(int)
    for w in members:
        for t in w.topic_ids:
            counts[t] += 1
    return tuple(t for t, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def group_works(works: Sequence[GroupInput], min_size: int, max_size: int) -> list[ClusterDraft]:
    """Cluster ``works`` by primary topic.

    1. One group per primary topic (works without topics go to ``misc``).
    2. Groups smaller than ``min_size`` merge by subfield (key ``sub:<name>``); what is still too small goes to ``misc``.
    3. Groups larger than ``max_size`` split by the secondary topic.

    Every work lands in exactly one cluster; the result is deterministic, sorted by size (descending) then key, and
    ``label_key`` is ``k1``, ``k2``... in that order (``misc`` is always last and is never a candidate).
    """
    by_topic: dict[str, list[GroupInput]] = defaultdict(list)
    misc: list[GroupInput] = []
    for w in sorted(works, key=lambda w: w.work_id):
        if w.topic_ids:
            by_topic[f"topic:{w.topic_ids[0]}"].append(w)
        else:
            misc.append(w)

    groups: dict[str, list[GroupInput]] = {}
    small: list[tuple[str, list[GroupInput]]] = []
    for key, members in by_topic.items():
        if len(members) >= min_size:
            groups[key] = members
        else:
            small.append((key, members))
    by_subfield: dict[str, list[GroupInput]] = defaultdict(list)
    for _, members in small:
        for w in members:
            if w.subfield:
                by_subfield[f"sub:{w.subfield}"].append(w)
            else:
                misc.append(w)
    for key, members in by_subfield.items():
        if len(members) >= min_size:
            groups[key] = members
        else:
            misc.extend(members)

    for key in list(groups):  # split the big ones by secondary topic
        members = groups[key]
        if len(members) <= max_size:
            continue
        parts: dict[str, list[GroupInput]] = defaultdict(list)
        for w in members:
            parts[w.topic_ids[1] if len(w.topic_ids) > 1 else "rest"].append(w)
        if len(parts) == 1:
            continue  # nothing to split on
        del groups[key]
        for sub, part in parts.items():
            groups[f"{key}/{sub}"] = part

    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    drafts = [
        ClusterDraft(
            key=key, label_key=f"k{i}", work_ids=tuple(w.work_id for w in members), topic_ids=_topic_ids(members),
            subfield=next((w.subfield for w in members if w.subfield), None), is_misc=False,
        )
        for i, (key, members) in enumerate(ordered, start=1)
    ]  # fmt: skip
    if misc:
        drafts.append(
            ClusterDraft(
                key=MISC,
                label_key=MISC,
                work_ids=tuple(sorted(w.work_id for w in misc)),
                topic_ids=_topic_ids(misc),
                subfield=None,
                is_misc=True,
            )  # fmt: skip
        )
    return drafts
