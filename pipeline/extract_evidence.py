"""Stage C: Extract vendor evidence from bid documents using LLM.

Extracts every relevant fact from vendor documents, with strict evidence
type classification to prevent the price-in-prior-contract trap.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from models.evidence import VendorEvidence, VendorEvidenceBundle, EvidenceType
from models.requirement import SourceRef
from pipeline.ingest import DocumentBundle, PageContent
from llm.client import GroqClient
from config import INTERMEDIATES_DIR

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a document analyst extracting factual evidence from vendor bid documents 
for a government tender compliance evaluation.

CRITICAL RULES FOR EVIDENCE TYPE CLASSIFICATION:

1. evidence_type MUST be one of: "current_quote", "prior_contract_price", "certificate", 
   "self_declaration", "test_report", "financial"

2. PRICE CLASSIFICATION IS THE MOST IMPORTANT RULE:
   - A price found inside a GeM contract, supply order, purchase order, past contract, 
     or historical document = "prior_contract_price" (NEVER "current_quote")
   - Only a price explicitly submitted as the bid for THIS specific tender = "current_quote"
   - When in doubt, use "prior_contract_price" (conservative default)
   - Look for context clues: past order numbers, previous contract references, 
     historical supply details all indicate prior_contract_price

3. CERTIFICATE VALIDITY:
   - For every certificate, extract the validity/expiry date if present
   - BIS Marking License: extract license number, covered IS standard, and valid_until date
   - Udyam Registration: extract registration number and date
   - MSE/MSME certificate: note the enterprise category

4. Every fact MUST have a source_page (the page number where it was found)

5. Extract ALL relevant facts, including:
   - Product designations offered (which pole sizes/types)
   - BIS license details (number, IS standard covered, validity)
   - Udyam/MSME registration
   - Local content declarations
   - Test report details (what was tested, results, lab name)
   - Manufacturing capabilities
   - Past supply experience (as evidence, but prices as prior_contract_price)
   - Financial details
   - Warranty/delivery commitments
"""

EXTRACTION_PROMPT_TEMPLATE = """Extract all factual evidence from these vendor document pages.
Vendor: {vendor_id}

For each fact, return a JSON object with:
- field: string (descriptive name, e.g., "bis_license_number", "designation_offered", "udyam_registration")
- value: string | number | boolean (the actual value/content)
- evidence_type: "current_quote" | "prior_contract_price" | "certificate" | "self_declaration" | "test_report" | "financial"
- valid_until: string (date in YYYY-MM-DD format) or null (for certificates with expiry)
- source_page: integer (page number where this fact appears)

IMPORTANT: 
- Prices in past contracts/supply orders/GeM orders = "prior_contract_price", NOT "current_quote"
- Extract validity dates for ALL certificates
- One fact per JSON object — do not merge multiple facts

DOCUMENT PAGES:
---
{document_text}
---

Return a JSON array of evidence objects."""

DOCUMENT_TYPE_PROMPT = """Classify the type of this document page. Options:
- "gem_contract" (GeM purchase order, supply order, past contract)
- "bis_license" (BIS Marking License / IS certification)
- "test_report" (Lab test report, quality certificate) 
- "registration" (Udyam/MSME/company registration)
- "financial" (Bank statement, balance sheet, turnover certificate)
- "technical_spec" (Product specification, technical details)
- "bid_document" (Current bid submission, price quote for this tender)
- "other"

Page text (first 500 chars):
---
{page_sample}
---

Return JSON: {{"document_type": "...", "confidence": 0.0-1.0}}"""


def _classify_page_type(page: PageContent, llm: GroqClient) -> str:
    """Classify the document type of a page for better evidence typing."""
    sample = page.text[:500] if page.text else ""
    if not sample.strip():
        return "other"
    
    # Quick heuristic checks before using LLM
    text_lower = sample.lower()
    if any(kw in text_lower for kw in ['gem/', 'government e marketplace', 'contract no', 'supply order', 'purchase order']):
        return 'gem_contract'
    if any(kw in text_lower for kw in ['bis marking', 'bureau of indian standards', 'is/iso', 'licence no', 'license no']):
        return 'bis_license'
    if any(kw in text_lower for kw in ['test report', 'test certificate', 'laboratory', 'nabl']):
        return 'test_report'
    if any(kw in text_lower for kw in ['udyam', 'udyog', 'msme', 'registration number']):
        return 'registration'
    
    # Fall back to LLM for ambiguous pages
    try:
        prompt = DOCUMENT_TYPE_PROMPT.format(page_sample=sample)
        result = llm.generate_json(prompt)
        return result.get('document_type', 'other')
    except Exception:
        return 'other'


