from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class AgentPhase(StrEnum):
    DETECT = "DETECT"
    LOCALIZE = "LOCALIZE"
    DIAGNOSE = "DIAGNOSE"
    PLAN = "PLAN"
    APPROVE = "APPROVE"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    REPORT = "REPORT"


class EvidenceItem(BaseModel):
    evidence_id: str
    kind: Literal["metric", "log", "k8s_event", "k8s_state", "runbook"]
    source: str
    observed_at: datetime = Field(default_factory=utc_now)
    summary: str
    value: Any | None = None
    unit: str | None = None
    service: str | None = None
    anomaly_score: float = Field(default=0.0, ge=0.0, le=1.0)


class ObservationBundle(BaseModel):
    problem_id: str
    description: str = ""
    evidence: list[EvidenceItem] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class RecommendedAction(BaseModel):
    action_type: Literal["restart_deployment", "restore_deployment_image", "manual"]
    target: str
    namespace: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    risk_level: Literal["low", "medium", "high"] = "medium"
    verification: str
    rollback: str


class ExecutedAction(BaseModel):
    action_type: str
    target: str
    approved: bool
    dry_run: bool
    success: bool
    message: str
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)


class VerificationResult(BaseModel):
    healthy: bool
    checks: list[str] = Field(default_factory=list)
    message: str = ""


class PhaseTrace(BaseModel):
    phase: AgentPhase
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)
    summary: str


class TopologyServiceScore(BaseModel):
    service: str
    direct_score: float = Field(ge=0.0)
    propagated_score: float = Field(ge=0.0)
    total_score: float = Field(ge=0.0)
    supporting_evidence_ids: list[str] = Field(default_factory=list)


class TopologyAnalysis(BaseModel):
    selected_service: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    graph_node_count: int = Field(default=0, ge=0)
    graph_edge_count: int = Field(default=0, ge=0)
    scores: list[TopologyServiceScore] = Field(default_factory=list)
    explanation: str = ""


class IncidentContext(BaseModel):
    root_cause: str | None = None
    faulty_service: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    topology_service: str | None = None
    abstained: bool = False


class IncidentReport(BaseModel):
    run_id: str
    problem_id: str
    provider: str = "unknown"
    diagnosis_engine: str = "unknown"
    model_name: str = "unknown"
    variant: str = "full"
    started_at: datetime
    finished_at: datetime
    detected: bool
    faulty_service: str | None = None
    root_cause: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_temperature: float = Field(default=1.0, gt=0.0)
    abstained: bool = False
    abstention_reason: str | None = None
    topology_analysis: TopologyAnalysis | None = None
    incident_context: IncidentContext | None = None
    excluded_causes: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    risk_level: Literal["none", "low", "medium", "high"] = "none"
    approval_status: Literal["not_required", "pending", "approved", "rejected"] = "not_required"
    executed_actions: list[ExecutedAction] = Field(default_factory=list)
    verification: VerificationResult | None = None
    response_time_seconds: float = Field(ge=0.0)
    tool_call_count: int = Field(default=0, ge=0)
    token_usage: int = Field(default=0, ge=0)
    phase_trace: list[PhaseTrace] = Field(default_factory=list)
    error: str | None = None


class GroundTruth(BaseModel):
    case_id: str
    problem_id: str
    expected_detected: bool
    expected_service: str | None = None
    expected_root_cause: str | None = None
    fault_type: str
    auto_remediable: bool = False


class EvaluationRecord(BaseModel):
    ground_truth: GroundTruth
    report: IncidentReport
    variant: str = "full"
