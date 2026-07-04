"""Utilities for veterinarian name normalization."""

import re
from typing import Optional


VET_HONORIFIC_RE = re.compile(
    r"^(?:dr\s*\(?a\)?\.?|dra\.?|dr\.?|doutora?|prof(?:essora?)?\.?)\s+",
    re.IGNORECASE,
)


def canonicalize_vet_name(value: Optional[str]) -> str:
    """Strip title-only differences from veterinarian names."""
    text = re.sub(r"\s+", " ", (value or "").strip()).strip(" ,;:")
    previous = None
    while text and text != previous:
        previous = text
        text = VET_HONORIFIC_RE.sub("", text).strip(" ,;:")
    return re.sub(r"\s+", " ", text).strip()


def ordering_vet_sql_normalized(column: str = "ts.ordering_vet") -> str:
    """Return a SQLite expression matching the canonical vet-name form."""
    expr = f"LOWER(TRIM({column}))"
    for prefix in (
        "dr(a). ",
        "dr(a) ",
        "dra. ",
        "dra ",
        "dr. ",
        "dr ",
        "doutora ",
        "doutor ",
        "professora ",
        "professor ",
        "prof. ",
        "prof ",
    ):
        expr = f"REPLACE({expr}, '{prefix}', '')"
    return f"TRIM({expr})"
