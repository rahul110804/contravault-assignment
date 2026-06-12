"""Tests for structured JSON/MD input mode bypass."""
import pytest
import json
from datetime import date

from models.requirement import RequirementBucket
from models.evidence import EvidenceType
from utils.structured_loader import load_structured_tender, load_structured_vendor

def test_load_structured_tender():
    """Verify that structured tender JSON loads perfectly into internal models."""
    sample_tender = {
        "tender_id": "TNDR-001",
        "opening_date": "2026-01-01",
        "schedules": [
            { "id": "Schedule 1", "designation": "410SP-57", "length_m": 11, "qty": 1600 }
        ],
        "requirements": [
            {
                "req_id": "TECH_001",
                "schedule": "Schedule 1",
                "bucket": "technical",
                "text": "Must be 11m long",
                "expected": 11,
                "is_gating": False
            },
            {
                "req_id": "ELIG_001",
                "schedule": None,
                "bucket": "eligibility",
                "text": "Must be MSE",
                "expected": True,
                "is_gating": True
            }
        ]
    }
    
    file_bytes = json.dumps(sample_tender).encode('utf-8')
    req_list, meta = load_structured_tender(file_bytes, "tender.json")
    
    assert len(req_list.requirements) == 2
    assert meta["tender_opening_date"] == "2026-01-01"
    
    tech_req = req_list.requirements[0]
    assert tech_req.req_id == "TECH_001"
    assert tech_req.bucket == RequirementBucket.TECHNICAL
    
    elig_req = req_list.requirements[1]
    assert elig_req.is_gating is True
    assert elig_req.bucket == RequirementBucket.ELIGIBILITY


def test_load_structured_vendor():
    """Verify that structured vendor JSON loads perfectly into internal models."""
    sample_vendor = {
        "vendor_id": "Vishal Heavy",
        "evidence": [
            {
                "field": "mse_status",
                "value": "Micro",
                "evidence_type": "certificate",
                "valid_until": "2027-12-31",
                "source_ref": "vendor_doc_p1.pdf"
            },
            {
                "field": "bid_price",
                "value": 15000,
                "evidence_type": "current_quote",
                "valid_until": None,
                "source_ref": "boq.pdf"
            }
        ]
    }
    
    file_bytes = json.dumps(sample_vendor).encode('utf-8')
    bundle = load_structured_vendor("Vishal Heavy", file_bytes, "vendor.json")
    
    assert len(bundle.evidence) == 2
    assert bundle.vendor_id == "Vishal Heavy"
    
    cert = bundle.evidence[0]
    assert cert.evidence_type == EvidenceType.CERTIFICATE
    assert str(cert.valid_until) == "2027-12-31"
    
    quote = bundle.evidence[1]
    assert quote.evidence_type == EvidenceType.CURRENT_QUOTE
    assert quote.value == 15000
