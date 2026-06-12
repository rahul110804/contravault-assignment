"""Pydantic v2 models for scoring outputs.

Defines compliance scores, eligibility verdicts, and price-ranking
structures used by the scoring engine.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, computed_field

from models.requirement import SourceRef


# --- enums ------------------------------------------------------------

class ComplianceLevel(str, Enum):
    """Degree to which a vendor meets a requirement."""

    FULL = "full"
    PARTIAL = "partial"
    MISSING = "missing"
    NOT_ADDRESSED = "not_addressed"


class EligibilityStatus(str, Enum):
    """Outcome of the eligibility / gating check."""

    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"


class PriceRankStatus(str, Enum):
    """Status of a vendor in the price-ranking process."""

    RANKED = "ranked"
    PENDING_QUOTE = "pending_quote"
    DISQUALIFIED = "disqualified"
    AWAITING_QUOTES = "awaiting_quotes"


# --- compliance -------------------------------------------------------

class ComplianceScore(BaseModel):
    """Compliance assessment for one vendor × one requirement.

    Attributes:
        vendor_id: Identifier for the vendor.
        schedule: Schedule/lot being assessed.
        req_id: The requirement being scored.
        compliance: Qualitative compliance level.
        score: Numeric score in [0, 100].
        reason: Mandatory, substantive explanation (≥ 10 chars).
        requirement_source_ref: Where the requirement was found.
        evidence_source_ref: Where the supporting evidence was found.
    """

    vendor_id: str
    schedule: str
    req_id: str
    compliance: ComplianceLevel
    score: int = Field(ge=0, le=100)
    reason: str = Field(min_length=10)
    requirement_source_ref: SourceRef
    evidence_source_ref: SourceRef | None = None


# --- eligibility / gating --------------------------------------------

class GatingCheckResult(BaseModel):
    """Result of a single gating-requirement check.

    Attributes:
        requirement_id: ID of the gating requirement.
        requirement_text: Full text of the requirement.
        passed: Whether the vendor passed this check.
        reason: Explanation for the pass/fail decision.
        evidence_used: Reference to the evidence used, if any.
    """

    requirement_id: str
    requirement_text: str
    passed: bool
    reason: str
    evidence_used: SourceRef | None = None


class EligibilityVerdict(BaseModel):
    """Overall eligibility verdict for a vendor.

    Attributes:
        vendor_id: Identifier for the vendor.
        eligibility: Qualified or disqualified.
        failed_conditions: Human-readable list of failed gating conditions.
        checks: Detailed per-requirement check results.
    """

    vendor_id: str
    eligibility: EligibilityStatus
    failed_conditions: list[str]
    checks: list[GatingCheckResult]

    @computed_field  # type: ignore[misc]
    @property
    def is_qualified(self) -> bool:
        """Return True if the vendor is qualified."""
        return self.eligibility == EligibilityStatus.QUALIFIED


# --- price ranking ----------------------------------------------------

class PriceRank(BaseModel):
    """Price-ranking entry for a single vendor within a schedule.

    Attributes:
        schedule: Schedule/lot name.
        vendor_id: Identifier for the vendor.
        price: Quoted price (None if quote not yet available).
        rank: Rank among competing vendors (None if not yet ranked).
        status: Current ranking status.
        h1_eliminated: Whether this vendor was eliminated via H1 rule.
    """

    schedule: str
    vendor_id: str
    price: float | None = None
    rank: int | None = None
    status: PriceRankStatus
    h1_eliminated: bool = False


class SchedulePriceRanking(BaseModel):
    """Complete price ranking for all vendors within one schedule.

    Attributes:
        schedule: Schedule/lot name.
        rankings: Ordered list of vendor price ranks.
        h1_vendor: Vendor ID of the H1 (lowest-price) vendor, if known.
    """

    schedule: str
    rankings: list[PriceRank]
    h1_vendor: str | None = None
