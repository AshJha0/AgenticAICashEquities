# VWAP Execution Algorithm Runbook

## Purpose

The VWAP strategy (version vwap-3.4.0) schedules a parent order across a user-defined horizon so that the execution
VWAP tracks the interval market VWAP. It is the default strategy for orders below 5% of expected interval volume.

## Configuration in force

- Slice interval: 30 seconds; slice size follows the intraday volume profile with ±20% randomisation.
- Target participation rate: 8% of interval volume.
- Maximum participation rate: 20%. Above this ceiling the algorithm is expected to under-fill rather than chase.
- Limit offset: 2 ticks through the far touch for aggressive slices; passive slices rest at the near touch.
- Venue selection: delegated to the smart-order-router (SOR); dark pool DARK1 is enabled for mid-point liquidity.
- Urgency: NORMAL unless overridden by the trader.

## Expected behaviour under stress

When quoted spreads widen beyond 2x their 30-day median the algorithm shifts towards passive slices, which lowers
the fill rate and raises slippage against arrival while keeping slippage against interval VWAP small.
When displayed depth falls the algorithm keeps the slice size, so per-slice impact rises: expect 1-3 ticks of
additional price concession per slice.

## Operational checks

1. Confirm the parent order horizon and the target quantity.
2. Confirm the participation rate is below the 20% ceiling; orders above 4x the usual size need trader approval.
3. If child rejects exceed 5% of slices escalate to the execution-platform on-call (see incident runbook).
