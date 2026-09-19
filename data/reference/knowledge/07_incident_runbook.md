# Incident Runbook: Execution Path Latency

## Symptoms

- order-gateway ack_latency_p99_us above the 3,000 microsecond SLO.
- WARN "ack latency SLO breached" and ERROR "venue timeout waiting for ack" log lines.
- Child orders rejected with reason TOO_LATE_TO_ENTER.

## First response

1. Check deployments to smart-order-router, order-gateway and market-data-adapter in the last two hours.
2. Compare route_latency_us with the baseline hour; a ratio above 3 confirms a technology cause.
3. If a deployment coincides with the latency increase, roll back and notify the desk.
4. Record the change id (CHG-xxxxx) in the incident ticket.

## Known change

CHG-40388 (smart-order-router 2.14.1) altered routing latency budgets and venue timeouts. Any latency incident
after this change should consider it a candidate cause.
