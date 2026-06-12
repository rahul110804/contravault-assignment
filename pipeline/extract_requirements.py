"""Stage B: Extract atomic requirements from tender documents using LLM."""
import json
import logging
from pathlib import Path
from datetime import datetime

from models.requirement import (
    Requirement, RequirementBucket, RequirementList, SourceRef
)
from pipeline.ingest import DocumentBundle, PageContent
from llm.client import GroqClient
from config import INTERMEDIATES_DIR

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a government procurement analyst specializing in Indian public sector tenders.
Your task is to extract EVERY atomic, individually-checkable requirement from the tender document.

For each requirement, determine:
1. A unique req_id (e.g., TECH_S1_001, ELIG_001, COMM_001)
2. Which schedule it applies to (null if applies to whole bid)
3. The bucket: "technical", "eligibility", or "commercial"
4. The requirement text (clear, specific, one rule per requirement)
5. The expected value/condition
6. Whether it is gating (is_gating=true means failure disqualifies the vendor)

Bucketing rules:
- technical: per-schedule specs like length, designation, coating, construction type, steel grade, test reports, BIS standard conformance
- eligibility: MSE status, Class-I local supplier, local content %, Udyam registration, BIS marking license, manufacturer status (these are ALWAYS is_gating=true)
- commercial: security deposit, warranty, inspection, delivery schedule, price terms, option clause

IMPORTANT:
- Extract schedule structure first: identify all schedules, their designations, quantities
- Each requirement must be ATOMIC — one checkable condition per requirement
- eligibility requirements are ALWAYS is_gating=true
- Capture the exact source page number for each requirement
- Do NOT invent requirements not in the document
- Do NOT combine multiple conditions into one requirement
"""

EXTRACTION_PROMPT_TEMPLATE = """Extract all atomic requirements from the following tender document pages.

The document is from pages {start_page} to {end_page}.

For each requirement, return a JSON object with these fields:
- req_id: string (e.g., TECH_S1_001 for technical schedule 1, ELIG_001 for eligibility, COMM_001 for commercial)
- schedule: string or null (schedule identifier like "Schedule 1", "Schedule 2", or null if applies to all)
- bucket: "technical" | "eligibility" | "commercial"
- text: string (the requirement in clear, specific language)
- expected: string | number | boolean (what the vendor must show/meet)
- is_gating: boolean (true if failure = disqualification; always true for eligibility bucket)
- source_page: integer (the page number where this requirement appears)

Return a JSON array of requirement objects. Extract EVERY requirement, not just a summary.

TENDER TEXT:
---
{tender_text}
---

Respond with a JSON array only."""


def _chunk_pages(pages: list[PageContent], max_chars: int = 30000) -> list[list[PageContent]]:
    """Split pages into chunks that fit within token limits."""
    chunks = []
    current_chunk = []
    current_size = 0
    
    for page in pages:
        page_size = len(page.text)
        if current_size + page_size > max_chars and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_size = 0
        current_chunk.append(page)
        current_size += page_size
    
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks


def _extract_tender_opening_date(pages: list[PageContent], llm: GroqClient) -> str | None:
    """Attempt to extract the tender opening date from the document."""
    # Use first few pages which typically contain bid dates
    sample_text = "\n".join(p.text for p in pages[:5])
    
    prompt = """Extract the bid/tender opening date from this document. 
Look for phrases like "bid opening date", "tender opening date", "bid start date", 
"bid end date", "last date", "closing date", or similar.

Return ONLY a JSON object: {"tender_opening_date": "YYYY-MM-DD"} 
If no date is found, return {"tender_opening_date": null}

Document text:
---
""" + sample_text[:5000] + "\n---"
    
    try:
        result = llm.generate_json(prompt)
        return result.get("tender_opening_date")
    except Exception as e:
        logger.warning(f"Failed to extract tender opening date: {e}")
        return None


def _extract_schedules(pages: list[PageContent], llm: GroqClient) -> list[dict]:
    """Extract schedule structure from tender."""
    sample_text = "\n".join(p.text for p in pages[:10])
    
    prompt = """Extract the evaluation schedule structure from this tender document.

Return a JSON array of schedule objects, each with:
- schedule_id: string (e.g., "Schedule 1")
- description: string (what is being procured in this schedule)
- designation: string (product designation/code if any)
- quantity: number or string
- unit: string (e.g., "pcs", "nos", "kg")

