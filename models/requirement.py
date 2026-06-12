"""Pydantic v2 models for tender requirements.

Defines the structured representation of requirements extracted from
tender documents, including schedule grouping, compliance buckets,
and gating flags.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Union

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    """Reference to the original source location of an extracted item."""

    file: str
    page: int


class RequirementBucket(str, Enum):
    """Classification bucket for a tender requirement."""

    TECHNICAL = "technical"
    ELIGIBILITY = "eligibility"
    COMMERCIAL = "commercial"


class Requirement(BaseModel):
    """A single requirement extracted from a tender document.

    Attributes:
        req_id: Unique identifier for this requirement.
        schedule: Schedule/lot this requirement belongs to.
            None means the requirement applies to the whole bid.
        bucket: Classification bucket (technical, eligibility, commercial).
        text: The full text of the requirement as stated in the tender.
        expected: The expected value or condition to satisfy.
        is_gating: Whether failure on this requirement disqualifies a vendor.
        source_ref: Reference to the source file and page.
    """

    req_id: str
    schedule: str | None = None
    bucket: RequirementBucket
    text: str
    expected: Union[str, int, float, bool]
    is_gating: bool = False
    source_ref: SourceRef


class RequirementList(BaseModel):
    """Collection of requirements extracted from a single tender document.

    Attributes:
        requirements: All extracted requirements.
        tender_file: Name / path of the source tender file.
        extraction_timestamp: When the extraction was performed.
    """

    requirements: list[Requirement]
    tender_file: str
    extraction_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # --- query helpers ------------------------------------------------

    def by_schedule(self, schedule: str | None) -> list[Requirement]:
        """Return requirements belonging to a specific schedule.

        Args:
            schedule: Schedule name, or None for whole-bid requirements.

        Returns:
            Filtered list of requirements.
        """
        return [r for r in self.requirements if r.schedule == schedule]

    def by_bucket(self, bucket: RequirementBucket) -> list[Requirement]:
        """Return requirements classified under a given bucket.

        Args:
            bucket: The requirement bucket to filter by.

        Returns:
            Filtered list of requirements.
        """
        return [r for r in self.requirements if r.bucket == bucket]

    def gating_requirements(self) -> list[Requirement]:
        """Return all gating (disqualification-triggering) requirements.

        Returns:
            List of requirements where is_gating is True.
        """
        return [r for r in self.requirements if r.is_gating]

    # --- persistence --------------------------------------------------

    def to_json_file(self, path: Path) -> None:
        """Serialise the requirement list to a JSON file.

        Args:
            path: Destination file path.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def from_json_file(cls, path: Path) -> RequirementList:
        """Deserialise a requirement list from a JSON file.

        Args:
            path: Source file path.

        Returns:
            Populated RequirementList instance.
        """
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(raw)
