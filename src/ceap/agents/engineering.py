"""Engineering agent: did our technology behave normally?"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ceap.agents.base import BaseAgent, Window, confidence_from_excess, finite
from ceap.analytics.calibration import CHANGE_POINT_KEY, calibration_info
from ceap.domain.agents import AgentContext, AgentResult
from ceap.domain.findings import Finding
from ceap.ml.anomaly import (
    TECHNOLOGY_FEATURES,
    Alarm,
    DetectionReport,
    alarm_confidence,
    attribute_deployment,
    load_detector,
    minute_features,
    series_features,
)

#: change onsets this long before the window start still belong to the window's story
CHANGE_POINT_LEAD = timedelta(minutes=15)


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
        latency, lat_ev = await self.fetched(context, "engineering.get_latency_metrics", lat_args)
        deployments, dep_ev = await self.fetched(
            context,
            "engineering.get_deployments",
            {"start": w.baseline_start, "end": w.end, "dataset": w.dataset},
        )
        errors, err_ev = await self.fetched(
            context,
            "engineering.search_logs",
            {"start": w.start, "end": w.end, "level": "ERROR", "dataset": w.dataset},
        )
        warns, warn_ev = await self.fetched(
            context,
            "engineering.search_logs",
            {"start": w.start, "end": w.end, "level": "WARN", "dataset": w.dataset},
        )
        md_metrics, md_ev = await self.fetched(
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

        cp_report, cp_ev, cp_error = await change_point_scan(
            self, context, w, ("order-gateway", "smart-order-router"), TECHNOLOGY_FEATURES
        )

        lr = latency.get("latency_ratio")
        slo = bool(latency.get("slo_breached"))
        window_p99 = (latency.get("window") or {}).get("p99_us")
        deps = deployments.get("items", [])
        deps_pre_window = [
            d for d in deps if _parse_ts(w.baseline_start) <= _parse_ts(d["timestamp"]) < _parse_ts(w.end)
        ]
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
                f"Order-gateway acknowledgement latency was {lr:.1f}x the baseline (p99 {_fmt_us(window_p99)}{', SLO breached' if slo else ''}).",
                confidence_from_excess(lr - 3.0, 3.0),
                (*lat_ev, calc.id),
                anomaly="TECHNOLOGY_LATENCY",
                latency_ratio=lr,
            )
        elif finite(lr):
            add(
                f"Execution latency remained within the normal range ({lr:.2f}x baseline, p99 {_fmt_us(window_p99)}).",
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

        cp_alarms = window_alarms(cp_report, w)
        if cp_report is not None and cp_alarms:
            first = cp_alarms[0]
            dep = attribute_deployment(first, deps)
            raw = alarm_confidence(cp_report)
            cp_calc = self.calc_evidence(
                context,
                "Change-point detection (robust z, CUSUM, PELT) on per-minute technology metrics",
                {
                    "change_points": cp_report.to_dict(),
                    "model_hash": cp_report.model_hash,
                    "label": cp_report.label,
                    "raw_confidence": raw,
                    "calibration": calibration_info(CHANGE_POINT_KEY),
                },
                cp_ev,
            )
            lag = ""
            if dep:
                minutes = int((first.onset_at - _parse_ts(dep["timestamp"])).total_seconds() // 60)
                lag = f", {minutes} minutes after deployment {dep['change_id']}"
            others = f"; {len(cp_alarms) - 1} other feature(s) also changed." if len(cp_alarms) > 1 else "."
            add(
                f"Change-point detector: {first.describe()}{lag}{others}",
                self.calibrated(CHANGE_POINT_KEY, raw),
                (*cp_ev, cp_calc.id),
                change_point=first.feature,
                change_onset=first.onset_at.isoformat(),
                model_hash=cp_report.model_hash,
            )

        return AgentResult(
            agent_id=self.id,
            success=True,
            findings=tuple(findings),
            output={
                "change_points": None if cp_report is None else cp_report.to_dict(),
                "change_point_error": cp_error,
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


async def change_point_scan(
    agent: BaseAgent,
    ctx: AgentContext,
    w: Window,
    services: tuple[str, ...],
    features: tuple[str, ...],
) -> tuple[DetectionReport | None, tuple[str, ...], str | None]:
    """Fetch per-minute series (a reference period before the baseline, through the window end) and detect."""
    det = load_detector()
    start = (_parse_ts(w.baseline_start) - timedelta(minutes=det.reference_minutes)).isoformat()
    points: list[dict[str, Any]] = []
    evidence: tuple[str, ...] = ()
    try:
        for svc in services:
            data, ev = await agent.ensure(
                ctx,
                "engineering.get_service_metrics",
                {"service": svc, "start": start, "end": w.end, "dataset": w.dataset},
            )
            points += series_features((data or {}).get("series") or {}, svc)
            evidence += ev
    except Exception as exc:  # noqa: BLE001 - an auxiliary scan: the agent's core findings do not depend on it
        return None, evidence, f"{type(exc).__name__}: {exc}"
    mf = minute_features(points, det.features)
    if len(mf) <= det.reference_minutes:
        return None, evidence, "not enough minutes for the reference period"
    return det.detect(mf, only=features), evidence, None


def window_alarms(report: DetectionReport | None, w: Window) -> list[Alarm]:
    """Alarms whose onset falls in ``[window start - lead, window end)``, earliest first."""
    if report is None:
        return []
    lo, hi = _parse_ts(w.start) - CHANGE_POINT_LEAD, _parse_ts(w.end)
    hits = [a for a in report.alarms if lo <= a.onset_at < hi]
    return sorted(hits, key=lambda a: (a.alarm_index, a.onset_index, -a.peak_z))


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    ts = datetime.fromisoformat(str(value))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _fmt_us(x: Any) -> str:
    return f"{float(x):.0f} us" if finite(x) else "n/a"
