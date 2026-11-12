# Execution: the book, impact, schedulers and cost loops (WS-F, 0.8.0)

Up to 0.7.1 CEAP could measure an execution after the fact (post-trade TCA) but could not run
one. Its synthetic market also handed out passive fills with a fixed probability and planted
market impact as a drift decided in advance. The package `ceap.execution` adds the missing half:
a limit order book in which fills and impact are caused by the orders themselves, the standard
schedulers, child-order logic, an auction model, and the loops that compare what a model
predicted with what happened.

Everything here is synthetic and deterministic in its seed. Nothing sends an order anywhere.

Each section below gives the plain idea, then the formal statement, then a worked example or a
measured result. Every claim carries its evidence class: *published* (a cited paper or rule),
*measured* (run here, with the seed and command), or *design choice* (ours, open to argument).

| Plan step | Module | What it does |
|---|---|---|
| F1 | `ceap.execution.lob` | price-time FIFO book, ms event clock, latency, hidden size, trade-through fills |
| F2 | `ceap.execution.impact` | propagator impact from the bot's own flow; fit and compare with the truth |
| F3 | `ceap.execution.schedule` | TWAP, VWAP, POV, Almgren-Chriss behind one `Schedule` |
| F4 | `ceap.execution.child` | passive or aggressive, I-would, cancel/replace, min qty, randomisation |
| F1-F4 | `ceap.execution.algo` | runs a parent through the book and scores it with the TCA's Perold IS |
| F5 | `ceap.execution.auction` | auction uncross, imbalance, indicative price, endogenous auction impact |
| F6 | `ceap.execution.cost_loop` | predicted vs realised cost in SQLite, recalibration, drift alert, reliability |
| F7 | `ceap.execution.markouts` | markout curves by venue and liquidity flag with bootstrap CIs; routing table |
| F8 | `ceap.analytics.attribution.regression_attribution` | regression cause attribution, behind a flag |
| F12 | `ceap.execution.rules` | tick sizes, round lots, LULD bands, Limit State and pauses |

## 1. The order book (F1)

**Plain idea.** Orders wait in a queue at each price. Whoever arrived first at the best price
trades first. A resting order does not fill because the price touched it; it fills when someone
trades through everything ahead of it in the queue.

**Formal statement.** Prices are integer ticks. Each level is a FIFO queue. An aggressive order of
size `q` with limit `L` consumes the opposite side best level first and, within a level, oldest
first, until `q` is done or the next level is beyond `L`. A new resting order joins the back of
its level, so its queue position equals the displayed size ahead of it (`OrderBook.queue_ahead`).
Icebergs show `peak` shares and refill from a hidden reserve; each refill joins the back of the
queue, so hidden size trades after displayed size at the same price.

The simulator draws background events (limit, market and cancel orders) from a Poisson process in
continuous time, rounded to the millisecond. Limit orders are placed a geometric number of ticks
behind the touch around a latent *efficient price* that diffuses. The bot's orders and cancels
reach the book `entry_latency_ms` after it sends them; the book it sees is `md_latency_ms` old.

**Adverse selection** comes from two places. Background takers lean towards the efficient price,
`P(buy) = 1 / (1 + exp(-kappa (eff - mid) / tick))`, and quotes left more than `stale_ticks` on the
wrong side of the efficient price are pulled. So a resting order tends to fill just before the
price moves through it, as on a real venue.

Evidence class: *published model family* (zero-intelligence books, Smith, Farmer, Gillemot and
Krishnamurthy (2003), Quant. Finance 3(6)) plus an informed-flow tilt; the parameters are
illustrative, *not calibrated to any venue*.

**Worked example** (`tests/unit/execution/test_lob.py`). Asks: 100 then 200 shares at 101, 300 at
102. A buy for 350 fills 100 and 200 at 101, then 50 at 102, in that order. An agent sell of 50
added behind them at 101 has 300 shares ahead; after a taker buys 120, it has 180 ahead.

