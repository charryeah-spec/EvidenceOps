from __future__ import annotations

import atexit
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from evidenceops.config import Settings
from evidenceops.models import (
    EvidenceItem,
    ExecutedAction,
    ObservationBundle,
    RecommendedAction,
    VerificationResult,
)


class _RegisteredAgent:
    async def get_action(self, _: str) -> str:
        return 'submit("No")'


class AIOpsLabProvider:
    """Adapter over AIOpsLab's Orchestrator and task action APIs.

    Fault setup and framework telemetry are delegated to AIOpsLab. Kubernetes
    state is normalized into EvidenceOps' stable evidence schema.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repo = settings.aiopslab_path.resolve()
        self.orchestrator: Any | None = None
        self.problem: Any | None = None
        self.problem_id: str | None = None
        self.description = ""
        self.available_actions: dict[str, str] = {}

    def prepare(self, problem_id: str) -> str:
        if not self.repo.joinpath("aiopslab", "orchestrator", "orchestrator.py").exists():
            raise RuntimeError(f"AIOpsLab is not available at {self.repo}")
        if str(self.repo) not in sys.path:
            sys.path.insert(0, str(self.repo))
        from aiopslab.orchestrator import Orchestrator

        self._ensure_kind_runtime()
        self.orchestrator = Orchestrator(results_dir=str(self.settings.results_dir / "aiopslab"))
        self.orchestrator.register_agent(_RegisteredAgent(), name="evidenceops")
        self.description, instructions, self.available_actions = self.orchestrator.init_problem(problem_id)
        self.problem = self.orchestrator.session.problem
        self.problem_id = problem_id
        return f"{self.description}\n{instructions}"

    def collect(self, problem_id: str) -> ObservationBundle:
        self._require_problem(problem_id)
        namespace = self.problem.namespace
        raw: dict[str, Any] = {"available_actions": sorted(self.available_actions)}
        evidence: list[EvidenceItem] = []

        chaos_resources: dict[str, Any] = {}
        for resource in ("podchaos", "networkchaos"):
            result = self._kubectl(
                ["get", resource, "-n", namespace, "-o", "json"],
                check=False,
            )
            if result.returncode:
                continue
            payload = json.loads(result.stdout)
            chaos_resources[resource] = payload
            for item in payload.get("items", []):
                metadata = item.get("metadata", {})
                spec = item.get("spec", {})
                selector = spec.get("selector", {})
                label_selectors = selector.get("labelSelectors", {})
                service = label_selectors.get("io.kompose.service")
                if isinstance(service, str):
                    service = service.split(",")[0].strip()
                name = metadata.get("name", "unknown")
                action = spec.get("action", "unknown")
                fault_signal = {
                    "loss": "network_packet_loss",
                    "delay": "network_delay",
                    "pod-failure": "pod_failure",
                }.get(action, action)
                evidence.append(
                    self._e(
                        f"chaos-{resource}-{name}",
                        "k8s_event",
                        f"chaos/{resource}/{name}",
                        (
                            f"active {resource} action={action}, "
                            f"fault_signal={fault_signal}, target_service={service}"
                        ),
                        service=service,
                        value={"action": action, "selector": selector},
                        score=1.0,
                    )
                )
        raw["chaos_resources"] = chaos_resources
        has_active_chaos = self._has_active_chaos(chaos_resources)
        raw["has_active_chaos"] = has_active_chaos

        pods = self._kubectl_json(["get", "pods", "-n", namespace, "-o", "json"])
        has_faulty_image = any(
            container.get("image") == "yinfangchen/geo:app3"
            for pod in pods.get("items", [])
            for container in pod.get("spec", {}).get("containers", [])
        )
        stabilization_wait = 0
        if (
            not has_active_chaos
            and not has_faulty_image
            and not self._all_active_pods_ready(pods)
        ):
            for _ in range(30):
                time.sleep(2)
                stabilization_wait += 2
                pods = self._kubectl_json(
                    ["get", "pods", "-n", namespace, "-o", "json"]
                )
                if self._all_active_pods_ready(pods):
                    break
        raw["pods"] = pods
        raw["stabilization_wait_seconds"] = stabilization_wait
        if stabilization_wait:
            evidence.append(
                self._e(
                    "stabilization-window",
                    "k8s_state",
                    f"namespace/{namespace}",
                    (
                        "Waited for transient post-deployment startup failures: "
                        f"{stabilization_wait}s; "
                        f"all_active_ready={self._all_active_pods_ready(pods)}"
                    ),
                    score=0.0,
                )
            )
        pod_services: dict[str, str] = {}
        pod_readiness: dict[str, bool] = {}
        for pod in pods.get("items", []):
            metadata = pod.get("metadata", {})
            status = pod.get("status", {})
            name = metadata.get("name", "unknown")
            labels = metadata.get("labels", {})
            service = labels.get("io.kompose.service") or labels.get("app") or name.split("-")[0]
            pod_services[name] = service
            statuses = status.get("containerStatuses", [])
            restarts = sum(int(item.get("restartCount", 0)) for item in statuses)
            ready = bool(statuses) and all(item.get("ready", False) for item in statuses)
            pod_readiness[name] = ready
            phase = status.get("phase", "Unknown")
            score = 0.95 if phase not in {"Running", "Succeeded"} or not ready else min(0.9, restarts / 5)
            evidence.append(self._e(f"pod-{name}", "k8s_state", f"pod/{name}", f"phase={phase}, ready={ready}, restarts={restarts}", service=service, value={"phase": phase, "ready": ready, "restarts": restarts}, score=score))
            for container in pod.get("spec", {}).get("containers", []):
                image = container.get("image", "")
                image_score = 1.0 if image == "yinfangchen/geo:app3" else 0.0
                evidence.append(self._e(f"image-{name}-{container.get('name', 'container')}", "k8s_state", f"pod/{name}", f"container={container.get('name')}, image={image}", service=service, value=image, score=image_score))

        services = self._kubectl_json(
            ["get", "services", "-n", namespace, "-o", "json"]
        )
        raw["services"] = services
        for item in services.get("items", []):
            metadata = item.get("metadata", {})
            spec = item.get("spec", {})
            name = metadata.get("name", "unknown")
            ports = [
                {
                    "port": port.get("port"),
                    "targetPort": port.get("targetPort"),
                }
                for port in spec.get("ports", [])
            ]
            evidence.append(
                self._e(
                    f"service-{name}",
                    "k8s_state",
                    f"service/{name}",
                    f"clusterIP={spec.get('clusterIP')}, ports={ports}",
                    service=name,
                    value={"clusterIP": spec.get("clusterIP"), "ports": ports},
                )
            )

        endpoints = self._kubectl_json(
            ["get", "endpoints", "-n", namespace, "-o", "json"]
        )
        raw["endpoints"] = endpoints
        for item in endpoints.get("items", []):
            name = item.get("metadata", {}).get("name", "unknown")
            subsets = item.get("subsets", [])
            ready_count = sum(len(subset.get("addresses", [])) for subset in subsets)
            not_ready_count = sum(
                len(subset.get("notReadyAddresses", [])) for subset in subsets
            )
            evidence.append(
                self._e(
                    f"endpoint-{name}",
                    "k8s_state",
                    f"endpoints/{name}",
                    f"ready_addresses={ready_count}, not_ready_addresses={not_ready_count}",
                    service=name,
                    value={"ready": ready_count, "not_ready": not_ready_count},
                    score=0.85 if ready_count == 0 and not_ready_count else 0.0,
                )
            )

        events = self._kubectl_json(["get", "events", "-n", namespace, "-o", "json"])
        raw["events"] = events
        for index, event in enumerate(events.get("items", [])):
            event_type = event.get("type", "Normal")
            reason = event.get("reason", "")
            message = event.get("message", "")
            involved = event.get("involvedObject", {}).get("name", "")
            service = pod_services.get(involved) or self._service_from_text(involved)
            current_unhealthy_pod = (
                involved in pod_readiness and not pod_readiness[involved]
            )
            score = (
                0.9
                if current_unhealthy_pod
                and (
                    event_type == "Warning"
                    or reason.lower() in {"backoff", "unhealthy", "failed"}
                )
                else 0.0
            )
            if score or index < 10:
                evidence.append(self._e(f"event-{index}", "k8s_event", f"event/{reason or index}", f"{reason}: {message}"[:1000], service=service, value=event_type, score=score))

        logs_raw: dict[str, str] = {}
        for pod_name, service in pod_services.items():
            result = self._kubectl(["logs", pod_name, "-n", namespace, "--all-containers=true", "--tail=200"], check=False)
            logs_raw[pod_name] = result.stdout[-12000:]
            text = (result.stdout + "\n" + result.stderr).strip()
            if not text:
                continue
            hits = len(re.findall(r"(?i)error|exception|timeout|deadline|refused|failed|unavailable", text))
            score = min(1.0, 0.55 + hits * 0.08) if hits else 0.05
            if pod_readiness.get(pod_name, False):
                score = min(score, 0.4)
            summary = self._summarize_logs(text)
            evidence.append(self._e(f"log-{pod_name}", "log", f"pod/{pod_name}", summary, service=service, value=hits, score=score))
        raw["logs"] = logs_raw

        try:
            metrics_result = self.problem.actions.get_metrics(namespace, duration=5)
            metrics_path = self._resolve_metrics_path(metrics_result)
            raw["metrics_result"] = str(metrics_result)
            raw["metrics_path"] = str(metrics_path)
            evidence.extend(self._read_metric_evidence(metrics_path))
        except Exception as exc:
            raw["metrics_error"] = str(exc)
            evidence.append(self._e("metrics-unavailable", "metric", "AIOpsLab.get_metrics", f"Metric collection failed: {exc}", score=0.0))

        return ObservationBundle(
            problem_id=problem_id,
            description=self.description,
            evidence=evidence,
            raw=raw,
        )

    def execute(self, action: RecommendedAction, *, approved: bool, dry_run: bool) -> ExecutedAction:
        started = datetime.now(UTC)
        if not approved:
            return ExecutedAction(action_type=action.action_type, target=action.target, approved=False, dry_run=dry_run, success=False, message="Action blocked: approval required", started_at=started, finished_at=datetime.now(UTC))
        namespace = action.namespace
        if action.action_type == "restart_deployment":
            if dry_run:
                restarted_at = datetime.now(UTC).isoformat()
                patch = {
                    "spec": {
                        "template": {
                            "metadata": {
                                "annotations": {
                                    "kubectl.kubernetes.io/restartedAt": restarted_at
                                }
                            }
                        }
                    }
                }
                command = [
                    "patch",
                    f"deployment/{action.target}",
                    "-n",
                    namespace,
                    "--type=merge",
                    "-p",
                    json.dumps(patch),
                    "--dry-run=server",
                    "-o",
                    "yaml",
                ]
            else:
                command = [
                    "rollout",
                    "restart",
                    f"deployment/{action.target}",
                    "-n",
                    namespace,
                ]
        elif action.action_type == "restore_deployment_image":
            container = action.parameters["container"]
            image = action.parameters["image"]
            command = ["set", "image", f"deployment/{action.target}", f"{container}={image}", "-n", namespace]
        else:
            return ExecutedAction(action_type=action.action_type, target=action.target, approved=True, dry_run=dry_run, success=False, message="Action is not supported by AIOpsLabProvider", started_at=started, finished_at=datetime.now(UTC))
        if dry_run and action.action_type != "restart_deployment":
            command.extend(["--dry-run=server", "-o", "yaml"])
        result = self._kubectl(command, check=False)
        return ExecutedAction(
            action_type=action.action_type,
            target=action.target,
            approved=True,
            dry_run=dry_run,
            success=result.returncode == 0,
            message=(result.stdout or result.stderr)[-2000:],
            started_at=started,
            finished_at=datetime.now(UTC),
        )

    def verify(self, problem_id: str, action: RecommendedAction | None = None) -> VerificationResult:
        self._require_problem(problem_id)
        namespace = self.problem.namespace
        checks: list[str] = []
        rollout_ok = True
        if action:
            rollout = self._kubectl(["rollout", "status", f"deployment/{action.target}", "-n", namespace, "--timeout=60s"], check=False)
            checks.append((rollout.stdout or rollout.stderr).strip())
            rollout_ok = rollout.returncode == 0

        states: list[bool] = []
        active_pods = []
        for attempt in range(16):
            pods = self._kubectl_json(["get", "pods", "-n", namespace, "-o", "json"])
            active_pods = [
                pod
                for pod in pods.get("items", [])
                if not pod.get("metadata", {}).get("deletionTimestamp")
            ]
            states = []
            for pod in active_pods:
                statuses = pod.get("status", {}).get("containerStatuses", [])
                states.append(
                    bool(statuses)
                    and all(item.get("ready", False) for item in statuses)
                )
            if states and all(states):
                break
            if attempt < 15:
                time.sleep(2)
        checks.append(f"ready_active_pods={sum(states)}/{len(states)}")

        endpoint_ok = True
        image_ok = True
        if action:
            endpoint = self._kubectl_json(
                ["get", "endpoints", action.target, "-n", namespace, "-o", "json"]
            )
            ready_addresses = sum(
                len(subset.get("addresses", []))
                for subset in endpoint.get("subsets", [])
            )
            endpoint_ok = ready_addresses > 0
            checks.append(f"ready_endpoints/{action.target}={ready_addresses}")

            if action.action_type == "restore_deployment_image":
                deployment = self._kubectl_json(
                    [
                        "get",
                        "deployment",
                        action.target,
                        "-n",
                        namespace,
                        "-o",
                        "json",
                    ]
                )
                images = {
                    container.get("name"): container.get("image")
                    for container in deployment.get("spec", {})
                    .get("template", {})
                    .get("spec", {})
                    .get("containers", [])
                }
                container = action.parameters.get("container")
                expected_image = action.parameters.get("image")
                image_ok = images.get(container) == expected_image
                checks.append(
                    f"deployment_image/{action.target}={images.get(container)}"
                )

        healthy = (
            rollout_ok
            and bool(states)
            and all(states)
            and endpoint_ok
            and image_ok
        )
        return VerificationResult(
            healthy=healthy,
            checks=checks,
            message=(
                "Deployment, active pods and endpoints recovered"
                if healthy
                else "Recovery checks did not all pass"
            ),
        )

    def cleanup(self, problem_id: str) -> None:
        if not self.problem or problem_id != self.problem_id:
            return
        try:
            self.problem.recover_fault()
        finally:
            try:
                from aiopslab.orchestrator.orchestrator import exit_cleanup_fault
                atexit.unregister(exit_cleanup_fault)
            except Exception:
                pass
            try:
                self.problem.app.cleanup()
            finally:
                if self.orchestrator and hasattr(self.orchestrator, "prometheus"):
                    self.orchestrator.prometheus.teardown()
        self.problem = None
        self.problem_id = None

    def _require_problem(self, problem_id: str) -> None:
        if self.problem is None or self.problem_id != problem_id:
            raise RuntimeError("Call prepare(problem_id) before collecting or verifying telemetry")

    def _ensure_kind_runtime(self) -> None:
        """Restore ephemeral host paths that disappear after Docker restarts."""
        cluster_name = os.getenv("AIOPSLAB_CLUSTER_NAME", "kind")
        node_name = f"{cluster_name}-control-plane"
        result = subprocess.run(
            ["docker", "exec", node_name, "mkdir", "-p", "/run/udev"],
            cwd=self.repo,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"Kind node {node_name} is unavailable: "
                f"{(result.stderr or result.stdout).strip()}"
            )

        ndm = self._kubectl(
            ["get", "pods", "-n", "openebs", "-l", "name=openebs-ndm", "-o", "json"],
            check=False,
        )
        if ndm.returncode:
            return
        items = json.loads(ndm.stdout).get("items", [])
        unhealthy = any(
            pod.get("status", {}).get("phase") != "Running"
            or not all(
                status.get("ready", False)
                for status in pod.get("status", {}).get("containerStatuses", [])
            )
            for pod in items
        )
        if unhealthy:
            self._kubectl(
                [
                    "delete",
                    "pod",
                    "-n",
                    "openebs",
                    "-l",
                    "name=openebs-ndm",
                    "--wait=true",
                ],
                check=False,
            )

    @staticmethod
    def _has_active_chaos(resources: dict[str, Any]) -> bool:
        return any(payload.get("items") for payload in resources.values())

    @staticmethod
    def _all_active_pods_ready(pods: dict[str, Any]) -> bool:
        active = [
            pod
            for pod in pods.get("items", [])
            if not pod.get("metadata", {}).get("deletionTimestamp")
        ]
        if not active:
            return False
        return all(
            bool(pod.get("status", {}).get("containerStatuses", []))
            and all(
                status.get("ready", False)
                for status in pod.get("status", {}).get("containerStatuses", [])
            )
            for pod in active
        )

    def _kubectl(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["kubectl", *args], cwd=self.repo, capture_output=True, text=True, timeout=120, check=check)

    def _kubectl_json(self, args: list[str]) -> dict[str, Any]:
        result = self._kubectl(args)
        return json.loads(result.stdout)

    def _read_metric_evidence(self, path: Path) -> list[EvidenceItem]:
        if not path.exists():
            return [self._e("metrics-path-missing", "metric", str(path), "AIOpsLab returned a metrics path that does not exist", score=0.0)]
        files = [path] if path.is_file() else sorted(path.rglob("*.csv"))
        results: list[EvidenceItem] = []
        for index, csv_path in enumerate(files[:40]):
            try:
                frame = pd.read_csv(csv_path)
                numeric = frame.select_dtypes(include="number")
                if numeric.empty:
                    continue
                relative_spread = float(((numeric.max() - numeric.min()) / (numeric.abs().median() + 1e-9)).max())
                semantic_signal = bool(
                    re.search(
                        r"(?i)error|latency|duration|restart|unavailable|failed",
                        csv_path.name,
                    )
                )
                score_cap = 0.95 if semantic_signal else 0.6
                score = min(score_cap, relative_spread / 10)
                service = self._service_from_text(csv_path.name)
                results.append(self._e(f"metric-{index}", "metric", str(csv_path), f"rows={len(frame)}, max_relative_spread={relative_spread:.3f}", service=service, value=round(relative_spread, 4), score=score))
            except Exception:
                continue
        return results or [self._e("metrics-empty", "metric", str(path), "No numeric metric CSV files were produced", score=0.0)]

    @staticmethod
    def _resolve_metrics_path(result: Any) -> Path:
        """Extract the export directory from AIOpsLab's human-readable result."""
        if isinstance(result, Path):
            return result

        text = str(result).strip()
        direct = Path(text)
        if "\n" not in text and direct.exists():
            return direct

        match = re.search(
            r"^Metrics data exported to directory:\s*(.+?)\s*$",
            text,
            flags=re.MULTILINE,
        )
        if not match:
            raise ValueError("AIOpsLab metric export result did not contain an output directory")
        return Path(match.group(1)).expanduser()

    @staticmethod
    def _service_from_text(text: str) -> str | None:
        for service in ["frontend", "geo", "profile", "rate", "recommendation", "reservation", "search", "user"]:
            if service in text.lower():
                return service
        return None

    @staticmethod
    def _summarize_logs(text: str) -> str:
        interesting = [line.strip() for line in text.splitlines() if re.search(r"(?i)error|exception|timeout|deadline|refused|failed|unavailable", line)]
        chosen = interesting[:8] or text.splitlines()[-5:]
        return " | ".join(chosen)[:1600]

    @staticmethod
    def _e(evidence_id: str, kind: str, source: str, summary: str, *, service: str | None = None, value: Any = None, score: float = 0.0) -> EvidenceItem:
        return EvidenceItem(evidence_id=evidence_id, kind=kind, source=source, summary=summary, service=service, value=value, anomaly_score=max(0.0, min(1.0, score)))
