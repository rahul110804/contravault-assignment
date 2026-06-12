"""Tests for evidence type classification and safety guards."""
import pytest
from datetime import date

from models.evidence import VendorEvidence, VendorEvidenceBundle, EvidenceType
from models.requirement import SourceRef


def _make_evidence(field, value, ev_type, valid_until=None):
    return VendorEvidence(
        vendor_id="test_vendor",
        field=field,
        value=value,
        evidence_type=ev_type,
        valid_until=valid_until,
        source_ref=SourceRef(file="test.pdf", page=1)
    )


class TestEvidenceTyping:
    """Test evidence type classification and filtering."""
    
    def test_prior_contract_price_type(self):
        """Prices from past contracts must be typed as prior_contract_price."""
        ev = _make_evidence(
            "supply_price_per_piece", 25000.0,
            EvidenceType.PRIOR_CONTRACT_PRICE
        )
        assert ev.evidence_type == EvidenceType.PRIOR_CONTRACT_PRICE
    
    def test_prior_contract_price_excluded_from_current_quotes(self):
        """prior_contract_price should not appear in current_quotes filter."""
        bundle = VendorEvidenceBundle(
            vendor_id="test_vendor",
            evidence=[
                _make_evidence("past_price", 25000.0, EvidenceType.PRIOR_CONTRACT_PRICE),
                _make_evidence("bid_price", 22000.0, EvidenceType.CURRENT_QUOTE),
            ]
        )
        
        current = bundle.current_quotes()
        prior = bundle.prior_prices()
        
        assert len(current) == 1
        assert current[0].value == 22000.0
        assert len(prior) == 1
        assert prior[0].value == 25000.0
    
    def test_certificate_with_validity_date(self):
        """Certificates should carry validity dates."""
        ev = _make_evidence(
            "bis_marking_license", "CM/L-1234 IS:2713",
            EvidenceType.CERTIFICATE,
            valid_until=date(2027, 3, 15)
        )
        assert ev.evidence_type == EvidenceType.CERTIFICATE
        assert ev.valid_until == date(2027, 3, 15)
    
    def test_evidence_type_enum_values(self):
        """All expected evidence types should be available."""
        assert EvidenceType.CURRENT_QUOTE == "current_quote"
        assert EvidenceType.PRIOR_CONTRACT_PRICE == "prior_contract_price"
        assert EvidenceType.CERTIFICATE == "certificate"
        assert EvidenceType.SELF_DECLARATION == "self_declaration"
        assert EvidenceType.TEST_REPORT == "test_report"
        assert EvidenceType.FINANCIAL == "financial"
    
    def test_bundle_by_type_filter(self):
        """Bundle filtering by evidence type should work correctly."""
        bundle = VendorEvidenceBundle(
            vendor_id="test_vendor",
            evidence=[
                _make_evidence("cert1", "val1", EvidenceType.CERTIFICATE),
                _make_evidence("cert2", "val2", EvidenceType.CERTIFICATE),
                _make_evidence("test1", "val3", EvidenceType.TEST_REPORT),
                _make_evidence("price1", 100, EvidenceType.PRIOR_CONTRACT_PRICE),
            ]
        )
        
        certs = bundle.by_type(EvidenceType.CERTIFICATE)
        assert len(certs) == 2
        
        tests = bundle.by_type(EvidenceType.TEST_REPORT)
        assert len(tests) == 1
        
        quotes = bundle.current_quotes()
        assert len(quotes) == 0  # No current quotes
