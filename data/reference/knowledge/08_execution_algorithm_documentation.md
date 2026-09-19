# Execution Algorithm Documentation

## Strategies

- VWAP: tracks interval VWAP using the historical volume profile (default).
- TWAP: evenly spaced slices, used for illiquid names or when the volume profile is unreliable.
- IS (Implementation Shortfall): front-loads execution to minimise arrival-price risk at the cost of impact.
- POV (Percentage of Volume): follows realised volume at a fixed participation rate.

## Child order lifecycle

A child order is created by the scheduler, priced by the pricing module, routed by the smart-order-router and
acknowledged by the venue. The acknowledgement latency is measured at the order-gateway. Normal ack latency is
around 800 microseconds at p50. Child orders that are not filled within their lifetime are cancelled; fills are
reported as executions carrying the venue, price, quantity and latency.

## Reading a report

Execution VWAP above interval VWAP for a buy means we paid more than the market average; the gap is the VWAP
slippage. Implementation shortfall against arrival includes price drift, so a large IS with small VWAP
slippage usually indicates a market move rather than poor scheduling.
