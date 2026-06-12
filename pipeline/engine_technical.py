"""Engine 1: Technical compliance scoring using LLM + retrieval.

Scores each technical requirement for each vendor per schedule.
Uses vector retrieval to find relevant evidence, then LLM to assess compliance.
"""
import json
import logging
from pathlib import Path

from models.requirement import Requirement, RequirementBucket, RequirementList, SourceRef
from models.evidence import VendorEvidence, VendorEvidenceBundle, EvidenceType
from models.scoring import ComplianceScore, ComplianceLevel
from retrieval.vector_store import VendorVectorStore
from llm.client import GroqClient
from config import INTERMEDIATES_DIR

logger = logging.getLogger(__name__)

SCORING_SYSTEM_PROMPT = """You are a technical compliance assessor for government procurement.
You evaluate whether a vendor's evidence meets a specific tender requirement.

You MUST return a JSON object with:
- compliance: "full" | "partial" | "missing" | "not_addressed"
- score: integer 0-100
- reason: string (MANDATORY — a specific sentence citing the evidence. Never emit a bare score.)

Scoring guide:
- full (80-100): Evidence clearly and fully satisfies the requirement
- partial (30-79): Evidence partially addresses the requirement but has gaps
- missing (1-29): Requirement is relevant but vendor's evidence is clearly insufficient
- not_addressed (0): No evidence whatsoever related to this requirement

CRITICAL RULES:
- The reason MUST cite specific evidence (e.g., "Vendor's test report shows tensile strength of 410 MPa, meeting the IS:2713 requirement of ≥410 MPa")
- If a vendor is silent on a schedule/designation, score as 'missing' with reason explaining what's absent
- Score per (vendor × schedule × requirement) — never average across schedules
- A vendor quoting SP-57 and SP-80 but silent on SP-72 MUST be scored 'missing' for SP-72 requirements
- Be precise about what evidence exists vs what's required
"""

SCORING_PROMPT_TEMPLATE = """Evaluate this vendor's compliance with the following tender requirement.

TENDER REQUIREMENT:
- ID: {req_id}
- Schedule: {schedule}
- Requirement: {requirement_text}
- Expected: {expected}

VENDOR: {vendor_id}

RELEVANT VENDOR EVIDENCE (retrieved from their documents):
{evidence_text}

Assess compliance and return JSON:
{{
  "compliance": "full" | "partial" | "missing" | "not_addressed",
  "score": 0-100,
  "reason": "Specific sentence citing evidence..."
}}
"""


def _format_evidence(evidence_items: list[dict]) -> str:
    """Format retrieved evidence items into a readable text block."""
    if not evidence_items:
        return "NO EVIDENCE FOUND — vendor documents contain no information relevant to this requirement."
    
    lines = []
    for i, item in enumerate(evidence_items, 1):
        meta = item.get('metadata', {})
        lines.append(
            f"{i}. {item.get('document', 'N/A')}\n"
            f"   Source: {meta.get('source_file', '?')} p.{meta.get('source_page', '?')}\n"
            f"   Type: {meta.get('evidence_type', '?')}"
        )
    return "\n".join(lines)


