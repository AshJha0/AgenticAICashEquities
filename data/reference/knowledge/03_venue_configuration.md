# Venue Configuration

## Eligible venues

| Venue | Type | Default routing weight | Notes |
|-------|------|------------------------|-------|
| XNAS  | Lit primary | 34% | Primary listing venue for the US large-cap universe |
| ARCA  | Lit | 22% | Strong at the open and close; timeout set to 250 ms |
| BATS  | Lit | 20% | Inverted fee schedule for passive flow |
| IEX   | Lit (speed bump) | 12% | 350 microsecond speed bump; used for passive slices |
| DARK1 | Dark mid-point | 12% | Minimum quantity 100; no pre-trade transparency |

## Smart-order-router behaviour

The SOR ranks venues by expected fill probability and cost. A venue whose fill rate drops below 60% of its
peers for more than ten minutes is automatically down-weighted; this down-weighting requires the router
health-check to be enabled. Cancel-on-timeout ratios above 20% at a venue generate a WARN log line from the
smart-order-router service.

## Known issues

ARCA has experienced intermittent acknowledgement delays during venue-side maintenance windows; symptoms are
lower fill rates and 3-5 bps of additional slippage while other venues remain normal.
