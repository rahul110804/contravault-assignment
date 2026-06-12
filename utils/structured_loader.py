"""Structured data loader for JSON/MD files.

Bypasses PDF ingestion and LLM extraction by loading strictly formatted
JSON files directly into internal Pydantic models.
"""
import json
import logging
from typing import Any

from models.requirement import RequirementList, Requirement, RequirementBucket, SourceRef
from models.evidence import VendorEvidenceBundle, VendorEvidence, EvidenceType

logger = logging.getLogger(__name__)


def load_structured_tender(file_bytes: bytes, filename: str) -> tuple[RequirementList, dict]:
    """Parse a structured tender JSON/MD into a RequirementList.
    
    Args:
        file_bytes: Raw bytes of the uploaded JSON file.
        filename: Name of the uploaded file.
        
    Returns:
        Tuple of (RequirementList, tender_meta dict).
    """
    try:
        raw_data = json.loads(file_bytes.decode('utf-8'))
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse tender JSON: {e}")
    
    requirements = []
    for req in raw_data.get('requirements', []):
        try:
            bucket_str = req.get('bucket', 'technical').lower()
            try:
                bucket = RequirementBucket(bucket_str)
            except ValueError:
                bucket = RequirementBucket.TECHNICAL
            
            is_gating = req.get('is_gating', False)
            if bucket == RequirementBucket.ELIGIBILITY:
                is_gating = True
                
            requirements.append(Requirement(
                req_id=req.get('req_id', 'UNKNOWN'),
                schedule=req.get('schedule'),
                bucket=bucket,
                text=req.get('text', ''),
                expected=req.get('expected', ''),
                is_gating=is_gating,
                source_ref=SourceRef(file=filename, page=1)
            ))
        except Exception as e:
            logger.warning(f"Skipping invalid requirement in {filename}: {e}")
            
    req_list = RequirementList(
        requirements=requirements,
        tender_file=filename
    )
    
    tender_meta = {
        'schedules': raw_data.get('schedules', []),
        'tender_opening_date': raw_data.get('opening_date'),
        'total_requirements': len(requirements)
    }
    
    return req_list, tender_meta


def load_structured_vendor(vendor_id: str, file_bytes: bytes, filename: str) -> VendorEvidenceBundle:
    """Parse a structured vendor JSON/MD into a VendorEvidenceBundle.
    
    Args:
        vendor_id: Identifier for the vendor.
        file_bytes: Raw bytes of the uploaded JSON file.
        filename: Name of the uploaded file.
        
    Returns:
        VendorEvidenceBundle with all evidence items.
    """
    try:
        raw_data = json.loads(file_bytes.decode('utf-8'))
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse vendor JSON {filename}: {e}")
    
    evidence_items = []
    for ev in raw_data.get('evidence', []):
        try:
            ev_type_str = ev.get('evidence_type', 'self_declaration')
            try:
                ev_type = EvidenceType(ev_type_str)
            except ValueError:
                ev_type = EvidenceType.SELF_DECLARATION
                
            evidence_items.append(VendorEvidence(
                vendor_id=vendor_id,
                field=ev.get('field', 'unknown'),
                value=ev.get('value', ''),
                evidence_type=ev_type,
                valid_until=ev.get('valid_until'),
                source_ref=SourceRef(
                    file=ev.get('source_ref', filename),
                    page=1
                )
            ))
        except Exception as e:
            logger.warning(f"Skipping invalid evidence in {filename}: {e}")
            
    bundle = VendorEvidenceBundle(
        vendor_id=vendor_id,
        evidence=evidence_items
    )
    
    return bundle
