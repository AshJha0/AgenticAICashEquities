"""Execution: an event-driven book, impact, schedulers, child orders, auctions and cost loops (WS-F).

Status (0.10.0, finding R-23): a simulator library, not used by the default paper book. The paper book imports only the
auction model and the LOB cost measurement, behind opt-in settings; wiring is planned for 0.11.0.

Everything here is deterministic given a seed and runs on synthetic data; nothing sends an order
anywhere. See ``docs/execution/execution.md`` for the walkthrough and the evidence behind each
model, and COOKBOOK recipes 'Execution' for runnable examples.

Modules:

* :mod:`~ceap.execution.lob` - price-time FIFO book and event simulator with latency (F1)
* :mod:`~ceap.execution.impact` - propagator impact and its fit against the truth (F2)
* :mod:`~ceap.execution.schedule` - TWAP, VWAP, POV, Almgren-Chriss behind one interface (F3)
* :mod:`~ceap.execution.child` - passive/aggressive child orders, I-would, cancel/replace (F4)
* :mod:`~ceap.execution.algo` - run a parent through the book and score it with Perold IS
* :mod:`~ceap.execution.auction` - opening/closing auction uncross and impact (F5)
* :mod:`~ceap.execution.cost_loop` - predicted vs realised cost, recalibration, drift (F6)
* :mod:`~ceap.execution.markouts` - markout curves with CIs and a routing table (F7)
* :mod:`~ceap.execution.rules` - tick sizes, round lots, LULD bands and pauses (F12)
"""

from ceap.execution.schedule import POV, TWAP, VWAP, AlmgrenChriss, Schedule, allocate

__all__ = ["POV", "TWAP", "VWAP", "AlmgrenChriss", "Schedule", "allocate"]