def _get_default_evidence_type(doc_type: str) -> str:
    """Map document type to default evidence type (conservative)."""
    mapping = {
        'gem_contract': 'prior_contract_price',
        'bis_license': 'certificate',
        'test_report': 'test_report',
        'registration': 'certificate',
        'financial': 'financial',
        'technical_spec': 'self_declaration',
        'bid_document': 'self_declaration',  # Still conservative — not auto-current_quote
        'other': 'self_declaration'
    }
    return mapping.get(doc_type, 'self_declaration')


def extract_evidence(
    vendor_id: str,
    vendor_bundle: DocumentBundle,
    llm: GroqClient
) -> VendorEvidenceBundle:
    """Extract all evidence from a vendor's document bundle.
    
    Uses document type classification to enforce conservative evidence_type
    defaults, preventing the price-in-prior-contract trap.
    
    Args:
        vendor_id: Vendor identifier
        vendor_bundle: Ingested vendor documents
        llm: Gemini LLM client
    
    Returns:
        VendorEvidenceBundle with all extracted evidence
    """
    pages = [p for p in vendor_bundle.pages if p.has_content]
    logger.info(f"Extracting evidence for vendor '{vendor_id}' from {len(pages)} pages")
    
    # Classify each page's document type
    page_types = {}
    for page in pages:
        doc_type = _classify_page_type(page, llm)
        page_types[page.page_number] = doc_type
        logger.debug(f"  Page {page.page_number}: {doc_type}")
    
    # Process pages in chunks
    all_evidence = []
    chunk_size = 5  # Process 5 pages at a time
    
    for i in range(0, len(pages), chunk_size):
        chunk = pages[i:i + chunk_size]
        
        document_text = "\n\n".join(
            f"--- Page {p.page_number} ({p.file}) [doc_type: {page_types.get(p.page_number, 'other')}] ---\n{p.text}"
            for p in chunk
        )
        
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(
            vendor_id=vendor_id,
            document_text=document_text
        )
        
        try:
            raw_evidence = llm.generate_json(prompt, system_instruction=SYSTEM_PROMPT)
            
            if isinstance(raw_evidence, list):
                for raw in raw_evidence:
                    try:
                        source_page = raw.get('source_page', chunk[0].page_number)
                        doc_type = page_types.get(source_page, 'other')
                        
                        # Get evidence type — use the extracted one, but validate
                        ev_type_str = raw.get('evidence_type', _get_default_evidence_type(doc_type))
                        
                        # CRITICAL: Override evidence type for prices in past contracts
                        if doc_type == 'gem_contract' and ev_type_str == 'current_quote':
                            logger.warning(
                                f"Overriding evidence_type from 'current_quote' to 'prior_contract_price' "
                                f"for price found in GeM contract page {source_page}"
                            )
                            ev_type_str = 'prior_contract_price'
                        
                        try:
                            ev_type = EvidenceType(ev_type_str)
                        except ValueError:
                            ev_type = EvidenceType(_get_default_evidence_type(doc_type))
                        
                        # Determine source file for this page
                        source_file = 'unknown'
                        for p in chunk:
                            if p.page_number == source_page:
                                source_file = p.file
                                break
                        if source_file == 'unknown' and chunk:
                            source_file = chunk[0].file
                        
                        evidence = VendorEvidence(
                            vendor_id=vendor_id,
                            field=raw.get('field', 'unknown'),
                            value=raw.get('value', ''),
                            evidence_type=ev_type,
                            valid_until=raw.get('valid_until'),
                            source_ref=SourceRef(file=source_file, page=source_page)
                        )
                        all_evidence.append(evidence)
                    except Exception as e:
                        logger.warning(f"Failed to parse evidence item: {raw} — {e}")
            
            logger.info(f"Pages {chunk[0].page_number}-{chunk[-1].page_number}: "
                       f"extracted {len(raw_evidence) if isinstance(raw_evidence, list) else 0} evidence items")
        
        except Exception as e:
            logger.error(f"Evidence extraction failed for pages "
                        f"{chunk[0].page_number}-{chunk[-1].page_number}: {e}")
    
    # Log summary
    type_counts = {}
    for ev in all_evidence:
        t = ev.evidence_type.value
        type_counts[t] = type_counts.get(t, 0) + 1
    logger.info(f"Vendor '{vendor_id}': {len(all_evidence)} evidence items — {type_counts}")
    
    # Build bundle
    bundle = VendorEvidenceBundle(
        vendor_id=vendor_id,
        evidence=all_evidence,
        extraction_timestamp=datetime.utcnow()
    )
    
    # Save intermediate
    output_path = INTERMEDIATES_DIR / f'{vendor_id}_evidence.json'
    bundle.to_json_file(output_path)
    
    return bundle
