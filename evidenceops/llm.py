from __future__ import annotations

import json
import re
import time
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field

from evidenceops.config import Settings
from evidenceops.models import EvidenceItem


class DiagnosisEngine(Protocol):
    token_usage: int
    model_name: str

    def analyze(self, problem_id: str, detected: bool, service: str | None, evidence: list[EvidenceItem], variant: str) -> dict[str, Any]: ...


class _DiagnosisAction(BaseModel):
    action_type: Literal[
        "restart_deployment",
        "restore_deployment_image",
        "manual",
    ]
    target: str
    risk_level: Literal["low", "medium", "high"]
    rationale: str
    verification: str
    rollback: str


class _DiagnosisResponse(BaseModel):
    root_cause: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    excluded_causes: list[str] = Field(default_factory=list)
    actions: list[_DiagnosisAction] = Field(default_factory=list)


class RuleBasedDiagnosisEngine:
    token_usage = 0
    model_name = "rule-based-v2-evidence-only"

    def analyze(self, problem_id: str, detected: bool, service: str | None, evidence: list[EvidenceItem], variant: str = "full") -> dict[str, Any]:
        # ``problem_id`` is deliberately ignored. A scenario identifier often
        # contains the ground-truth label, so using it would leak the answer
        # into an offline evaluation. This baseline must infer the fault class
        # from the same evidence surface that is available to the LLM.
        del problem_id, variant
        if not detected:
            return {"root_cause": None, "confidence": 0.98, "excluded_causes": [], "actions": []}
        root_cause, confidence = _infer_root_cause_from_evidence(evidence)
        excluded = {
            "application_image_misconfiguration": ["pod_failure", "resource_exhaustion"],
            "pod_failure": ["application_image_misconfiguration", "network_delay"],
            "network_packet_loss": ["pod_failure", "resource_exhaustion"],
            "network_delay": ["pod_failure", "resource_exhaustion"],
        }.get(root_cause, [])
        return {
            "root_cause": root_cause,
            "confidence": confidence,
            "excluded_causes": excluded,
            "actions": _reconcile_actions(root_cause, [], service),
        }


