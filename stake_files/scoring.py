"""
Institutional Consensus Scoring Engine
--------------------------------------
Pure, deterministic scoring. No network, no side effects.
Mirrors the Excel workbook formulas exactly.

All thresholds are class attributes so you can tune them in one place.
"""
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class QuarterData:
    """One quarter's shareholding snapshot for a stock."""
    fii_pct: float
    dii_pct: float
    promoter_pct: float
    fii_investor_count: Optional[int] = None
    mf_scheme_count: Optional[int] = None


@dataclass
class ConsensusResult:
    raw_score: float
    persistence: float
    final_score: float
    classification: str
    net_change: float
    fii_change: float
    dii_change: float
    promoter_change: float
    components: dict
    breakdown: str


class ConsensusScorer:
    # ---- Tunable thresholds (one place to change them) ----
    NET_STRONG = 0.50        # |ΔNet| above this counts as strong directional flow
    DEAD_BAND = 0.20         # moves inside ±this are treated as flat
    ACC_CUTOFF = 3.0         # final score >= this -> ACCUMULATION
    DIST_CUTOFF = -3.0       # final score <= this -> DISTRIBUTION

    def _component_net_direction(self, net: float) -> int:
        if net > self.NET_STRONG:
            return 2
        if net < -self.NET_STRONG:
            return -2
        return 0

    def _component_agreement(self, d_fii: float, d_dii: float) -> int:
        both_buy = d_fii > self.DEAD_BAND and d_dii > self.DEAD_BAND
        both_sell = d_fii < -self.DEAD_BAND and d_dii < -self.DEAD_BAND
        if both_buy:
            return 1
        if both_sell:
            return -1
        return 0  # divergence or flat

    def _component_breadth(self, q1: QuarterData, q0: QuarterData) -> int:
        # Needs both counts in both quarters; otherwise neutral
        if None in (q1.fii_investor_count, q0.fii_investor_count,
                    q1.mf_scheme_count, q0.mf_scheme_count):
            return 0
        fii_up = q0.fii_investor_count > q1.fii_investor_count
        mf_up = q0.mf_scheme_count > q1.mf_scheme_count
        fii_dn = q0.fii_investor_count < q1.fii_investor_count
        mf_dn = q0.mf_scheme_count < q1.mf_scheme_count
        if fii_up and mf_up:
            return 1
        if fii_dn and mf_dn:
            return -1
        return 0

    def _component_promoter(self, d_prom: float, pledge_rising: bool = False) -> int:
        if pledge_rising or d_prom < 0:
            return -2
        if d_prom > 0:
            return 1
        return 0

    def _persistence(self, latest_net: float, prior_net: float) -> float:
        same_sign = (latest_net > 0 and prior_net > 0) or (latest_net < 0 and prior_net < 0)
        if same_sign:
            return 1.5
        # signs differ and both non-zero -> trend flipped
        if latest_net != 0 and prior_net != 0:
            return 0.5
        # no prior trend to confirm
        if prior_net == 0:
            return 0.5
        return 1.0

    def score(self, q2: QuarterData, q1: QuarterData, q0: QuarterData,
              pledge_rising: bool = False) -> ConsensusResult:
        """
        q2 = oldest (Q-2), q1 = previous (Q-1), q0 = latest (Q0).
        """
        d_fii = q0.fii_pct - q1.fii_pct
        d_dii = q0.dii_pct - q1.dii_pct
        d_prom = q0.promoter_pct - q1.promoter_pct
        net = d_fii + d_dii

        prior_net = (q1.fii_pct - q2.fii_pct) + (q1.dii_pct - q2.dii_pct)

        c1 = self._component_net_direction(net)
        c2 = self._component_agreement(d_fii, d_dii)
        c3 = self._component_breadth(q1, q0)
        c4 = self._component_promoter(d_prom, pledge_rising)

        raw = c1 + c2 + c3 + c4
        persist = self._persistence(net, prior_net)
        final = raw * persist

        if final >= self.ACC_CUTOFF:
            cls = "ACCUMULATION"
        elif final <= self.DIST_CUTOFF:
            cls = "DISTRIBUTION"
        else:
            cls = "NEUTRAL"

        components = {
            "net_direction": c1,
            "agreement": c2,
            "breadth": c3,
            "promoter": c4,
        }
        agreement_label = (
            "Consensus Buy" if c2 == 1 else
            "Consensus Sell" if c2 == -1 else
            "Divergence/Flat"
        )
        breakdown = (
            f"ΔFII={d_fii:+.2f}, ΔDII={d_dii:+.2f}, ΔNet={net:+.2f} ({agreement_label}); "
            f"net_dir={c1:+d}, agreement={c2:+d}, breadth={c3:+d}, promoter={c4:+d} "
            f"-> raw={raw:+.1f} x persistence={persist} = {final:+.1f}"
        )

        return ConsensusResult(
            raw_score=raw, persistence=persist, final_score=final,
            classification=cls, net_change=round(net, 2),
            fii_change=round(d_fii, 2), dii_change=round(d_dii, 2),
            promoter_change=round(d_prom, 2),
            components=components, breakdown=breakdown,
        )


# ---- Weekly overlay logic ----
def apply_weekly_overlay(quarterly_classification: str,
                         weekly_fii_dii_signal: str,
                         bulk_deal_signal: str,
                         monthly_mf_signal: str) -> dict:
    """
    Downgrade quarterly call to NEUTRAL if any faster feed contradicts it.
    Signals expected as: 'confirms', 'contradicts', or 'neutral'.
    """
    signals = [weekly_fii_dii_signal, bulk_deal_signal, monthly_mf_signal]
    contradicts = any(s == "contradicts" for s in signals)
    confirms = sum(1 for s in signals if s == "confirms")

    if quarterly_classification == "NEUTRAL":
        final_call = "NEUTRAL"
        note = "No quarterly edge; rely on technicals."
    elif contradicts:
        final_call = "NEUTRAL (wait)"
        note = "Faster data contradicts quarterly score — wait for confirmation."
    elif confirms >= 1:
        final_call = quarterly_classification
        note = f"Confirmed by {confirms} faster signal(s)."
    else:
        final_call = quarterly_classification
        note = "Quarterly score stands; no contradicting fast data."

    return {"final_call": final_call, "note": note}
