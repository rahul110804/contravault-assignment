"""Date parsing and validation utilities for ContraVault.

Handles the wide variety of date formats found in Indian government
tender documents and vendor certificates.
"""
from __future__ import annotations

import re
from datetime import date

from dateutil import parser as dateutil_parser


def parse_date(date_str: str) -> date | None:
    """Parse a date string flexibly, handling common formats.

    Supported formats include (but are not limited to):
        DD/MM/YYYY, DD-MM-YYYY, DD-Mon-YYYY, YYYY-MM-DD,
        DD.MM.YYYY, Month DD YYYY, etc.

    Args:
        date_str: Raw date string to parse.

    Returns:
        Parsed date, or None if the string cannot be parsed.
    """
    if not date_str or not date_str.strip():
        return None

    cleaned = date_str.strip()

    try:
        # dayfirst=True because Indian tender docs predominantly use
        # DD/MM/YYYY ordering.
        parsed = dateutil_parser.parse(cleaned, dayfirst=True)
        return parsed.date()
    except (ValueError, OverflowError):
        return None


def is_valid_at(valid_until: date | None, reference_date: date) -> bool:
    """Check whether a validity date has not expired relative to a reference.

    Args:
        valid_until: Expiry date, or None if the item never expires.
        reference_date: The date to compare against.

    Returns:
        True if valid_until is None (no expiry) or valid_until >= reference_date.
        False if valid_until < reference_date (expired).
    """
    if valid_until is None:
        return True
    return valid_until >= reference_date


# Pre-compiled patterns for extract_dates_from_text
_DATE_PATTERNS: list[re.Pattern[str]] = [
    # DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
    re.compile(r"\b(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})\b"),
    # YYYY-MM-DD (ISO)
    re.compile(r"\b(\d{4}-\d{1,2}-\d{1,2})\b"),
    # DD-Mon-YYYY or DD Mon YYYY  (e.g. 15-Jan-2024, 15 Jan 2024)
    re.compile(
        r"\b(\d{1,2}[\s\-]"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
        r"[\s\-]\d{4})\b",
        re.IGNORECASE,
    ),
    # Month DD, YYYY or Month DD YYYY  (e.g. January 15, 2024)
    re.compile(
        r"\b((?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+\d{1,2},?\s+\d{4})\b",
        re.IGNORECASE,
    ),
]


def extract_dates_from_text(text: str) -> list[date]:
    """Extract all dates found in a text string.

    Uses regex patterns to locate date-like substrings, then attempts
    to parse each one.

    Args:
        text: The text to search for dates.

    Returns:
        List of successfully parsed dates (duplicates possible).
    """
    if not text:
        return []

    found: list[date] = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            parsed = parse_date(match.group(1))
            if parsed is not None:
                found.append(parsed)
    return found
