# Market Data Architecture

## Feed handling

Consolidated NBBO quotes and prints are received by the market-data-adapter service, sequenced and published
on the internal bus at one update per second per symbol for the analytics store, with full-depth order-book
snapshots every ten seconds.

## Quality monitoring

The adapter emits the metric feed_gap_count whenever a sequence gap is detected on the consolidated feed and
logs a WARN entry "feed gap detected". Crossed NBBO observations are suppressed and logged. A stale-quote run
is any run of identical consecutive quotes longer than five seconds on a liquid symbol.

## Impact on execution

Stale or crossed quotes cause the SOR to misjudge the far touch: aggressive slices may be priced off a stale
ask, leading to rejects or to fills at a worse price than the live market. Execution reports in such windows
should be read alongside the feed_gap_count metric.
