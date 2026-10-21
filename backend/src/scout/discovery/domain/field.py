"""Field configuration (``config/fields.yaml``): what a launch field searches for and how novelty is banded."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QueryConfig(BaseModel):
    """Literature search defaults."""

    model_config = ConfigDict(extra="forbid")

    years_back: int = Field(default=6, ge=1, le=30)
    type: str = "article"


class NoveltyBands(BaseModel):
    """Count thresholds that give a plain-language band (labels only; the score is continuous)."""

    model_config = ConfigDict(extra="forbid")

    very_thin: int = Field(gt=0)
    thin: int = Field(gt=0)
    moderate: int = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> NoveltyBands:
        if not self.very_thin < self.thin < self.moderate:
            raise ValueError("novelty bands must be increasing")
        return self

    def label(self, count: int) -> str:
        """``very thin``, ``thin``, ``moderate`` or ``crowded``."""
        if count < self.very_thin:
            return "very thin"
        if count < self.thin:
            return "thin"
        return "moderate" if count < self.moderate else "crowded"


class NoveltyConfig(BaseModel):
    """How the OpenAlex count of matching works becomes a novelty score."""

    model_config = ConfigDict(extra="forbid")

    years_back: int = Field(default=5, ge=1, le=30)
    saturation_count: int = Field(default=5000, gt=1)
    bands: NoveltyBands


class ClusterConfig(BaseModel):
    """Grouping thresholds."""

    model_config = ConfigDict(extra="forbid")

    min_size: int = Field(default=3, ge=1)
    max_size: int = Field(default=150, ge=2)
    max_labelled: int = Field(default=30, ge=1, le=100)


class FieldConfig(BaseModel):
    """One launch field."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    openalex_topic_ids: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    query: QueryConfig = QueryConfig()
    novelty: NoveltyConfig
    clusters: ClusterConfig = ClusterConfig()

    @model_validator(mode="after")
    def _sizes(self) -> FieldConfig:
        if self.clusters.min_size >= self.clusters.max_size:
            raise ValueError("clusters.min_size must be below max_size")
        return self
