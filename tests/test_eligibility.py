"""Tests for Engine 2: Eligibility gate (deterministic)."""
import pytest
from datetime import date

from models.requirement import Requirement, RequirementBucket, RequirementList, SourceRef
from models.evidence import VendorEvidence, VendorEvidenceBundle, EvidenceType
from models.scoring import EligibilityStatus
from pipeline.engine_eligibility import evaluate_eligibility


def _make_source_ref(page=1):
    return SourceRef(file="test.pdf", page=page)


def _make_gating_requirement(req_id, text, expected="true"):
    return Requirement(
        req_id=req_id,
        schedule=None,
        bucket=RequirementBucket.ELIGIBILITY,
        text=text,
        expected=expected,
        is_gating=True,
        source_ref=_make_source_ref()
    )


def _make_evidence(vendor_id, field, value, ev_type, valid_until=None, page=1):
    return VendorEvidence(
        vendor_id=vendor_id,
        field=field,
        value=value,
        evidence_type=ev_type,
        valid_until=valid_until,
        source_ref=SourceRef(file=f"{vendor_id}.pdf", page=page)
    )


def _make_req_list(requirements):
    return RequirementList(
        requirements=requirements,
        tender_file="tender.pdf"
    )


def _make_evidence_bundle(vendor_id, evidence_list):
    return VendorEvidenceBundle(
        vendor_id=vendor_id,
        evidence=evidence_list
    )


class TestEligibilityGate:
    """Test the deterministic eligibility gate."""
    
    def test_vendor_missing_mse_is_disqualified(self):
        """Vendor without MSE evidence should be disqualified."""
        reqs = _make_req_list([
            _make_gating_requirement("ELIG_001", "Vendor must have MSE status")
        ])
        evidence = {
            "vendor_a": _make_evidence_bundle("vendor_a", [
                # No MSE evidence
                _make_evidence("vendor_a", "company_name", "Test Corp", EvidenceType.SELF_DECLARATION)
            ])
        }
        
        verdicts = evaluate_eligibility(reqs, evidence, date(2025, 6, 1))
        assert verdicts["vendor_a"].eligibility == EligibilityStatus.DISQUALIFIED
        assert len(verdicts["vendor_a"].failed_conditions) > 0
    
    def test_vendor_with_expired_bis_license_disqualified(self):
        """Vendor with BIS license expired before tender opening should be disqualified."""
        reqs = _make_req_list([
            _make_gating_requirement("ELIG_002", "Valid BIS Marking License for IS:2713")
        ])
        evidence = {
            "vendor_a": _make_evidence_bundle("vendor_a", [
                _make_evidence(
                    "vendor_a", "bis_marking_license", "CM/L-1234 IS:2713",
                    EvidenceType.CERTIFICATE,
                    valid_until=date(2024, 12, 31)  # Expired before 2025-06-01
                )
            ])
        }
        
        verdicts = evaluate_eligibility(reqs, evidence, date(2025, 6, 1))
        assert verdicts["vendor_a"].eligibility == EligibilityStatus.DISQUALIFIED
    
    def test_vendor_with_valid_certificates_qualified(self):
        """Vendor with all valid certificates should be qualified."""
        reqs = _make_req_list([
            _make_gating_requirement("ELIG_001", "Vendor must have MSE status"),
            _make_gating_requirement("ELIG_002", "Valid BIS Marking License for IS:2713"),
            _make_gating_requirement("ELIG_003", "Valid Udyam Registration")
        ])
        evidence = {
            "vendor_a": _make_evidence_bundle("vendor_a", [
                _make_evidence("vendor_a", "mse_status", "Micro Enterprise", EvidenceType.CERTIFICATE),
                _make_evidence(
                    "vendor_a", "bis_marking_license", "CM/L-5678 IS:2713 Part 1-3",
                    EvidenceType.CERTIFICATE,
                    valid_until=date(2027, 3, 15)
                ),
                _make_evidence(
                    "vendor_a", "udyam_registration", "UDYAM-XX-00-1234567",
                    EvidenceType.CERTIFICATE,
                    valid_until=date(2030, 1, 1)
                )
            ])
        }
        
        verdicts = evaluate_eligibility(reqs, evidence, date(2025, 6, 1))
        assert verdicts["vendor_a"].eligibility == EligibilityStatus.QUALIFIED
        assert len(verdicts["vendor_a"].failed_conditions) == 0
    
    def test_high_technical_score_does_not_override_eligibility_failure(self):
        """A vendor failing eligibility is disqualified regardless of how their 
        technical score might be. The eligibility gate is independent."""
        reqs = _make_req_list([
            _make_gating_requirement("ELIG_001", "Vendor must have MSE status")
        ])
        # Vendor with lots of evidence but no MSE
        evidence = {
            "vendor_a": _make_evidence_bundle("vendor_a", [
                _make_evidence("vendor_a", "designation_offered", "410SP-57", EvidenceType.TEST_REPORT),
                _make_evidence("vendor_a", "designation_offered", "410SP-72", EvidenceType.TEST_REPORT),
                _make_evidence("vendor_a", "designation_offered", "410SP-80", EvidenceType.TEST_REPORT),
                _make_evidence("vendor_a", "steel_grade", "410 MPa", EvidenceType.TEST_REPORT),
                # Note: no MSE evidence
            ])
        }
        
        verdicts = evaluate_eligibility(reqs, evidence, date(2025, 6, 1))
        assert verdicts["vendor_a"].eligibility == EligibilityStatus.DISQUALIFIED
    
    def test_multiple_vendors_mixed_eligibility(self):
        """Test with one qualified and one disqualified vendor."""
        reqs = _make_req_list([
            _make_gating_requirement("ELIG_001", "Vendor must have MSE status")
        ])
        evidence = {
            "qualified_vendor": _make_evidence_bundle("qualified_vendor", [
                _make_evidence("qualified_vendor", "mse_status", "Small Enterprise", EvidenceType.CERTIFICATE)
            ]),
            "disqualified_vendor": _make_evidence_bundle("disqualified_vendor", [
                _make_evidence("disqualified_vendor", "company_name", "Big Corp", EvidenceType.SELF_DECLARATION)
            ])
        }
        
        verdicts = evaluate_eligibility(reqs, evidence, date(2025, 6, 1))
        assert verdicts["qualified_vendor"].eligibility == EligibilityStatus.QUALIFIED
        assert verdicts["disqualified_vendor"].eligibility == EligibilityStatus.DISQUALIFIED
