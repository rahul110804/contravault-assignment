"""Engine 3: Price ranking — numeric, per schedule.

Ranks qualified vendors by price for each schedule independently.
Never uses prior_contract_price. Never calls the LLM.
"""
import logging
from typing import Any

from models.scoring import (
    PriceRank, PriceRankStatus, SchedulePriceRanking
)
from models.scoring import EligibilityVerdict, EligibilityStatus

logger = logging.getLogger(__name__)


def rank_prices(
    eligibility_verdicts: dict[str, EligibilityVerdict],
    quotes: dict[str, dict[str, float | None]],
    schedules: list[str],
    apply_h1_elimination: bool = True
) -> list[SchedulePriceRanking]:
    """Rank qualified vendors by price for each schedule.
    
    This function is PURELY NUMERIC — no LLM calls.
    Only uses explicitly supplied current quotes.
    
    Args:
        eligibility_verdicts: Dict of vendor_id -> EligibilityVerdict
        quotes: Dict of vendor_id -> {schedule: price}.
                Price is None if not quoted.
                An empty dict means no quotes supplied at all.
        schedules: List of schedule identifiers to rank
        apply_h1_elimination: Whether to apply H1 (highest price) elimination
    
    Returns:
        List of SchedulePriceRanking, one per schedule
    """
    results = []
    no_quotes_at_all = not quotes or all(
        not vendor_quotes for vendor_quotes in quotes.values()
    )
    
    for schedule in schedules:
        rankings = []
        
        if no_quotes_at_all:
            # No quotes supplied at all — awaiting quotes state
            for vendor_id in eligibility_verdicts:
                verdict = eligibility_verdicts[vendor_id]
                if verdict.eligibility == EligibilityStatus.DISQUALIFIED:
                    status = PriceRankStatus.DISQUALIFIED
                else:
                    status = PriceRankStatus.AWAITING_QUOTES
                
                rankings.append(PriceRank(
                    schedule=schedule,
                    vendor_id=vendor_id,
                    price=None,
                    rank=None,
                    status=status
                ))
            
            results.append(SchedulePriceRanking(
                schedule=schedule,
                rankings=rankings,
                h1_vendor=None
            ))
            continue
        
        # Separate qualified vendors with quotes from others
        quotable_vendors = []  # (vendor_id, price)
        
        for vendor_id, verdict in eligibility_verdicts.items():
            if verdict.eligibility == EligibilityStatus.DISQUALIFIED:
                rankings.append(PriceRank(
                    schedule=schedule,
                    vendor_id=vendor_id,
                    price=None,
                    rank=None,
                    status=PriceRankStatus.DISQUALIFIED
                ))
                continue
            
            vendor_quotes = quotes.get(vendor_id, {})
            price = vendor_quotes.get(schedule)
            
            if price is None:
                rankings.append(PriceRank(
                    schedule=schedule,
                    vendor_id=vendor_id,
                    price=None,
                    rank=None,
                    status=PriceRankStatus.PENDING_QUOTE
                ))
            else:
                quotable_vendors.append((vendor_id, price))
        
        # Sort by price ascending
        quotable_vendors.sort(key=lambda x: x[1])
        
        # Identify H1 (highest-priced bidder)
        h1_vendor = None
        if apply_h1_elimination and len(quotable_vendors) > 1:
            h1_vendor = quotable_vendors[-1][0]  # Last = highest price
        
        # Assign ranks
        rank = 1
        for vendor_id, price in quotable_vendors:
            is_h1 = (vendor_id == h1_vendor)
            rankings.append(PriceRank(
                schedule=schedule,
                vendor_id=vendor_id,
                price=price,
                rank=rank,
                status=PriceRankStatus.RANKED,
                h1_eliminated=is_h1
            ))
            rank += 1
        
        results.append(SchedulePriceRanking(
            schedule=schedule,
            rankings=rankings,
            h1_vendor=h1_vendor
        ))
        
        logger.info(
            f"Schedule '{schedule}': {len(quotable_vendors)} quoted vendors, "
            f"H1={'none' if not h1_vendor else h1_vendor}"
        )
    
    return results
