"""Engineering agent: did our technology behave normally?"""

from __future__ import annotations

from ceap.agents.base import BaseAgent, confidence_from_excess, finite
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.findings import Finding


class EngineeringAgent(BaseAgent):
    agent_id = "engineering"

    async def execute(self, context: AgentContext) -> AgentResult:
        w = self.window(context)
        lat_args = {
            "service": "order-gateway",
            "start": w.start,
            "end": w.end,
            "baseline_start": w.baseline_start,
            "baseline_end": w.baseline_end,
            "dataset": w.dataset,
        }
        latency, lat_ev = await self.ensure(context, "engineering.get_latency_metrics", lat_args)
        deployments, dep_ev = await self.ensure(
            context,
            "engineering.get_deployments",
            {"start": w.baseline_start, "end": w.end, "dataset": w.dataset},
        )
        errors, err_ev = await self.ensure(
            context,
            "engineering.search_logs",
            {"start": w.start, "end": w.end, "level": "ERROR", "dataset": w.dataset},
        )
        warns, warn_ev = await self.ensure(
            context,
            "engineering.search_logs",
            {"start": w.start, "end": w.end, "level": "WARN", "dataset": w.dataset},
        )
        md_metrics, md_ev = await self.ensure(
            context,
            "engineering.get_service_metrics",
            {
                "service": "market-data-adapter",
                "start": w.start,
                "end": w.end,
                "metric": "feed_gap_count",
                "dataset": w.dataset,
            },
        )

        lr = latency.get("latency_ratio")
        slo = bool(latency.get("slo_breached"))
        window_p99 = (latency.get("window") or {}).get("p99_us")
        deps = deployments.get("items", [])
        deps_pre_window = [d for d in deps if d["timestamp"] < w.end and d["timestamp"] >= w.baseline_start]
        error_count = errors.get("count", 0)
        warn_count = warns.get("count", 0)
        feed_gaps = ((md_metrics.get("summary") or {}).get("feed_gap_count") or {}).get("sum", 0.0)

        calc = self.calc_evidence(
            context,
            "Technology health summary for the window",
            {
                "latency_ratio": lr,
                "slo_breached": slo,
                "window_p99_us": window_p99,
                "deployments_in_scope": len(deps_pre_window),
                "error_logs": error_count,
                "warn_logs": warn_count,
                "feed_gap_count": feed_gaps,
            },
            (*lat_ev, *dep_ev, *err_ev, *warn_ev, *md_ev),
        )
        findings: list[Finding] = []

        def add(statement: str, conf: float, evidence: tuple[str, ...], **attrs: object) -> None:
            findings.append(
                Finding.create(
                    statement,
                    evidence,
                    conf,
                    category="TECHNOLOGY",
                    produced_by=self.id,
                    attributes=dict(attrs),
                )
            )

        if finite(lr) and lr >= 3.0:
            add(
                f"Order-gateway acknowledgement latency was {lr:.1f}x the baseline (p99 {window_p99:.0f} us{', SLO breached' if slo else ''}).",
                confidence_from_excess(lr - 3.0, 3.0),
                (*lat_ev, calc.id),
                anomaly="TECHNOLOGY_LATENCY",
                latency_ratio=lr,
            )
        elif finite(lr):
            add(
                f"Execution latency remained within the normal range ({lr:.2f}x baseline, p99 {window_p99:.0f} us).",
                0.85,
                (*lat_ev, calc.id),
                latency_ratio=lr,
            )

        sor = [
            d
            for d in deps_pre_window
            if d["service"] in ("smart-order-router", "order-gateway", "market-data-adapter")
        ]
        if sor:
            d = sor[-1]
            add(
                f"Deployment {d['change_id']} ({d['service']} {d['version']}: {d['description']}) went live at {d['timestamp']} - shortly before the window.",
                0.85,
                (*dep_ev, calc.id),
                anomaly="CODE_CHANGE",
                change_id=d["change_id"],
            )
        else:
            add(
                "No deployments to the execution path occurred in the hour before or during the window.",
                0.8,
                (*dep_ev, calc.id),
            )

        if error_count > 0:
            sample = "; ".join(e["message"] for e in errors.get("items", [])[:2])
            add(
                f"{error_count} ERROR log events were recorded in the window (e.g. {sample}).",
                min(0.9, 0.6 + 0.05 * error_count),
                (*err_ev, calc.id),
                error_logs=error_count,
            )
        else:
            add("No ERROR-level log events were recorded in the window.", 0.8, (*err_ev, calc.id))
        if feed_gaps > 0:
            add(
                f"The market-data adapter reported {feed_gaps:.0f} feed gaps during the window.",
                0.85,
                (*md_ev, calc.id),
                anomaly="MARKET_DATA_ANOMALY",
                feed_gap_count=feed_gaps,
            )

        return AgentResult(
            agent_id=self.id,
            success=True,
            findings=tuple(findings),
            output={
                "latency_ratio": lr,
                "slo_breached": slo,
                "window_p99_us": window_p99,
                "deployments": len(deps_pre_window),
                "deployment_items": deps_pre_window,
                "error_logs": error_count,
                "warn_logs": warn_count,
                "feed_gap_count": feed_gaps,
                "reject_rate": None,
            },
            summary=f"latency x{lr:.2f}, {len(deps_pre_window)} deployments, {error_count} errors"
            if finite(lr)
            else "no latency data",
        )
