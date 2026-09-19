# Trading Limits Policy

## Cash equities book CASH_EQ_1

- Maximum position per symbol: 500,000 shares.
- Maximum single parent order: 250,000 shares; larger orders must be split and require desk-head approval.
- Maximum participation rate: 25% of interval volume for any algorithmic order.
- Maximum gross notional per symbol: 150,000,000 USD.

## Breach handling

A breach detected by the risk-service blocks new child orders and raises an incident with severity 2. The
investigation platform reports limit utilisation and any breach as a RISK finding; agents never modify limits.

## Stress

The standard stress scenario is an instantaneous 200 bps adverse move on the end-of-window position.
