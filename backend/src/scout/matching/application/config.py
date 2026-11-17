"""Tunables of the matching jobs (settings carry the same names)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MatchConfig:
    """Matching parameters."""

    field: str = "bio-based-materials"
    expiry_days: int = 14
    cooldown_days: int = 180
    weekly_cap: int = 3
    min_score: float = 0.11
    alpha: float = 1.0
    beta: float = 1.5
    exclude_coauthors: bool = True
    exclude_same_group: bool = True
    candidate_limit: int = 2000
    candidates_per_person: int = 50
    max_explains_per_run: int = 200
    nightly_utc_hour: int = 2
    methods_prompt_version: str = "1.0.1"
    reveal_retry_minutes: int = 10


@dataclass(frozen=True)
class ExternalConfig:
    """Tunables of external suggestions (researchers not on the platform; settings ``EXTERNAL_*``)."""

    refresh_per_day: int = 10  # per account in 24 hours, 0 = unlimited
    batch_size: int = 20  # most new suggestions stored per refresh
    pi_only: bool = True  # only last authors (likely principal investigators) become candidates
    min_score: float = 0.11
    min_topic: float = 0.1
    alpha: float = 1.0
    bridge_coauthors: int = 15
    max_lookups: int = 50
    network_share: float = 0.6
    my_works_limit: int = 300
    concurrency: int = 4
