"""Pydantic v2 models for vendor evidence.

Defines structured representations of evidence items extracted from
vendor bid documents — certificates, price quotes, declarations, etc.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Union

from pydantic import BaseModel, field_validator, Field

from models.requirement import SourceRef

logger = logging.getLogger(__name__)


class EvidenceType(str, Enum):
    """Category of vendor evidence."""

    CURRENT_QUOTE = "current_quote"
    PRIOR_CONTRACT_PRICE = "prior_contract_price"
    CERTIFICATE = "certificate"
    SELF_DECLARATION = "self_declaration"
    TEST_REPORT = "test_report"
    FINANCIAL = "financial"


class VendorEvidence(BaseModel):
    """A single piece of evidence extracted from a vendor's bid.

    Attributes:
        vendor_id: Identifier for the vendor.
        field: The requirement field this evidence addresses.
        value: Extracted value (string, numeric, or boolean).
        evidence_type: Classification of the evidence.
        valid_until: Optional expiry date of the evidence.
        source_ref: Reference to the source file and page.
    """

    vendor_id: str
    field: str
    value: Union[str, int, float, bool]
    evidence_type: EvidenceType
    valid_until: date | None = None
    source_ref: SourceRef

    @field_validator("evidence_type")
    @classmethod
    def _warn_on_current_quote(cls, v: EvidenceType) -> EvidenceType:
        """Log a warning when evidence_type is current_quote.

        current_quote is the conservative default — callers should verify
        that the price genuinely comes from the current tender cycle rather
        than a prior contract.
        """
        if v == EvidenceType.CURRENT_QUOTE:
            logger.warning(
                "Evidence classified as 'current_quote'. "
                "Verify this is from the current tender cycle."
            )
        return v


class VendorEvidenceBundle(BaseModel):
    """Collection of evidence items for a single vendor.

    Attributes:
        vendor_id: Identifier for the vendor.
        evidence: All extracted evidence items.
        extraction_timestamp: When the extraction was performed.
    """

    vendor_id: str
    evidence: list[VendorEvidence]
    extraction_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # --- query helpers ------------------------------------------------

    def by_type(self, et: EvidenceType) -> list[VendorEvidence]:
        """Return evidence items matching a specific type.

        Args:
            et: The evidence type to filter by.

        Returns:
            Filtered list of vendor evidence.
        """
        return [e for e in self.evidence if e.evidence_type == et]

    def certificates(self) -> list[VendorEvidence]:
        """Return all certificate evidence items."""
        return self.by_type(EvidenceType.CERTIFICATE)

    def current_quotes(self) -> list[VendorEvidence]:
        """Return all current-quote evidence items."""
        return self.by_type(EvidenceType.CURRENT_QUOTE)

    def prior_prices(self) -> list[VendorEvidence]:
        """Return all prior-contract-price evidence items."""
        return self.by_type(EvidenceType.PRIOR_CONTRACT_PRICE)

    # --- persistence --------------------------------------------------

    def to_json_file(self, path: Path) -> None:
        """Serialise the evidence bundle to a JSON file.

        Args:
            path: Destination file path.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def from_json_file(cls, path: Path) -> VendorEvidenceBundle:
        """Deserialise an evidence bundle from a JSON file.

        Args:
            path: Source file path.

        Returns:
            Populated VendorEvidenceBundle instance.
        """
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(raw)
