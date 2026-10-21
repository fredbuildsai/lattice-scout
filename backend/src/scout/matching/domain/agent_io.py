"""Typed inputs and outputs of the matching agents. No names, institutions, years or countries ever go in."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scout.matching.domain.proposal import BringItem


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class VocabularyEntry(_Model):
    """One method of the controlled vocabulary."""

    method_id: str
    label: str
    synonyms: list[str] = Field(default_factory=list)


class MethodSource(_Model):
    """One text the methods may come from."""

    source_ref: str  # work:W123 or insight:<id>
    text: str = Field(max_length=1500)


class MethodExtractorInput(_Model):
    """Input of MethodExtractor."""

    vocabulary: list[VocabularyEntry]
    sources: list[MethodSource] = Field(max_length=30)


class MethodMention(_Model):
    """A method found in the sources."""

    method_id: str | None = None
    new_label: str | None = Field(default=None, max_length=60)
    source_refs: list[str] = Field(default_factory=list, max_length=20)
    confidence: Literal["high", "medium"]


class MethodSet(_Model):
    """Output of MethodExtractor."""

    methods: list[MethodMention] = Field(max_length=25)


class SummaryTopic(_Model):
    """A topic with its weight."""

    id: str
    name: str
    weight: float


class SummaryMethod(_Model):
    """A method of the vocabulary."""

    id: str
    label: str


class SummaryWork(_Model):
    """A work a side can be cited on (title only)."""

    ref: str  # work:W123
    title: str = Field(max_length=200)


class ResearcherSummary(_Model):
    """What the explainer may know about a person."""

    topics: list[SummaryTopic] = Field(max_length=10)
    methods: list[SummaryMethod] = Field(max_length=10)
    works: list[SummaryWork] = Field(max_length=8)


class ScoreBreakdown(_Model):
    """The numbers behind a proposal."""

    shared_topics: list[str]
    a_only_methods: list[str]
    b_only_methods: list[str]
    score: float


class MatchExplainerInput(_Model):
    """Input of MatchExplainer."""

    a: ResearcherSummary
    b: ResearcherSummary
    score_breakdown: ScoreBreakdown


class Perspective(_Model):
    """The explanation from one side's point of view."""

    you_bring: list[BringItem] = Field(max_length=3)
    they_bring: list[BringItem] = Field(max_length=3)
    why: str = Field(max_length=300)


class MatchRationale(_Model):
    """Output of MatchExplainer."""

    for_a: Perspective
    for_b: Perspective


class SignalItem(_Model):
    """A consented vault insight (verified, at most 300 characters, contact details stripped)."""

    ref: str
    text: str = Field(max_length=300)


class JointIdeasInput(_Model):
    """Input of JointIdeas."""

    a: ResearcherSummary
    b: ResearcherSummary
    a_signals: list[SignalItem] = Field(default_factory=list, max_length=10)
    b_signals: list[SignalItem] = Field(default_factory=list, max_length=10)
    shared_topics: list[str]
    a_only_methods: list[str]
    b_only_methods: list[str]


class JointIdea(_Model):
    """One collaboration idea."""

    title: str = Field(max_length=100)
    question: str = Field(max_length=250)
    uses_from_a: list[BringItem] = Field(min_length=1, max_length=3)
    uses_from_b: list[BringItem] = Field(min_length=1, max_length=3)
    first_step: str = Field(max_length=200)
    possible_output: str = Field(max_length=100)


class JointIdeaList(_Model):
    """Output of JointIdeas."""

    ideas: list[JointIdea] = Field(min_length=1, max_length=3)
