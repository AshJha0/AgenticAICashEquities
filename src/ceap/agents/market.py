"""Market agent: what happened in the market?"""

from __future__ import annotations

from ceap.agents.base import BaseAgent, confidence_from_excess, finite, ratio
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.findings import Finding


class MarketAgent(BaseAgent):
    agent_id = "market"

    async def execute(self, context: AgentContext) -> AgentResult:
        w = self.window(context)
        win, win_ev = await self.ensure(context, "market_data.get_market_statistics", w.window_args)
        base, base_ev = await self.ensure(context, "market_data.get_market_statistics", w.baseline_args)
        book_w, book_w_ev = await self.ensure(context, "market_data.get_order_book_statistics", w.window_args)
        book_b, book_b_ev = await self.ensure(
            context, "market_data.get_order_book_statistics", w.baseline_args
        )

        vol_ratio = ratio(win.get("realised_volatility_bps"), base.get("realised_volatility_bps"))
        spread_ratio = ratio(win.get("average_spread_bps"), base.get("average_spread_bps"))
        depth_ratio = ratio(book_w.get("average_displayed_depth"), book_b.get("average_displayed_depth"))
        tob_ratio = ratio(win.get("average_top_of_book_size"), base.get("average_top_of_book_size"))
        drift = win.get("price_drift_bps")
        stale = win.get("stale_quote_fraction", 0.0) or 0.0
        crossed = win.get("crossed_quote_count", 0) or 0

        calc = self.calc_evidence(
            context,
            "Window vs baseline market ratios (volatility, spread, depth, top-of-book)",
            {
                "volatility_ratio": vol_ratio,
                "spread_ratio": spread_ratio,
                "depth_ratio": depth_ratio,
                "top_of_book_ratio": tob_ratio,
                "price_drift_bps": drift,
                "stale_quote_fraction": stale,
                "crossed_quotes": crossed,
            },
            (*win_ev, *base_ev, *book_w_ev, *book_b_ev),
        )
        ev = (*win_ev, *base_ev, calc.id)
        book_ev = (*book_w_ev, *book_b_ev, calc.id)
        findings: list[Finding] = []

        def add(statement: str, conf: float, evidence: tuple[str, ...], **attrs: object) -> None:
            findings.append(
                Finding.create(
                    statement, evidence, conf, category="MARKET", produced_by=self.id, attributes=dict(attrs)
                )
            )

        if finite(vol_ratio):
            if vol_ratio >= 1.6:
                add(
                    f"Realised volatility rose to {win['realised_volatility_bps']:.2f} bps/min, {vol_ratio:.1f}x the baseline.",
                    confidence_from_excess(vol_ratio - 1.6, 1.0),
                    ev,
                    volatility_ratio=vol_ratio,
                    anomaly="MARKET_VOLATILITY",
                )
            else:
                add(
                    f"Realised volatility ({win['realised_volatility_bps']:.2f} bps/min) was in line with the baseline ({vol_ratio:.2f}x).",
                    0.8,
                    ev,
                    volatility_ratio=vol_ratio,
                )
        if finite(spread_ratio):
            if spread_ratio >= 1.4:
                add(
                    f"Average quoted spread widened to {win['average_spread_bps']:.2f} bps, {spread_ratio:.1f}x the baseline.",
                    confidence_from_excess(spread_ratio - 1.4, 0.6),
                    ev,
                    spread_ratio=spread_ratio,
                    anomaly="WIDE_SPREADS",
                )
            else:
                add(
                    f"Average quoted spread ({win['average_spread_bps']:.2f} bps) was in line with the baseline ({spread_ratio:.2f}x).",
                    0.8,
                    ev,
                    spread_ratio=spread_ratio,
                )
        liq = (
            min(x for x in (depth_ratio, tob_ratio) if finite(x))
            if any(finite(x) for x in (depth_ratio, tob_ratio))
            else float("nan")
        )
        if finite(liq):
            if liq <= 0.65:
                add(
                    f"Displayed liquidity fell to {liq:.0%} of the baseline (order-book depth ratio {depth_ratio:.2f}, top-of-book ratio {tob_ratio:.2f}).",
                    confidence_from_excess(0.65 - liq, 0.2),
                    book_ev,
                    depth_ratio=depth_ratio,
                    anomaly="LOW_LIQUIDITY",
                )
            else:
                add(
                    f"Displayed liquidity was normal ({liq:.0%} of baseline depth).",
                    0.8,
                    book_ev,
                    depth_ratio=depth_ratio,
                )
        if finite(drift) and abs(drift) >= 60:
            add(
                f"The mid price drifted {drift:+.0f} bps across the window (open {win['open_mid']:.2f}, close {win['close_mid']:.2f}).",
                confidence_from_excess(abs(drift) - 60, 40),
                ev,
                price_drift_bps=drift,
                anomaly="PRICE_MOVEMENT",
            )
        if stale >= 0.03 or crossed >= 5:
            add(
                f"Market-data quality degraded: {stale:.1%} stale quotes and {crossed} crossed quotes in the window.",
                confidence_from_excess(max(stale - 0.03, (crossed - 5) / 100), 0.05),
                ev,
                stale_quote_fraction=stale,
                crossed_quotes=crossed,
                anomaly="MARKET_DATA_ANOMALY",
            )
        else:
            add(
                "Market-data feed quality was normal (no stale runs or crossed quotes of note).",
                0.75,
                ev,
                stale_quote_fraction=stale,
            )

        return AgentResult(
            agent_id=self.id,
            success=True,
            findings=tuple(findings),
            output={
                "window": win,
                "baseline": base,
                "order_book": {"window": book_w, "baseline": book_b},
                "ratios": {
                    "volatility": vol_ratio,
                    "spread": spread_ratio,
                    "depth": depth_ratio,
                    "top_of_book": tob_ratio,
                },
            },
            summary=f"vol x{vol_ratio:.2f}, spread x{spread_ratio:.2f}, depth x{depth_ratio:.2f}",
        )