**Property tested.** Over six seeds, every passive agent fill is at the agent's own limit (no
price improvement for a resting order) and coincides with a background taker trading at that
price; every aggressive fill respects its limit.

## 2. Impact caused by the bot's own trades (F2)

**Plain idea.** When we buy, the price goes up a little; part of that move fades as liquidity comes
back, part stays. Here that is caused by our fills, not planted in advance.

**Formal statement.** A signed trade of `q` shares at time `s` moves the efficient price at
`t >= s` by `(q / unit) * (G_perm + G_temp exp(-(t - s) / tau))` bps. This is the transient-impact
(propagator) model of Bouchaud, Gefen, Potters and Wyart (2004), Quant. Finance 4(2), with the
exponential resilience of Obizhaeva and Wang (2013), J. Financial Markets 16(1), and a permanent
floor. It is linear in volume, so it does not reproduce the concave square-root law in size; that
remains the pre-trade model in `ceap.analytics.market_impact`. Evidence class: *published model,
simplified*.

The fit (`fit_propagator`) is a deconvolved multi-horizon markout. Differencing the model gives
`dy_t = G_perm v_t + G_temp dE_t + noise` with `E_t = a E_{t-1} + v_t`, `a = exp(-dt / tau)`, which
is linear in the two amplitudes for a fixed `tau`, so it is least squares on a grid of decays.
`fit_markout_curve` fits the same shape to a plain markout curve when the flow is not
autocorrelated.

**Measured: recovered parameters against the truth.**

| Data | permanent (bps per 1,000 sh) | temporary | decay (s) |
|---|---|---|---|
| Direct propagator series, truth | 0.40 | 1.20 | 30 |
| ... fit, seed 1 (20,000 s) | 0.38 ± 0.07 | 1.22 ± 0.07 | 28.8 |
| ... fit, seed 3 | 0.39 ± 0.07 | 1.21 ± 0.07 | 28.8 |
| ... fit, seed 2 | 0.25 ± 0.07 | 1.35 ± 0.07 | 35.8 |
| LOB, truth | 2.00 | 6.00 | 30 |
| ... fit on the efficient price, 2 h, seed 3 | 1.87 ± 1.10 | 5.81 ± 1.09 | 44.5 |
| ... fit on the quoted mid, same run | 7.49 ± 0.18 | -2.04 ± 0.15 | 1.0 |

Evidence class: *measured* (`tests/unit/execution/test_impact.py`; the LOB rows are the nightly
test `test_lob_kernel_recovered_on_the_efficient_path`). The standard errors are conditional on
the fitted decay, so they are a little too small: seed 2 misses by about 2 SE in a trade-off
between the permanent and temporary parts.

**What the last row means.** Fitted on the quoted mid, the same run gives a wrong split: the mid
moves in half-tick steps and bounces between bid and ask, which the differenced fit reads as a
large instant impact that reverses within a second. The plain markout curve of that run (5.6 bps
at 0 s, 5.1 at 30 s, 3.7 at 120 s per 1,000 shares) decays the right way but sits above the
kernel. The quoted mid is what a desk observes, so a fitted "permanent impact" from high-frequency
mids should be read with this bias in mind: fit on coarser buckets, or on the markout curve.

## 3. Schedulers (F3)

**Plain idea.** Split the parent over time. TWAP evenly; VWAP in proportion to expected volume;
POV as a fixed share of what the market actually trades; Almgren-Chriss front-loads when the
trader dislikes price risk.

**Formal statement.** Every scheduler gives slice weights summing to one. `allocate` turns them into
whole shares by largest remainder, so the slices always sum exactly to the parent and no slice is
more than one share from its exact pro-rata size (property-tested over 200 random cases).

Almgren and Chriss (2000), J. Risk 3(2), section 3: with `tau = T/N`, `eta_tilde = eta - gamma tau / 2`,
`kappa_tilde^2 = lambda sigma^2 / eta_tilde` and `cosh(kappa tau) = 1 + tau^2 kappa_tilde^2 / 2`,

