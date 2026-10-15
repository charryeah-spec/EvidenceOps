from __future__ import annotations

from datetime import UTC, datetime

from evidenceops.models import (
    EvidenceItem,
    ExecutedAction,
    ObservationBundle,
    RecommendedAction,
    VerificationResult,
)


def _e(
    evidence_id: str,
    kind: str,
    source: str,
    summary: str,
    *,
    value=None,
    unit: str | None = None,
    service: str | None = None,
    score: float = 0.0,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        source=source,
        summary=summary,
        value=value,
        unit=unit,
        service=service,
        anomaly_score=score,
    )


class FixtureProvider:
    """Fixture data source used by tests and offline demos."""

    def __init__(self) -> None:
        self._repaired: set[str] = set()

    def prepare(self, problem_id: str) -> str:
        return f"Prepared deterministic scenario {problem_id}"

    def collect(self, problem_id: str) -> ObservationBundle:
        if problem_id in self._repaired:
            return self._healthy(problem_id, "Scenario recovered after approved action")
        key = problem_id.lower()
        if "misconfig" in key or "target_port" in key:
            service = "geo"
            evidence = [
                _e("m-error", "metric", "prometheus", "HTTP error rate rose to 95%", value=0.95, unit="ratio", service=service, score=0.98),
                _e("l-app-error", "log", service, "Geo service returns application errors after an image change", service=service, score=0.92),
                _e("k-image", "k8s_state", "deployment/geo", "Deployment image changed to the known faulty geo:app3 image", value="yinfangchen/geo:app3", service=service, score=1.0),
                _e("k-pods-ok", "k8s_state", "deployment/geo", "All pods are Running and Ready despite application errors", value=True, service=service, score=0.05),
            ]
            return ObservationBundle(problem_id=problem_id, description="Hotel Reservation faulty application image", evidence=evidence)
        if "pod_failure" in key or "pod_kill" in key:
            service = "user"
            evidence = [
                _e("m-restart", "metric", "kube-state-metrics", "Pod restart count increased from 0 to 6", value=6, unit="count", service=service, score=0.95),
                _e("l-crash", "log", service, "Container terminated unexpectedly and failed readiness probe", service=service, score=0.94),
                _e("k-backoff", "k8s_event", "pod/user", "BackOff restarting failed container", service=service, score=1.0),
            ]
            return ObservationBundle(problem_id=problem_id, description="Hotel Reservation pod failure", evidence=evidence)
        if "network_loss" in key:
            service = "user"
            evidence = [
                _e("m-loss", "metric", "network-observer", "Packet loss increased to 65%", value=0.65, unit="ratio", service=service, score=0.97),
                _e("m-errors", "metric", "prometheus", "HTTP timeout rate increased to 71%", value=0.71, unit="ratio", service=service, score=0.94),
                _e("l-timeout", "log", service, "Repeated upstream request timeout", service=service, score=0.88),
                _e("k-ready", "k8s_state", "namespace/test-hotel-reservation", "Pods remain Ready; no restart spike", value=True, service=service, score=0.1),
            ]
            return ObservationBundle(problem_id=problem_id, description="Hotel Reservation network packet loss", evidence=evidence)
        if "network_delay" in key:
            service = "user"
            evidence = [
                _e("m-p95", "metric", "prometheus", "P95 latency increased from 120 ms to 2600 ms", value=2600, unit="ms", service=service, score=0.96),
                _e("l-deadline", "log", service, "Downstream calls exceeded request deadline", service=service, score=0.86),
                _e("k-stable", "k8s_state", "deployment/user", "Pods are Ready with stable CPU and memory", value=True, service=service, score=0.05),
            ]
            return ObservationBundle(problem_id=problem_id, description="Hotel Reservation network delay", evidence=evidence)
        return self._healthy(problem_id, "No fault injected")

    def _healthy(self, problem_id: str, description: str) -> ObservationBundle:
        evidence = [
            _e("m-error-ok", "metric", "prometheus", "HTTP error rate is within baseline", value=0.008, unit="ratio", service="frontend", score=0.02),
            _e("m-p95-ok", "metric", "prometheus", "P95 latency is within baseline", value=118, unit="ms", service="frontend", score=0.02),
            _e("k-all-ready", "k8s_state", "namespace/test-hotel-reservation", "All workloads are Ready", value=True, score=0.0),
        ]
        return ObservationBundle(problem_id=problem_id, description=description, evidence=evidence)

    def execute(self, action: RecommendedAction, *, approved: bool, dry_run: bool) -> ExecutedAction:
        started = datetime.now(UTC)
        success = approved
        if approved and not dry_run:
            self._repaired.add(action.parameters.get("problem_id", ""))
        message = "Dry-run validation passed" if dry_run else ("Action completed" if success else "Action blocked: approval required")
        return ExecutedAction(
            action_type=action.action_type,
            target=action.target,
            approved=approved,
            dry_run=dry_run,
            success=success,
            message=message,
            started_at=started,
            finished_at=datetime.now(UTC),
        )

    def verify(self, problem_id: str, action: RecommendedAction | None = None) -> VerificationResult:
        healthy = problem_id in self._repaired or "noop" in problem_id.lower()
        return VerificationResult(
            healthy=healthy,
            checks=["workloads_ready", "error_rate_within_baseline", "latency_within_baseline"],
            message="Service recovered" if healthy else "Fault remains active",
        )

    def cleanup(self, problem_id: str) -> None:
        self._repaired.discard(problem_id)