def score_technical(
    requirements: RequirementList,
    vendor_evidence: dict[str, VendorEvidenceBundle],
    vector_store: VendorVectorStore,
    llm: GroqClient,
    schedules: list[str] | None = None
) -> list[ComplianceScore]:
    """Score technical compliance for all vendors against all technical requirements.
    
    For each (vendor, schedule, technical_requirement), retrieves relevant evidence
    from the vector store and uses LLM to assess compliance.
    
    Args:
        requirements: Extracted tender requirements
        vendor_evidence: Dict mapping vendor_id to their evidence bundle
        vector_store: Populated vector store with indexed evidence
        llm: Gemini LLM client
        schedules: Optional list of schedules to score (if None, derive from requirements)
    
    Returns:
        List of ComplianceScore objects
    """
    # Get technical requirements
    tech_reqs = requirements.by_bucket(RequirementBucket.TECHNICAL)
    logger.info(f"Scoring {len(tech_reqs)} technical requirements for {len(vendor_evidence)} vendors")
    
    # Derive schedules from requirements if not provided
    if schedules is None:
        schedules = list(set(
            r.schedule for r in tech_reqs if r.schedule is not None
        ))
        if not schedules:
            schedules = [None]  # If no schedules, treat as single evaluation
    
    all_scores = []
    
    for vendor_id in vendor_evidence:
        logger.info(f"Scoring vendor: {vendor_id}")
        
        for req in tech_reqs:
            # Determine which schedules this requirement applies to
            if req.schedule is not None:
                applicable_schedules = [req.schedule]
            else:
                applicable_schedules = schedules
            
            for schedule in applicable_schedules:
                schedule_str = schedule or "all"
                
                # Retrieve relevant evidence
                query = f"{req.text} {schedule_str}"
                evidence_items = vector_store.retrieve_relevant(
                    vendor_id, query, top_k=5
                )
                
                evidence_text = _format_evidence(evidence_items)
                
                # Determine evidence source ref
                evidence_source_ref = None
                if evidence_items and evidence_items[0].get('metadata'):
                    meta = evidence_items[0]['metadata']
                    evidence_source_ref = SourceRef(
                        file=meta.get('source_file', 'unknown'),
                        page=int(meta.get('source_page', 0))
                    )
                
                if not evidence_items:
                    # No evidence at all — score immediately without LLM
                    score = ComplianceScore(
                        vendor_id=vendor_id,
                        schedule=schedule_str,
                        req_id=req.req_id,
                        compliance=ComplianceLevel.NOT_ADDRESSED,
                        score=0,
                        reason=f"No evidence found in vendor '{vendor_id}' documents addressing: {req.text}",
                        requirement_source_ref=req.source_ref,
                        evidence_source_ref=None
                    )
                else:
                    # Use LLM to assess
                    prompt = SCORING_PROMPT_TEMPLATE.format(
                        req_id=req.req_id,
                        schedule=schedule_str,
                        requirement_text=req.text,
                        expected=req.expected,
                        vendor_id=vendor_id,
                        evidence_text=evidence_text
                    )
                    
                    try:
                        result = llm.generate_json(
                            prompt,
                            system_instruction=SCORING_SYSTEM_PROMPT
                        )
                        
                        # Parse compliance level
                        compliance_str = result.get('compliance', 'not_addressed')
                        try:
                            compliance = ComplianceLevel(compliance_str)
                        except ValueError:
                            compliance = ComplianceLevel.NOT_ADDRESSED
                        
                        raw_score = result.get('score', 0)
                        raw_score = max(0, min(100, int(raw_score)))
                        
                        reason = result.get('reason', 'No reason provided by assessment')
                        if len(reason) < 10:
                            reason = f"Assessment result for {req.req_id}: {reason}"
                        
                        score = ComplianceScore(
                            vendor_id=vendor_id,
                            schedule=schedule_str,
                            req_id=req.req_id,
                            compliance=compliance,
                            score=raw_score,
                            reason=reason,
                            requirement_source_ref=req.source_ref,
                            evidence_source_ref=evidence_source_ref
                        )
                    except Exception as e:
                        logger.warning(
                            f"LLM scoring failed for {vendor_id}/{schedule_str}/{req.req_id}: {e}"
                        )
                        score = ComplianceScore(
                            vendor_id=vendor_id,
                            schedule=schedule_str,
                            req_id=req.req_id,
                            compliance=ComplianceLevel.NOT_ADDRESSED,
                            score=0,
                            reason=f"Scoring failed due to an error: {str(e)[:100]}",
                            requirement_source_ref=req.source_ref,
                            evidence_source_ref=evidence_source_ref
                        )
                
                all_scores.append(score)
                logger.debug(
                    f"  {vendor_id}/{schedule_str}/{req.req_id}: "
                    f"{score.compliance.value} ({score.score})"
                )
    
    # Save intermediate
    output_path = INTERMEDIATES_DIR / 'technical_scores.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(
            [s.model_dump(mode='json') for s in all_scores],
            f, indent=2, ensure_ascii=False
        )
    logger.info(f"Saved {len(all_scores)} technical scores to {output_path}")
    
    return all_scores
