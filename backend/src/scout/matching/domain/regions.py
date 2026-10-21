"""Country (ISO 3166 alpha-2) to the coarse region shown on the anonymous card."""

from __future__ import annotations

from scout.matching.domain.enums import Region

_BY_REGION: dict[Region, str] = {
    Region.EUROPE: "AL AT BA BE BG BY CH CY CZ DE DK EE ES FI FR GB GR HR HU IE IS IT LT LU LV MD ME MK MT NL NO PL PT RO RS RU SE SI SK UA XK",
    Region.NORTH_AMERICA: "CA US MX CR CU DO GT HN JM NI PA SV TT",
    Region.SOUTH_AMERICA: "AR BO BR CL CO EC PE PY UY VE",
    Region.ASIA: "AE AM AZ BD CN GE HK ID IL IN IQ IR JO JP KG KH KR KW KZ LB LK MM MN MY NP OM PH PK QA SA SG SY TH TJ TM TR TW UZ VN",
    Region.AFRICA: "DZ AO BF BI BJ BW CD CG CI CM DJ EG ET GA GH GM GN KE LR LS LY MA MG ML MR MU MW MZ NA NE NG RW SD SL SN SO SS TG TN TZ UG ZA ZM ZW",
    Region.OCEANIA: "AU NZ FJ PG",
}
_REGION_OF = {c: r for r, codes in _BY_REGION.items() for c in codes.split()}


def region_of(country: str | None) -> Region:
    """Region of an ISO-2 country code; ``unknown`` for anything else."""
    return _REGION_OF.get((country or "").strip().upper(), Region.UNKNOWN)