```
x_j = X sinh(kappa (T - t_j)) / sinh(kappa T)
E   = gamma X^2 / 2 + epsilon X + (eta_tilde / tau) sum n_k^2
V   = sigma^2 tau sum x_k^2
```

`lambda -> 0` gives the straight line (TWAP) and `E = gamma X^2/2 + epsilon X + eta_tilde X^2 / T`.
Evidence class: *published derivation*, re-derived in the known-answer tests with the paper's own
example parameters (X = 10^6, T = 5, sigma = 0.95, eta = 2.5e-6, gamma = 2.5e-7, lambda = 1e-6).
`AlmgrenChriss.frontier` traces expected cost against risk as `lambda` moves.

POV re-targets each slice from the market volume printed so far: `clip(rate V, (rate - band) V,
(rate + band) V)`, capped at the parent.

**Measured: the four schedulers in the book.** A 3,000-share buy over 10 minutes in 10 slices,
default `ChildPolicy`, eight seeds (100-107):

| Schedule | IS mean (bps) | SE | fill rate | passive share |
|---|---|---|---|---|
| TWAP | -1.66 | 5.17 | 1.000 | 0.05 |
| VWAP | -1.58 | 5.15 | 1.000 | 0.05 |
| POV 10 % | -4.74 | 4.46 | 1.000 | 0.09 |
| Almgren-Chriss | -1.66 | 4.95 | 1.000 | 0.06 |

Evidence class: *measured*, eight seeds. **No difference between the schedulers is detectable**:
price noise over ten minutes (about 5 bps of SE) swamps the impact of 3,000 shares. Comparing
schedulers needs hundreds of seeds or a larger parent; that study is not done here. The low
passive share says the default child policy is too eager to cancel when the touch moves (see
section 4).

## 4. Child orders (F4)

**Plain idea.** For each slice, post at our side of the spread and wait; take liquidity only for
what we are behind, when crossing is cheap, or at the end.

**Rules** (`ceap.execution.child.decide`). Post the slice at the near touch. Take
`urgency x (shares behind schedule)` at the far touch. Take everything if the spread is at its
minimum (`tight_spread_ticks`) or in the last slice. If the far touch is at or better than the
I-would price, take the whole remaining parent up to it. A resting child no longer at the near
touch is cancelled and re-posted at the back of the new queue. Children under `min_qty` are not
sent unless they finish the parent. Passive child sizes can be jittered down by up to
`randomise`. Evidence class: *standard desk practice* (Johnson (2010), *Algorithmic Trading and
DMA*, ch. 7-8); the thresholds are *design choices*, not tuned.

Known limitation: cancel/replace on every touch move loses queue priority each time, which is why
the passive share above is low. A queue-aware rule (keep the order while the queue ahead is short)
is the obvious next step.

## 5. Auctions (F5)

**Plain idea.** The open and close are call auctions: orders are collected, then all trade at one
price. A big market-on-close order moves that price, and how much depends on the orders that are
there.

**Formal statement.** The uncross price maximises matched volume; ties go to the smallest
imbalance, then to the price closest to the reference. Allocation is market orders first, then
price, then time. `auction_impact_bps` uncrosses with and without our order. Evidence class:
*exchange rule summaries as recalled* (NYSE Rule 7.35, Nasdaq Rule 4752), simplified (no
continuous-book interaction, no imbalance-only orders).

**Worked example.** MOC buy 500, buys 300 @ 10.02 and 200 @ 10.00; sells 400 @ 9.99, 400 @ 10.01,
300 @ 10.03, reference price 10.00. At 10.01, 800 shares match with no imbalance. 10.02 also
matches 800 with no imbalance, so the tie goes to the price closer to the reference: 10.01. The
10.00 buy and the 10.03 sell get nothing (`tests/unit/execution/test_auction_costloop_rules.py`).