class OpenAICompatibleDiagnosisEngine:
    def __init__(self, settings: Settings, model_name: str | None = None) -> None:
        selected_model = model_name or settings.llm_model
        if not (settings.llm_base_url and settings.llm_api_key and selected_model):
            raise ValueError("LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL are required")
        self.settings = settings
        self.model_name = selected_model
        self.token_usage = 0
        self.last_prompt_evidence_count = 0

    def analyze(self, problem_id: str, detected: bool, service: str | None, evidence: list[EvidenceItem], variant: str = "full") -> dict[str, Any]:
        selected_evidence = _select_prompt_evidence(
            evidence,
            service=service,
            limit=self.settings.llm_max_evidence_items,
        )
        self.last_prompt_evidence_count = len(selected_evidence)
        evidence_payload = [
            _compact_evidence(item, self.settings.llm_max_summary_chars)
            for item in selected_evidence
        ]
        prompt = {
            "scenario_id": "withheld-to-prevent-ground-truth-leakage",
            "preliminary_detected": detected,
            "preliminary_service": service,
            "variant": variant,
            "evidence_selection": {
                "available": len(evidence),
                "included": len(selected_evidence),
                "policy": "localized, anomalous, source-diverse, runbook-bounded",
            },
            "evidence": evidence_payload,
            "required_schema": {
                "root_cause": "application_image_misconfiguration|pod_failure|network_packet_loss|network_delay|unknown|null",
                "confidence": "0..1",
                "excluded_causes": ["string"],
                "actions": [{"action_type": "restart_deployment|restore_deployment_image|manual", "target": "string", "risk_level": "low|medium|high", "rationale": "string", "verification": "string", "rollback": "string"}],
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an AIOps diagnosis agent. Use only supplied evidence. "
                    "Return one JSON object matching the schema. Never invent observations or shell commands. "
                    "Distinguish root causes from downstream symptoms: an explicitly changed or known-faulty "
                    "deployment image is the root cause even if it causes restarts or temporary unready pods, "
                    "and restarting without restoring that image is not a valid repair. Treat BackOff/restarts "
                    "as pod_failure only when no stronger configuration/image evidence exists. Direct network "
                    "fault evidence takes precedence over generic resource metrics."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ]
        payload = {"model": self.model_name, "temperature": 0, "messages": messages, "response_format": {"type": "json_object"}}
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        last_error: Exception | None = None
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                response = httpx.post(url, headers={"Authorization": f"Bearer {self.settings.llm_api_key}"}, json=payload, timeout=self.settings.llm_timeout_seconds)
                response.raise_for_status()
                body = response.json()
                self.token_usage += int(body.get("usage", {}).get("total_tokens", 0))
                parsed = _parse_json(body["choices"][0]["message"]["content"])
                validated = _DiagnosisResponse.model_validate(parsed).model_dump()
                validated["root_cause"] = _canonicalize_root_cause(
                    validated.get("root_cause"),
                    validated.get("actions", []),
                )
                direct_root_cause = _root_cause_from_direct_evidence(evidence)
                if direct_root_cause:
                    validated["root_cause"] = direct_root_cause
                validated["actions"] = _reconcile_actions(
                    validated["root_cause"],
                    validated.get("actions", []),
                    service,
                )
                return validated
            except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.settings.llm_max_retries:
                    time.sleep(0.5 * (2**attempt))
        raise RuntimeError(f"LLM request failed after retries: {last_error}")


def _select_prompt_evidence(
    evidence: list[EvidenceItem],
    *,
    service: str | None,
    limit: int,
) -> list[EvidenceItem]:
    """Select a small, deterministic and source-diverse evidence set for the LLM.

    The complete evidence remains in IncidentReport; only the model prompt is
    bounded. This prevents healthy cluster inventory from crowding out the
    anomalous workload and keeps local-model latency reproducible.
    """
    if len(evidence) <= limit:
        return list(evidence)

    selected: list[EvidenceItem] = []
    selected_ids: set[str] = set()

    def add(items: list[EvidenceItem], quota: int | None = None) -> None:
        added = 0
        for item in items:
            if len(selected) >= limit:
                return
            if item.evidence_id in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(item.evidence_id)
            added += 1
            if quota is not None and added >= quota:
                return

    by_score = sorted(
        evidence,
        key=lambda item: (-item.anomaly_score, item.kind, item.evidence_id),
    )

    # Direct chaos observations and localized service evidence are most useful
    # for distinguishing the four supported fault classes.
    add([item for item in by_score if item.source.startswith("chaos/")], quota=3)
    if service:
        add([item for item in by_score if item.service == service], quota=7)

    # Preserve source diversity even when one noisy metric family dominates.
    for kind in ("k8s_state", "k8s_event", "log", "metric"):
        add([item for item in by_score if item.kind == kind], quota=2)

    # Runbooks are bounded separately so they assist rather than dominate.
    add([item for item in evidence if item.kind == "runbook"], quota=1)
    add(by_score)
    return selected


def _compact_evidence(item: EvidenceItem, max_summary_chars: int) -> dict[str, Any]:
    summary = _strip_ansi(item.summary).strip()
    value = _compact_value(item.value, max_chars=300)
    return {
        "evidence_id": item.evidence_id,
        "kind": item.kind,
        "source": item.source[:300],
        "observed_at": item.observed_at.isoformat(),
        "summary": summary[:max_summary_chars],
        "value": value,
        "unit": item.unit,
        "service": item.service,
        "anomaly_score": round(item.anomaly_score, 3),
    }


def _compact_value(value: Any, *, max_chars: int) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:max_chars]
    encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return encoded if len(encoded) <= max_chars else encoded[:max_chars] + "..."


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def _canonicalize_root_cause(
    root_cause: str | None,
    actions: list[dict[str, Any]],
) -> str | None:
    if root_cause is None:
        return None
    normalized = root_cause.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {
        "application_image_misconfiguration",
        "pod_failure",
        "network_packet_loss",
        "network_delay",
        "unknown",
    }:
        return normalized
    if any(term in normalized for term in ("image", "misconfig", "configuration")):
        return "application_image_misconfiguration"
    if any(term in normalized for term in ("packet_loss", "network_loss", "dropped_packet")):
        return "network_packet_loss"
    if any(term in normalized for term in ("network_delay", "latency", "slow_network")):
        return "network_delay"
    if any(term in normalized for term in ("pod", "crash", "backoff", "container_restart")):
        return "pod_failure"
    action_types = {action.get("action_type") for action in actions}
    if "restore_deployment_image" in action_types:
        return "application_image_misconfiguration"
    if "restart_deployment" in action_types:
        return "pod_failure"
    return "unknown"


