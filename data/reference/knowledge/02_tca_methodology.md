# TCA Methodology

## Benchmarks

- Arrival price: mid-quote at the time the parent order is accepted (the decision price).
- Interval VWAP: volume-weighted average price of all market prints between the parent's start and end time,
  including our own fills.
- Implementation shortfall (IS): executed cost minus decision price times target quantity, plus the opportunity
  cost of any unfilled quantity priced at the closing mid of the horizon. Normalised to basis points of the
  decision-price notional. Positive IS is always adverse to the trader regardless of side.
- Slippage versus VWAP: execution VWAP minus interval VWAP in bps, signed so that positive is adverse.

## Decomposition

Market impact is split into a permanent component (mid at last fill versus arrival mid) and a temporary component
(execution VWAP versus mid at last fill). Spread cost is measured as the effective spread: twice the signed
distance between the fill price and the prevailing mid.

## Baseline comparison

An investigation window is always compared with a baseline window of equal length immediately before it. A
change in IS of at least 1.5 bps, or a change in VWAP slippage of at least 1.5 bps, is considered a deterioration
worth attributing. Because hourly IS carries roughly 30 bps of price-drift noise on a 10% volatility name, VWAP
slippage is the preferred benchmark for judging a VWAP algorithm.

## Attribution thresholds

- Volatility: realised one-minute volatility more than 1.6x the baseline.
- Spreads: average quoted spread more than 1.4x the baseline.
- Liquidity: displayed depth below 65% of the baseline.
- Venue: a venue whose fill rate is 25 percentage points below its peers or whose slippage is 2 bps worse.
- Technology: order-gateway latency more than 3x the baseline or a reject rate above 5%.
- Market data: more than 3% stale quotes or more than 5 crossed quotes in the window.
- Order size: participation above 20%, especially with a parent 4x larger than usual.
- Price movement: an adverse drift of more than 60 bps that is not explained by elevated volatility.