`auction_capacity` sizes an MOC at a fraction of expected auction volume
(`ADV x CLOSING_AUCTION_ADV_FRACTION`, 0.06 in the generator; public commentary puts US closes
at roughly 7-10 % of volume, *not verified here*).

**Deferred** to the lane that owns `ceap.paper`: capping paper MOC fills at an auction fraction
with this impact, and aligning the pre-trade 25 % and fill 10 % limits.

## 6. Predicted versus realised cost (F6)

**Plain idea.** Write down the cost you expect before trading, measure it after, and check that the
errors average out. If they stop averaging out, the model has drifted.

**Formal statement.** Prediction `Y sigma_daily (Q/ADV)^delta` bps; with `delta = 0.5` this calls
`square_root_impact_estimate_bps` (no copy of the formula). Observations go to an SQLite table.
`recalibrate` fits `realised / sigma = Y (Q/ADV)^delta` by least squares on a grid of `delta`, with
`Y` in closed form for each. `drift_report` is the mean residual over the latest week in standard
errors; `|z| >= 2` alerts. `reliability_table` gives predicted against realised by prediction
quantile, the data for a reliability plot. `CostLoop.weekly(as_of)` runs the three in order and
adopts the new fit. Evidence class: the square-root law is an *empirical regularity* (Almgren,
Thum, Hauptmann and Li (2005), Risk 18(7); Toth et al. (2011), Phys. Rev. X 1); `Y = 0.6` is the
repo's existing coefficient, *not a fit*.

**Measured.** On synthetic costs with `Y = 0.9`, `delta = 0.6`, the fit recovers both (400 orders).
The drift z is calibrated: over 400 seeds of an unbiased model, 5.75 % alarm at `|z| >= 2` and
the z has standard deviation 1.02.

## 7. Markouts and routing (F7)

**Plain idea.** After each fill, look at where the mid went 1 s, 5 s, 30 s, 1 min, 5 min later and
at the close. If passive fills on one venue keep being followed by the price moving through us,
that venue is selecting against us.

**Formal statement.** Markout `s (m_{t+h} - p) / p` in bps, positive when the price moved our way
(the convention of `ceap.analytics.slippage.markout_bps`). Size-weighted means by venue and
liquidity flag, each with a percentile bootstrap CI over fills. The routing table marks a venue
`adverse` when its whole CI is below zero, `favourable` when above, otherwise `indistinguishable`,
and suggests a weight: current fill share times `exp(markout / scale)`, halved if adverse. The
weighting is a *design choice*: read it next to the CIs.

**Metamorphic test.** Scaling every price by 25 leaves every markout and CI unchanged.

## 8. Regression cause attribution (F8) - behind a flag