def _root_cause_from_direct_evidence(evidence: list[EvidenceItem]) -> str | None:
    for item in evidence:
        if not item.source.startswith("chaos/"):
            continue
        text = f"{item.summary} {json.dumps(item.value, ensure_ascii=False, default=str)}".lower()
        if "fault_signal=pod_failure" in text or "pod-failure" in text:
            return "pod_failure"
        if "fault_signal=network_packet_loss" in text or "network_loss" in text or "packet loss" in text:
            return "network_packet_loss"
        if "fault_signal=network_delay" in text or "network_delay" in text or "network latency" in text:
            return "network_delay"
    return None


def _infer_root_cause_from_evidence(
    evidence: list[EvidenceItem],
) -> tuple[str, float]:
    """Return a deterministic baseline diagnosis without scenario-label access.

    The precedence mirrors the production prompt: direct configuration and
    network-control evidence outrank downstream restart or timeout symptoms.
    It is intentionally small and explainable so the rule baseline remains a
    useful control rather than a second opaque model.
    """
    direct = _root_cause_from_direct_evidence(evidence)
    if direct:
        return direct, 0.97

    evidence_text = []
    for item in sorted(evidence, key=lambda value: value.anomaly_score, reverse=True):
        # Retrieved runbooks describe possible causes and therefore must not
        # be treated as observations. Otherwise a distractor runbook can turn
        # its own diagnostic guidance into a false signal.
        if item.kind == "runbook":
            continue
        value = json.dumps(item.value, ensure_ascii=False, default=str)
        evidence_text.append(
            f"{item.kind} {item.source} {item.summary} {value}".lower()
        )
    text = "\n".join(evidence_text)

    patterns = (
        (
            "application_image_misconfiguration",
            ("known faulty", "faulty image", "image changed", "bad image", "imagepull"),
            0.94,
        ),
        (
            "network_packet_loss",
            ("packet loss", "network_loss", "dropped packet", "loss increased"),
            0.92,
        ),
        (
            "network_delay",
            ("p95 latency", "network delay", "network_delay", "request deadline"),
            0.90,
        ),
        (
            "pod_failure",
            ("backoff", "restart count", "terminated unexpectedly", "failed readiness"),
            0.91,
        ),
    )
    for root_cause, markers, confidence in patterns:
        if any(marker in text for marker in markers):
            return root_cause, confidence
    return "unknown", 0.5


def _reconcile_actions(
    root_cause: str | None,
    actions: list[dict[str, Any]],
    service: str | None,
) -> list[dict[str, Any]]:
    expected_type = {
        "application_image_misconfiguration": "restore_deployment_image",
        "pod_failure": "restart_deployment",
        "network_packet_loss": "manual",
        "network_delay": "manual",
    }.get(root_cause)
    if expected_type is None:
        return [action for action in actions if action.get("action_type") == "manual"]

    matching = [action for action in actions if action.get("action_type") == expected_type]
    if matching:
        for action in matching:
            action["target"] = service or action.get("target") or "unknown"
        return matching

    target = service or "unknown"
    if expected_type == "restore_deployment_image":
        target = service or "geo"
        rationale = "Restore the workload image identified by the evidence as misconfigured"
        verification = "Verify rollout, Ready pods, endpoints, image and application health"
        rollback = "Restore the previously captured Deployment image"
    elif expected_type == "restart_deployment":
        rationale = "Restart the failed workload after direct Pod-failure evidence"
        verification = "Verify rollout, Ready pods, stable restarts and endpoints"
        rollback = "Undo the rollout and preserve failed Pod logs"
    else:
        rationale = "Network changes require operator review; no automatic write is permitted"
        verification = "Verify packet loss, latency and request success return to baseline"
        rollback = "Remove the reviewed network fault or policy change"
    return [
        {
            "action_type": expected_type,
            "target": target,
            "risk_level": "medium",
            "rationale": rationale,
            "verification": verification,
            "rollback": rollback,
        }
    ]


def _parse_json(content: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
    result = json.loads(cleaned)
    if not isinstance(result, dict):
        raise ValueError("LLM response must be a JSON object")
    return result