Document text:
---
""" + sample_text[:8000] + "\n---\n\nReturn JSON array only."
    
    try:
        result = llm.generate_json(prompt)
        if isinstance(result, list):
            return result
        return []
    except Exception as e:
        logger.warning(f"Failed to extract schedules: {e}")
        return []


def extract_requirements(
    tender_bundle: DocumentBundle,
    llm: GroqClient
) -> RequirementList:
    """Extract atomic requirements from a tender document.
    
    Uses LLM to parse the tender text into individually-checkable requirements,
    bucketed as technical, eligibility, or commercial.
    
    Args:
        tender_bundle: Ingested tender document
        llm: Gemini LLM client
    
    Returns:
        RequirementList with all extracted requirements
    """
    pages = [p for p in tender_bundle.pages if p.has_content]
    logger.info(f"Extracting requirements from {len(pages)} pages")
    
    # Extract schedule structure first
    schedules = _extract_schedules(pages, llm)
    logger.info(f"Identified {len(schedules)} schedules: {schedules}")
    
    # Extract tender opening date
    opening_date = _extract_tender_opening_date(pages, llm)
    logger.info(f"Tender opening date: {opening_date}")
    
    # Process in chunks
    chunks = _chunk_pages(pages)
    all_raw_requirements = []
    
    for i, chunk in enumerate(chunks):
        logger.info(f"Processing chunk {i + 1}/{len(chunks)} "
                    f"(pages {chunk[0].page_number}-{chunk[-1].page_number})")
        
        tender_text = "\n\n".join(
            f"--- Page {p.page_number} ({p.file}) ---\n{p.text}"
            for p in chunk
        )
        
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(
            start_page=chunk[0].page_number,
            end_page=chunk[-1].page_number,
            tender_text=tender_text
        )
        
        try:
            raw_reqs = llm.generate_json(prompt, system_instruction=SYSTEM_PROMPT)
            if isinstance(raw_reqs, list):
                all_raw_requirements.extend(raw_reqs)
                logger.info(f"Chunk {i + 1}: extracted {len(raw_reqs)} requirements")
            else:
                logger.warning(f"Chunk {i + 1}: unexpected response type: {type(raw_reqs)}")
        except Exception as e:
            logger.error(f"Chunk {i + 1} extraction failed: {e}")
    
    # Convert raw JSON to Requirement models
    requirements = []
    seen_ids = set()
    
    for raw in all_raw_requirements:
        try:
            req_id = raw.get('req_id', f'REQ_{len(requirements) + 1:03d}')
            
            # Deduplicate by req_id
            if req_id in seen_ids:
                base_id = req_id
                counter = 2
                while req_id in seen_ids:
                    req_id = f"{base_id}_{counter}"
                    counter += 1
            seen_ids.add(req_id)
            
            # Determine bucket
            bucket_str = raw.get('bucket', 'technical').lower()
            try:
                bucket = RequirementBucket(bucket_str)
            except ValueError:
                bucket = RequirementBucket.TECHNICAL
            
            # Force eligibility requirements to be gating
            is_gating = raw.get('is_gating', False)
            if bucket == RequirementBucket.ELIGIBILITY:
                is_gating = True
            
            # Build source ref
            source_page = raw.get('source_page', 1)
            source_file = tender_bundle.pages[0].file if tender_bundle.pages else 'unknown'
            
            req = Requirement(
                req_id=req_id,
                schedule=raw.get('schedule'),
                bucket=bucket,
                text=raw.get('text', ''),
                expected=raw.get('expected', ''),
                is_gating=is_gating,
                source_ref=SourceRef(file=source_file, page=source_page)
            )
            requirements.append(req)
        except Exception as e:
            logger.warning(f"Failed to parse requirement: {raw} — {e}")
    
    logger.info(f"Total requirements extracted: {len(requirements)} "
                f"(technical={sum(1 for r in requirements if r.bucket == RequirementBucket.TECHNICAL)}, "
                f"eligibility={sum(1 for r in requirements if r.bucket == RequirementBucket.ELIGIBILITY)}, "
                f"commercial={sum(1 for r in requirements if r.bucket == RequirementBucket.COMMERCIAL)})")
    
    # Build RequirementList
    req_list = RequirementList(
        requirements=requirements,
        tender_file=tender_bundle.pages[0].file if tender_bundle.pages else 'unknown',
        extraction_timestamp=datetime.utcnow()
    )
    
    # Save intermediate
    output_path = INTERMEDIATES_DIR / 'requirements.json'
    req_list.to_json_file(output_path)
    
    # Also save schedule structure and opening date
    meta_path = INTERMEDIATES_DIR / 'tender_meta.json'
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump({
            'schedules': schedules,
            'tender_opening_date': opening_date,
            'total_requirements': len(requirements),
            'extraction_timestamp': datetime.utcnow().isoformat()
        }, f, indent=2, ensure_ascii=False)
    
    return req_list
