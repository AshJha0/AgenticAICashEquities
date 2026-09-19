"""Engineering MCP server: logs, service metrics, deployments, latency."""

from __future__ import annotations

from typing import Any

import numpy as np

from ceap.data.repositories import DatasetStore
from ceap.domain.common import to_jsonable
from ceap.domain.engineering import LatencySummary
from ceap.mcp.common import paginate, select_dataset, window_of
from ceap.mcp.server import MCPServerDefinition

LATENCY_METRICS = {"order-gateway": "ack_latency_p50_us", "smart-order-router": "route_latency_us"}


def build_server(store: DatasetStore) -> MCPServerDefinition:
    server = MCPServerDefinition(
        "engineering",
        "Observability for the trading stack: log search, service metrics, deployments and latency summaries.",
    )

    @server.tool("Search structured logs by service, level and free-text query within a window; paginated.")
    async def search_logs(
        start: str,
        end: str,
        service: str | None = None,
        level: str | None = None,
        query: str | None = None,
        limit: int = 200,
        offset: int = 0,
        dataset: str | None = None,
    ) -> dict[str, Any]:
        ds = select_dataset(store, dataset)
        s, e = window_of(ds, start, end)
        q = (query or "").lower()
        hits = [
            log
            for log in ds.logs
            if s <= log.timestamp < e
            and (service is None or log.service == service)
            and (level is None or log.level == level.upper())
            and (not q or q in log.message.lower())
        ]
        page = paginate([to_jsonable(h) for h in hits], limit, offset)
        levels: dict[str, int] = {}
        for h in hits:
            levels[h.level] = levels.get(h.level, 0) + 1
        page.update({"start": s.isoformat(), "end": e.isoformat(), "level_counts": levels})
        return page

    @server.tool(
        "Return time series of a service metric (all metrics of the service when metric is omitted)."
    )
    async def get_service_metrics(
        service: str, start: str, end: str, metric: str | None = None, dataset: str | None = None
    ) -> dict[str, Any]:
        ds = select_dataset(store, dataset)
        s, e = window_of(ds, start, end)
        points = [
            m
            for m in ds.metrics
            if m.service == service and s <= m.timestamp < e and (metric is None or m.metric == metric)
        ]
        series: dict[str, list[dict[str, Any]]] = {}
        for p in points:
            series.setdefault(p.metric, []).append({"timestamp": p.timestamp.isoformat(), "value": p.value})
        summary = {
            name: {
                "mean": float(np.mean([v["value"] for v in vals])),
                "max": float(np.max([v["value"] for v in vals])),
                "sum": float(np.sum([v["value"] for v in vals])),
            }
            for name, vals in series.items()
        }
        return {
            "service": service,
            "start": s.isoformat(),
            "end": e.isoformat(),
            "series": series,
            "summary": summary,
        }

    @server.tool("Return deployments / configuration changes in the window, optionally filtered by service.")
    async def get_deployments(
        start: str, end: str, service: str | None = None, dataset: str | None = None
    ) -> dict[str, Any]:
        ds = select_dataset(store, dataset)
        s, e = window_of(ds, start, end)
        deps = [
            d for d in ds.deployments if s <= d.timestamp < e and (service is None or d.service == service)
        ]
        return {
            "start": s.isoformat(),
            "end": e.isoformat(),
            "count": len(deps),
            "items": [to_jsonable(d) for d in deps],
        }

    @server.tool(
        "Summarise service latency in a window and compare with a baseline window "
        "(p50/p99/max plus the window-over-baseline ratio)."
    )
    async def get_latency_metrics(
        service: str,
        start: str,
        end: str,
        baseline_start: str | None = None,
        baseline_end: str | None = None,
        dataset: str | None = None,
    ) -> dict[str, Any]:
        ds = select_dataset(store, dataset)
        s, e = window_of(ds, start, end)
        metric = LATENCY_METRICS.get(service, "ack_latency_p50_us")

        def summarise(a, b) -> LatencySummary | None:
            vals = np.array(
                [
                    m.value
                    for m in ds.metrics
                    if m.service == service and m.metric == metric and a <= m.timestamp < b
                ]
            )
            if vals.size == 0:
                return None
            return LatencySummary(
                service,
                a,
                b,
                float(np.percentile(vals, 50)),
                float(np.percentile(vals, 99)),
                float(vals.max()),
                int(vals.size),
            )

        window = summarise(s, e)
        bs = (
            window_of(ds, baseline_start, baseline_end)
            if baseline_start and baseline_end
            else (ds.baseline_start, ds.baseline_end)
        )
        baseline = summarise(*bs)
        ratio = (window.p50_us / baseline.p50_us) if window and baseline and baseline.p50_us else None
        return {
            "service": service,
            "metric": metric,
            "window": to_jsonable(window),
            "baseline": to_jsonable(baseline),
            "latency_ratio": ratio,
            "slo_p99_us": 3000.0,
            "slo_breached": bool(window and window.p99_us > 3000.0),
        }

    @server.tool("Return the list of services in the trading stack and their owners.")
    async def get_services() -> dict[str, Any]:
        return {
            "services": [
                {"name": "order-gateway", "owner": "execution-platform", "tier": 1},
                {"name": "smart-order-router", "owner": "sor-team", "tier": 1},
                {"name": "market-data-adapter", "owner": "market-data", "tier": 1},
                {"name": "risk-service", "owner": "risk-tech", "tier": 2},
            ]
        }

    return server