**Plain idea.** The existing attribution scores each cause by fixed ratio thresholds ("spread more
than 1.4 times the baseline"). The new one asks: across comparable windows, how many bps of cost
does each cause actually explain, and how sure are we?

**Formal statement.** Across the observations (matched peer windows, or any comparable set) the
cost `y` (the IS residual against the pre-trade estimate when available, else arrival or total
shortfall) is regressed on one standardised regressor per cause: realised volatility, spread,
minus log depth, latency, participation and adverse drift. Ridge regression (penalty 1 in
standardised units by default) keeps a small cross-section stable. The window's share for cause
`k` is `beta_k z_k`; the shares plus the unexplained part sum to the window's excess over the
mean. CIs resample observations. Fewer than four observations: no shares. Evidence class:
*standard linear cost attribution* (Kissell (2013), *The Science of Algorithmic Trading and
Portfolio Management*, ch. 6), *not validated on real desk data*.

**How to turn it on.** It does not change the heuristic verdict and is off by default:

```python
attribute_causes(window, baseline, mkt_w, mkt_b, peers=peers, method="regression")
```

or set `CEAP_ATTRIBUTION=regression` in the environment for code that calls `attribute_causes`
without the argument (the agents). The result appears under the extra `regression` key of
`AttributionResult.to_dict()`; with the flag off the output is byte-identical to 0.7.1. With five
matched peer sessions (the default) the shares lean heavily on the ridge prior and say so in
`note`; it becomes useful on a multi-day universe of parents.

**Measured.** With 40 synthetic peers where the residual is planted as 2 bps per bps of spread, a
window with a 6 bps spread gets its excess assigned to `WIDE_SPREADS` within 15 %, with a CI above
zero, and volatility (no planted effect) gets under 1 bps.

## 9. Trading rules (F12: ticks, lots, LULD)

Evidence class for all of this: *regulation text as recalled at the time of writing*; check the
current rule before relying on it.

* **Ticks** (Reg NMS Rule 612): $0.01 at or above $1.00, $0.0001 below; the 2024 amendments add a
  $0.005 tick for tight-spread stocks (`tight_spread=True`). `round_to_tick` never rounds in the
  trader's favour (buys down, sells up).
* **Round lots** (2024 round-lot definition): 100 shares up to $250, 40 to $1,000, 10 to
  $10,000, 1 above; `classic=True` gives the old 100. `lot_type` labels odd, round and mixed.
* **LULD** (NMS Plan, Appendix A): Tier 1 5 %, Tier 2 10 % above $3; 20 % from $0.75 to $3; below
  $0.75 the lesser of $0.15 or 75 %; doubled 9:30-9:45 and 15:35-16:00. `LULDMonitor` keeps the
  five-minute reference price (moved only by changes of 1 % or more), enters a Limit State when
  the quote sits on a band, and pauses trading for five minutes if it stays 15 seconds.
  Whether the closing doubling applies to every Tier 2 band has changed across plan amendments, so
  it is a parameter (`double_at_close`), not a guess.

The fat-finger band tied to sigma (the rest of F12) is not in this lane.

## 10. Data generator speed-up (G2) and cleanups (F14)

`SyntheticMarketGenerator._trades` was a per-trade Python loop with five random calls per busy
second. It is now vectorised, and its output is **bit-for-bit identical**. The random draws keep
their exact order: the uniforms between two lognormal calls are contiguous in the stream, so one
call replaces four, and `rng.choice` is replaced by its own algorithm (cumulative weights searched
with one uniform each).

Measured on this machine, with other jobs running:

| | v0.7.1 | 0.8.0 |
|---|---|---|
| `_trades`, one 4-hour segment (19,955 prints) | 10.2 s | 2.1 s |
| `generate` for one scenario | 37.4 s | 3.8 s |
| SHA-256 of all trades, quotes, orders, executions, books, metrics and logs of three scenarios | `9149985c...` | `9149985c...` |

The plan's target was over 50x for `_trades`; this is about 5x on `_trades` and 10x on a whole
`generate`. The rest is building one frozen `Trade` (and one time-zone-aware `datetime`) per
print, which could only go by changing the dataset to lazy columns, and so the output type.
`tests/unit/execution/test_synthetic_trades_g2.py` pins the v0.7.1 digest.

Note: the ML registry labels models with a hash of the generator *source*
(`ceap.ml.registry.generator_hash`), so that hash changes although the generated data does not.
The shipped model cards keep the old hash until the models are re-stamped.

F14: `london()` is documented as a deprecated alias of `session_time` (kept, no warning, because
`ceap.ml.anomaly` imports it); `vwap_by_bucket` uses one `bincount` pass instead of a mask per
bucket; `market_impact_bps` and `temporary_impact_bps` warn with `DeprecationWarning` (nothing in
CEAP calls them) and will go in 1.0.

## 11. What this is not

* Not calibrated to any venue, symbol or period. The book's parameters are illustrative.
* Linear impact in volume; no cross-impact between symbols; one venue (no smart order router
  inside the simulator; the routing table is advice computed from fills).
* The schedulers are compared on eight seeds only, which detects nothing (section 3).
* Deferred, owned by other lanes: paper MOC caps and limit alignment (F5, paper half); integer
  money in ledgers (F10); persisted staging and kill switch (F11); dividends as cash events (F13).
