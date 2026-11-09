"""AI evaluation harness (plan 0.8.0, WS-B and WS-C).

Every metric here is computed through the :class:`~ceap.llm.client.LLMClient` interface, so the
mock, a hand-built cassette and a local open-weight model (B1) plug in unchanged. Every result
carries an ``evidence_class`` (``mock``, ``hand_built``, ``local_model`` or ``recorded``) taken from
the client, never assumed, and a 95 % Wilson interval on every rate.

* :mod:`ceap.eval.stats` - Wilson intervals and rate records;
* :mod:`ceap.eval.clients` - a capturing wrapper and the deliberately degraded clients (B3);
* :mod:`ceap.eval.agents` - per-agent evals for the planner, critic and reporter (B2);
* :mod:`ceap.eval.ood` - the out-of-distribution golden split (B6);
* :mod:`ceap.eval.corpus` - knowledge-corpus version pinning and staleness (C3).
"""
