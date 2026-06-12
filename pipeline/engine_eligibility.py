"""Engine 2: Eligibility gate — deterministic, no LLM.

Evaluates mandatory pass/fail gating requirements using hard rules.
A vendor that fails any gating requirement is disqualified regardless of technical score.
"""
import logging
from datetime import date
from typing import Any

from models.requirement import Requirement, RequirementBucket, RequirementList
from models.evidence import VendorEvidence, VendorEvidenceBundle, EvidenceType
from models.scoring import (
    EligibilityVerdict, EligibilityStatus, GatingCheckResult
)
from models.requirement import SourceRef
from utils.date_utils import is_valid_at, parse_date

logger = logging.getLogger(__name__)


def _find_evidence_for_field(
    evidence_list: list[VendorEvidence],
    field_keywords: list[str]
) -> list[VendorEvidence]:
    """Find evidence items whose field name matches any of the keywords (case-insensitive)."""
    results = []
    for ev in evidence_list:
        field_lower = ev.field.lower()
        if any(kw.lower() in field_lower for kw in field_keywords):
            results.append(ev)
    return results


def _check_presence(
    evidence_list: list[VendorEvidence],
    field_keywords: list[str],
    requirement: Requirement
) -> GatingCheckResult:
    """Check if evidence exists for a given field."""
    matches = _find_evidence_for_field(evidence_list, field_keywords)
    if matches:
        return GatingCheckResult(
            requirement_id=requirement.req_id,
            requirement_text=requirement.text,
            passed=True,
            reason=f"Evidence found: {matches[0].field} = {matches[0].value}",
            evidence_used=matches[0].source_ref
        )
    return GatingCheckResult(
        requirement_id=requirement.req_id,
        requirement_text=requirement.text,
        passed=False,
        reason=f"No evidence found matching: {', '.join(field_keywords)}",
        evidence_used=None
    )


def _check_certificate_validity(
    evidence_list: list[VendorEvidence],
    field_keywords: list[str],
    tender_opening_date: date,
    requirement: Requirement
) -> GatingCheckResult:
    """Check if a certificate exists AND is valid at tender opening date."""
    matches = _find_evidence_for_field(evidence_list, field_keywords)
    
    if not matches:
        return GatingCheckResult(
            requirement_id=requirement.req_id,
            requirement_text=requirement.text,
            passed=False,
            reason=f"No certificate found matching: {', '.join(field_keywords)}",
            evidence_used=None
        )
    
    # Check validity of the most relevant match
    for ev in matches:
        if ev.valid_until is not None:
            if is_valid_at(ev.valid_until, tender_opening_date):
                return GatingCheckResult(
                    requirement_id=requirement.req_id,
                    requirement_text=requirement.text,
                    passed=True,
                    reason=f"Certificate '{ev.field}' valid until {ev.valid_until} "
                           f"(tender opens {tender_opening_date})",
                    evidence_used=ev.source_ref
                )
            else:
                return GatingCheckResult(
                    requirement_id=requirement.req_id,
                    requirement_text=requirement.text,
                    passed=False,
                    reason=f"Certificate '{ev.field}' expired on {ev.valid_until}, "
                           f"before tender opening date {tender_opening_date}",
                    evidence_used=ev.source_ref
                )
        else:
            # No expiry date on certificate — pass but note
            return GatingCheckResult(
                requirement_id=requirement.req_id,
                requirement_text=requirement.text,
                passed=True,
                reason=f"Certificate '{ev.field}' found (no expiry date recorded)",
                evidence_used=ev.source_ref
            )
    
    # Should not reach here
    return GatingCheckResult(
        requirement_id=requirement.req_id,
        requirement_text=requirement.text,
        passed=False,
        reason="Certificate check inconclusive",
        evidence_used=None
    )


def _check_numeric_threshold(
    evidence_list: list[VendorEvidence],
    field_keywords: list[str],
    threshold: float,
    operator: str,  # 'gte' or 'lte'
    requirement: Requirement
) -> GatingCheckResult:
    """Check if a numeric evidence value meets a threshold."""
    matches = _find_evidence_for_field(evidence_list, field_keywords)
    
    if not matches:
        return GatingCheckResult(
            requirement_id=requirement.req_id,
            requirement_text=requirement.text,
            passed=False,
            reason=f"No evidence found for numeric check: {', '.join(field_keywords)}",
            evidence_used=None
        )
    
    for ev in matches:
        try:
            value = float(str(ev.value).replace('%', '').strip())
        except (ValueError, TypeError):
            continue
        
        if operator == 'gte' and value >= threshold:
            return GatingCheckResult(
                requirement_id=requirement.req_id,
                requirement_text=requirement.text,
                passed=True,
                reason=f"{ev.field} = {value}% >= {threshold}% threshold",
                evidence_used=ev.source_ref
            )
        elif operator == 'lte' and value <= threshold:
            return GatingCheckResult(
                requirement_id=requirement.req_id,
                requirement_text=requirement.text,
                passed=True,
                reason=f"{ev.field} = {value}% <= {threshold}% threshold",
                evidence_used=ev.source_ref
            )
    
    # Threshold not met
    best = matches[0]
    return GatingCheckResult(
        requirement_id=requirement.req_id,
        requirement_text=requirement.text,
        passed=False,
        reason=f"{best.field} = {best.value} does not meet threshold {operator} {threshold}%",
        evidence_used=best.source_ref
    )


