---
version: 1.1
effective_date: 2026-11-08
classification: internal
---

# Attribution Threshold Tables

## Why these deterioration thresholds

An investigation only looks for causes when the window has deteriorated against its baseline: implementation
shortfall up by at least 1.5 bps. The threshold sits just above the noise of a one-hour VWAP-slippage estimate on
a liquid name, so ordinary hour-to-hour variation does not trigger an attribution, while the 5 bps escalation
level in the execution policy is still caught with margin. Thresholds are a ratio against the baseline window
so that one table works across names of different volatility and spread. Since 0.5.0 the window must also be
statistically worse than expected: the residual against the pre-trade estimate must reach z 1.96, where the standard
error pools the model's execution noise (weight 4 pseudo-observations) with the dispersion of matched peers.

## Cause threshold table

The values below are the defaults of `ceap.analytics.attribution.Thresholds`; a unit test keeps this table and
the code in step.

| Cause | Metric | Threshold |
|-------|--------|-----------|
| Deterioration gate (fallback without a pre-trade estimate) | IS increase versus baseline | 1.5 bps |
| Deterioration gate | residual z against the pre-trade estimate | 1.96 |
| Deterioration gate | prior weight of model noise pooled with matched peers (pseudo-observations) | 4.0 |
| Market volatility | realised volatility ratio | 1.6x |
| Wide spreads | quoted spread ratio | 1.4x |
| Low liquidity | displayed depth ratio | below 0.65x |
| Venue degradation | fill-rate gap to peers | 0.25 |
| Venue degradation | slippage excess at a venue | 2.0 bps |
| Technology latency | gateway latency ratio | 3.0x |
| Technology latency | child reject rate | 0.05 |
| Market-data anomaly | stale quote fraction | 0.03 |
| Market-data anomaly | crossed quotes in window | 5 |
| Large-order impact | participation rate | 0.20 |
| Large-order impact | parent size ratio to usual | 4.0x |
| Unexpected price movement | adverse drift | 60 bps |

## Changing a threshold

A threshold change is a methodology change: it needs a new version of this document, a new effective date, a
CHANGELOG entry and a re-run of the attribution evaluation. Reports record the knowledge corpus hash, so a report
can always be traced to the threshold table that was in force.
