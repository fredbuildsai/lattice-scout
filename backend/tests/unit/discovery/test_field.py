"""Field configuration."""

from pathlib import Path

import pytest

from scout.discovery.adapters.fields import load_field
from scout.discovery.domain.field import NoveltyBands
from scout.shared import ValidationFailed

ROOT = Path(__file__).resolve().parents[4]


def test_the_launch_field_loads_with_the_fixture_topic_ids() -> None:
    f = load_field(ROOT / "config" / "fields.yaml", "bio-based-materials")
    assert f.label == "Bio-based materials" and f.openalex_topic_ids[:2] == ["T10610", "T11208"]
    assert f.novelty.saturation_count == 5000 and f.clusters.min_size < f.clusters.max_size
    assert "lignin" in f.keywords


def test_unknown_field_and_invalid_entries_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationFailed):
        load_field(ROOT / "config" / "fields.yaml", "astrology")
    bad = tmp_path / "f.yaml"
    bad.write_text(
        "x:\n  label: X\n  novelty: {saturation_count: 100, bands: {very_thin: 9, thin: 5, moderate: 20}}\n"
    )
    with pytest.raises(ValidationFailed):
        load_field(bad, "x")


@pytest.mark.parametrize(("count", "label"), [(0, "very thin"), (49, "very thin"), (50, "thin"), (299, "thin"),
                                              (300, "moderate"), (1499, "moderate"), (1500, "crowded")])  # fmt: skip
def test_bands(count: int, label: str) -> None:
    assert NoveltyBands(very_thin=50, thin=300, moderate=1500).label(count) == label
