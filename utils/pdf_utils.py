"""PDF processing utilities for ContraVault.

Provides helpers for detecting scanned pages, cleaning extracted text,
and deriving vendor identifiers from filenames.
"""
from __future__ import annotations

import re
import unicodedata

import fitz  # PyMuPDF


def is_page_scanned(page: fitz.Page, text_threshold: int = 50) -> bool:
    """Check whether a PyMuPDF page is scanned (image-based).

    A page is considered scanned when it contains very little
    extractable text but does contain images.

    Args:
        page: A PyMuPDF page object.
        text_threshold: Minimum number of text characters to consider
            the page as digitally authored.  Pages with fewer characters
            AND at least one image are classified as scanned.

    Returns:
        True if the page appears to be a scanned image.
    """
    text = page.get_text("text") or ""
    text_length = len(text.strip())

    if text_length >= text_threshold:
        return False

    # Check for embedded images
    image_list = page.get_images(full=True)
    return len(image_list) > 0


def clean_text(text: str) -> str:
    """Normalise whitespace and remove control characters.

    Operations:
    1. Remove Unicode control characters (except newline and tab).
    2. Replace tabs with single spaces.
    3. Collapse multiple consecutive spaces into one.
    4. Collapse three or more consecutive newlines into two.
    5. Strip leading/trailing whitespace.

    Args:
        text: Raw extracted text.

    Returns:
        Cleaned text.
    """
    if not text:
        return ""

    # Remove control characters except \n and \t
    cleaned = "".join(
        ch
        for ch in text
        if ch in ("\n", "\t") or not unicodedata.category(ch).startswith("C")
    )

    # Replace tabs with spaces
    cleaned = cleaned.replace("\t", " ")

    # Collapse multiple spaces
    cleaned = re.sub(r" {2,}", " ", cleaned)

    # Collapse excessive newlines (3+ → 2)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # Strip leading/trailing whitespace
    return cleaned.strip()


def extract_vendor_id_from_filename(filename: str) -> str:
    """Derive a vendor_id from a filename.

    Removes the file extension, replaces spaces and special characters
    with underscores, collapses consecutive underscores, and lowercases.

    Examples:
        >>> extract_vendor_id_from_filename('Vishal Heavy Engineering.pdf')
        'vishal_heavy_engineering'
        >>> extract_vendor_id_from_filename('ACME Corp (Div-2).PDF')
        'acme_corp_div_2'

    Args:
        filename: Original filename (with or without directory path).

    Returns:
        Normalised vendor identifier string.
    """
    # Take only the filename portion (no directory)
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

    # Remove extension
    if "." in name:
        name = name.rsplit(".", 1)[0]

    # Replace non-alphanumeric characters with underscores
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name)

    # Collapse multiple underscores and strip edge underscores
    name = re.sub(r"_+", "_", name).strip("_")

    return name.lower()