def _classify_gating_check(
    requirement: Requirement,
    evidence_list: list[VendorEvidence],
    tender_opening_date: date
) -> GatingCheckResult:
    """Route a gating requirement to the appropriate check function.
    
    Uses keyword matching on the requirement text to determine the check type.
    """
    req_text_lower = requirement.text.lower()
    expected_str = str(requirement.expected).lower()
    
    # MSE / MSME status check
    if any(kw in req_text_lower for kw in ['mse ', 'msme', 'micro and small', 'micro small']):
        return _check_presence(
            evidence_list,
            ['mse', 'msme', 'micro', 'small enterprise', 'udyam'],
            requirement
        )
    
    # Udyam registration
    if 'udyam' in req_text_lower or 'uam' in req_text_lower:
        return _check_certificate_validity(
            evidence_list,
            ['udyam', 'uam', 'udyog'],
            tender_opening_date,
            requirement
        )
    
    # BIS Marking License
    if 'bis' in req_text_lower or 'bureau of indian standards' in req_text_lower:
        result = _check_certificate_validity(
            evidence_list,
            ['bis', 'bureau of indian standards', 'is_marking', 'bis_license', 'bis_marking'],
            tender_opening_date,
            requirement
        )
        # Additional check: does BIS license cover the tendered IS standard?
        if result.passed and 'is:2713' in req_text_lower or 'is 2713' in req_text_lower:
            bis_evidence = _find_evidence_for_field(
                evidence_list, ['bis', 'is_marking', 'bis_license']
            )
            covers_standard = any(
                '2713' in str(ev.value)
                for ev in bis_evidence
            )
            if not covers_standard:
                result = GatingCheckResult(
                    requirement_id=requirement.req_id,
                    requirement_text=requirement.text,
                    passed=False,
                    reason="BIS license found but does not cover IS:2713",
                    evidence_used=bis_evidence[0].source_ref if bis_evidence else None
                )
        return result
    
    # Local content / Class-I supplier
    if 'local content' in req_text_lower or 'class-i' in req_text_lower or 'class i' in req_text_lower:
        # Extract threshold from expected value
        try:
            threshold = float(str(requirement.expected).replace('%', '').replace('>=', '').strip())
        except (ValueError, TypeError):
            threshold = 60.0  # Default from tender spec
        return _check_numeric_threshold(
            evidence_list,
            ['local content', 'local_content', 'class-i', 'class_i', 'domestic content'],
            threshold,
            'gte',
            requirement
        )
    
    # Manufacturer status
    if 'manufacturer' in req_text_lower:
        return _check_presence(
            evidence_list,
            ['manufacturer', 'manufacturing', 'factory', 'production'],
            requirement
        )
    
    # Generic fallback: simple presence check based on requirement keywords
    keywords = [w for w in req_text_lower.split() if len(w) > 4][:3]
    return _check_presence(evidence_list, keywords, requirement)


def evaluate_eligibility(
    requirements: RequirementList,
    vendor_evidence: dict[str, VendorEvidenceBundle],
    tender_opening_date: date
) -> dict[str, EligibilityVerdict]:
    """Evaluate eligibility for all vendors against gating requirements.
    
    This function is PURELY DETERMINISTIC — no LLM calls.
    
    Args:
        requirements: Extracted tender requirements
        vendor_evidence: Dict mapping vendor_id to their evidence bundle
        tender_opening_date: Date to check certificate validity against
    
    Returns:
        Dict mapping vendor_id to their EligibilityVerdict
    """
    gating_reqs = requirements.gating_requirements()
    logger.info(f"Evaluating {len(gating_reqs)} gating requirements for {len(vendor_evidence)} vendors")
    
    verdicts = {}
    
    for vendor_id, bundle in vendor_evidence.items():
        checks = []
        failed_conditions = []
        
        for req in gating_reqs:
            result = _classify_gating_check(req, bundle.evidence, tender_opening_date)
            checks.append(result)
            if not result.passed:
                failed_conditions.append(f"{req.req_id}: {result.reason}")
        
        eligibility = (
            EligibilityStatus.QUALIFIED
            if not failed_conditions
            else EligibilityStatus.DISQUALIFIED
        )
        
        verdict = EligibilityVerdict(
            vendor_id=vendor_id,
            eligibility=eligibility,
            failed_conditions=failed_conditions,
            checks=checks
        )
        
        verdicts[vendor_id] = verdict
        logger.info(
            f"Vendor '{vendor_id}': {eligibility.value} "
            f"({len(failed_conditions)} failed conditions)"
        )
    
    return verdicts
