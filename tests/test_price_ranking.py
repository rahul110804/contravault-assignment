"""Tests for Engine 3: Price ranking (numeric, per schedule)."""
import pytest

from models.scoring import (
    EligibilityVerdict, EligibilityStatus, GatingCheckResult,
    PriceRankStatus, SchedulePriceRanking
)
from models.requirement import SourceRef
from pipeline.engine_price import rank_prices


def _make_verdict(vendor_id, status):
    return EligibilityVerdict(
        vendor_id=vendor_id,
        eligibility=status,
        failed_conditions=[] if status == EligibilityStatus.QUALIFIED else ["Failed check"],
        checks=[]
    )


class TestPriceRanking:
    """Test the numeric price ranking engine."""
    
    def test_correct_l1_l2_ordering(self):
        """Vendors should be ranked L1 (lowest) to L2 (highest) per schedule."""
        verdicts = {
            "vendor_a": _make_verdict("vendor_a", EligibilityStatus.QUALIFIED),
            "vendor_b": _make_verdict("vendor_b", EligibilityStatus.QUALIFIED),
        }
        quotes = {
            "vendor_a": {"Schedule 1": 15000.0},
            "vendor_b": {"Schedule 1": 12000.0},
        }
        
        results = rank_prices(verdicts, quotes, ["Schedule 1"], apply_h1_elimination=False)
        assert len(results) == 1
        
        rankings = {r.vendor_id: r for r in results[0].rankings}
        assert rankings["vendor_b"].rank == 1  # L1 (lower price)
        assert rankings["vendor_a"].rank == 2  # L2
    
    def test_disqualified_vendor_excluded(self):
        """Disqualified vendors should not be ranked."""
        verdicts = {
            "vendor_a": _make_verdict("vendor_a", EligibilityStatus.QUALIFIED),
            "vendor_b": _make_verdict("vendor_b", EligibilityStatus.DISQUALIFIED),
        }
        quotes = {
            "vendor_a": {"Schedule 1": 15000.0},
            "vendor_b": {"Schedule 1": 12000.0},  # Lower but disqualified
        }
        
        results = rank_prices(verdicts, quotes, ["Schedule 1"])
        rankings = {r.vendor_id: r for r in results[0].rankings}
        
        assert rankings["vendor_a"].status == PriceRankStatus.RANKED
        assert rankings["vendor_b"].status == PriceRankStatus.DISQUALIFIED
        assert rankings["vendor_b"].rank is None
    
    def test_missing_quote_shows_pending(self):
        """Vendor without quote for a schedule should show 'pending'."""
        verdicts = {
            "vendor_a": _make_verdict("vendor_a", EligibilityStatus.QUALIFIED),
            "vendor_b": _make_verdict("vendor_b", EligibilityStatus.QUALIFIED),
        }
        quotes = {
            "vendor_a": {},  # No quotes for any schedule
            "vendor_b": {"Schedule 1": 1000.0},
        }
        
        results = rank_prices(verdicts, quotes, ["Schedule 1"])
        rankings = results[0].rankings
        
        rankings_by_vendor = {r.vendor_id: r for r in results[0].rankings}
        assert rankings_by_vendor["vendor_a"].status == PriceRankStatus.PENDING_QUOTE
        assert rankings_by_vendor["vendor_a"].rank is None
    
    def test_no_quotes_returns_awaiting_state(self):
        """With no quotes at all, return 'awaiting quotes' state."""
        verdicts = {
            "vendor_a": _make_verdict("vendor_a", EligibilityStatus.QUALIFIED),
            "vendor_b": _make_verdict("vendor_b", EligibilityStatus.QUALIFIED),
        }
        quotes = {}  # Empty — no quotes supplied
        
        results = rank_prices(verdicts, quotes, ["Schedule 1"])
        
        for ranking in results[0].rankings:
            assert ranking.status == PriceRankStatus.AWAITING_QUOTES
            assert ranking.rank is None
    
    def test_h1_elimination(self):
        """Highest-priced vendor (H1) should be marked for elimination."""
        verdicts = {
            "vendor_a": _make_verdict("vendor_a", EligibilityStatus.QUALIFIED),
            "vendor_b": _make_verdict("vendor_b", EligibilityStatus.QUALIFIED),
            "vendor_c": _make_verdict("vendor_c", EligibilityStatus.QUALIFIED),
        }
        quotes = {
            "vendor_a": {"Schedule 1": 12000.0},
            "vendor_b": {"Schedule 1": 15000.0},
            "vendor_c": {"Schedule 1": 20000.0},  # Highest = H1
        }
        
        results = rank_prices(verdicts, quotes, ["Schedule 1"], apply_h1_elimination=True)
        rankings = {r.vendor_id: r for r in results[0].rankings}
        
        assert rankings["vendor_c"].h1_eliminated is True
        assert rankings["vendor_a"].h1_eliminated is False
        assert results[0].h1_vendor == "vendor_c"
    
    def test_per_schedule_independent_ranking(self):
        """Rankings should be independent per schedule."""
        verdicts = {
            "vendor_a": _make_verdict("vendor_a", EligibilityStatus.QUALIFIED),
            "vendor_b": _make_verdict("vendor_b", EligibilityStatus.QUALIFIED),
        }
        quotes = {
            "vendor_a": {"Schedule 1": 10000.0, "Schedule 2": 20000.0},
            "vendor_b": {"Schedule 1": 15000.0, "Schedule 2": 18000.0},
        }
        
        results = rank_prices(verdicts, quotes, ["Schedule 1", "Schedule 2"], apply_h1_elimination=False)
        
        s1_rankings = {r.vendor_id: r for r in results[0].rankings}
        s2_rankings = {r.vendor_id: r for r in results[1].rankings}
        
        assert s1_rankings["vendor_a"].rank == 1  # vendor_a L1 for S1
        assert s2_rankings["vendor_b"].rank == 1  # vendor_b L1 for S2
