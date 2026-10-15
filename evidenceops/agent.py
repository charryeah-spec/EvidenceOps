from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime

from evidenceops.confidence import temperature_scale
from evidenceops.config import Settings
from evidenceops.llm import DiagnosisEngine, RuleBasedDiagnosisEngine
from evidenceops.models import (
    AgentPhase,
    EvidenceItem,
    ExecutedAction,
    IncidentContext,
    IncidentReport,
    PhaseTrace,
    RecommendedAction,
    VerificationResult,
)
from evidenceops.providers.base import TelemetryProvider
from evidenceops.runbooks import RunbookRetriever
from evidenceops.signals import SignalDetector
from evidenceops.tools import SafetyPolicy
from evidenceops.topology import ServiceTopology


class EvidenceOpsAgent:
    def __init__(
        self,
        provider: TelemetryProvider,
        settings: Settings,
        diagnosis_engine: DiagnosisEngine | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self.engine = diagnosis_engine or RuleBasedDiagnosisEngine()
        self.detector = SignalDetector()
        self.runbooks = RunbookRetriever(settings.runbook_dir)
        self.safety = SafetyPolicy(
            settings.evidenceops_namespace,
            min_confidence=settings.min_diagnosis_confidence,
        )
        self.topology = (
            ServiceTopology.from_yaml(settings.topology_path)
            if settings.topology_enabled and settings.topology_path.exists()
            else ServiceTopology.empty()
        )

    def run(
        self,
        problem_id: str,
        *,
        mode: str = "diagnose",
        approved: bool = False,
        variant: str = "full",
    ) -> IncidentReport:
        if mode not in {"diagnose", "repair"}:
            raise ValueError("mode must be diagnose or repair")
        if variant not in {"raw", "evidence", "full"}:
            raise ValueError("variant must be raw, evidence, or full")

        started_at = datetime.now(UTC)
        monotonic_start = time.monotonic()
        engine_tokens_start = int(getattr(self.engine, "token_usage", 0))
        run_id = f"INC-{started_at:%Y%m%d}-{uuid.uuid4().hex[:8]}"
        trace: list[PhaseTrace] = []
        error: str | None = None
        executed = []
        verification: VerificationResult | None = None
        tool_calls = 0

        try:
            self.provider.prepare(problem_id)
            observations = self.provider.collect(problem_id)
            tool_calls += 1
        except Exception as exc:
            error = f"Provider setup/collection failed: {exc}"
            trace.append(self._trace(AgentPhase.REPORT, error))
            finished_at = datetime.now(UTC)
            return IncidentReport(
                run_id=run_id,
                problem_id=problem_id,
                provider=type(self.provider).__name__,
                diagnosis_engine=type(self.engine).__name__,
                model_name=getattr(self.engine, "model_name", "unknown"),
                variant=variant,
                started_at=started_at,
                finished_at=finished_at,
                detected=False,
                approval_status="not_required",
                response_time_seconds=round(time.monotonic() - monotonic_start, 4),
                tool_call_count=tool_calls,
                token_usage=max(
                    0,
                    int(getattr(self.engine, "token_usage", 0))
                    - engine_tokens_start,
                ),
                phase_trace=trace,
                error=error,
            )

        detected, preliminary_confidence = self.detector.detect(observations)
        trace.append(self._trace(AgentPhase.DETECT, f"detected={detected}; signal_confidence={preliminary_confidence:.3f}"))

        detector_service = self.detector.localize(observations.evidence) if detected else None
        topology_analysis = self.topology.analyze(observations.evidence) if detected else None
        topology_service = (
            topology_analysis.selected_service
            if topology_analysis and topology_analysis.confidence >= 0.5
            else None
        )
        faulty_service = topology_service or detector_service
        trace.append(
            self._trace(
                AgentPhase.LOCALIZE,
                f"candidate_service={faulty_service or 'none'}; "
                f"signal_service={detector_service or 'none'}; "
                f"topology_service={topology_service or 'none'}; "
                f"topology_confidence={topology_analysis.confidence if topology_analysis else 0.0:.3f}",
            )
        )

        if variant == "raw":
            raw_text = json.dumps(observations.raw or {"description": observations.description, "evidence": [item.model_dump(mode="json") for item in observations.evidence]}, ensure_ascii=False)
            analysis_evidence = [EvidenceItem(evidence_id="raw-observation", kind="log", source="provider/raw", summary=raw_text[:30000])]
        else:
            analysis_evidence = list(observations.evidence)
        if detected and variant == "full":
            direct_fault_evidence = [
                item
                for item in observations.evidence
                if item.source.startswith("chaos/")
            ]
            ranked_evidence = direct_fault_evidence or sorted(
                observations.evidence,
                key=lambda item: item.anomaly_score,
                reverse=True,
            )[:12]
            query = f"{faulty_service or ''} " + " ".join(
                item.summary for item in ranked_evidence
            )
            # One high-relevance runbook prevents a lower-ranked document from
            # injecting a contradictory fault class into the model context.
            runbook_evidence = self.runbooks.search(query, limit=1)
            analysis_evidence.extend(runbook_evidence)
            tool_calls += 1

        if detected:
            try:
                analysis = self.engine.analyze(
                    problem_id,
                    detected,
                    faulty_service,
                    analysis_evidence,
                    variant,
                )
            except Exception as exc:  # Record the model error in the incident report.
                analysis = {
                    "root_cause": "analysis_failed",
                    "confidence": preliminary_confidence,
                    "excluded_causes": [],
                    "actions": [],
                }
                error = str(exc)
        else:
            # Detection is the diagnosis gate. A healthy window uses the
            # detector's healthy-decision confidence and never spends model
            # tokens or allows a generative backend to create an incident.
            analysis = {
                "root_cause": None,
                "confidence": preliminary_confidence,
                "excluded_causes": [],
                "actions": [],
            }
        raw_confidence = float(analysis.get("confidence", preliminary_confidence) or 0.0)
        confidence = temperature_scale(
            max(0.0, min(1.0, raw_confidence)),
            self.settings.confidence_temperature,
        )
        root_cause = analysis.get("root_cause")
        abstained = bool(
            detected
            and (
                root_cause in {None, "unknown", "analysis_failed"}
                or confidence < self.settings.min_diagnosis_confidence
            )
        )
        abstention_reason = None
        if abstained:
            if root_cause in {None, "unknown", "analysis_failed"}:
                abstention_reason = "No supported root-cause class reached a decision"
            else:
                abstention_reason = (
                    f"Calibrated confidence {confidence:.3f} is below "
                    f"the {self.settings.min_diagnosis_confidence:.3f} threshold"
                )
            analysis["actions"] = []
        supporting_evidence_ids = [
            item.evidence_id
            for item in observations.evidence
            if item.kind != "runbook"
            and item.service == faulty_service
            and item.anomaly_score >= 0.75
        ][:12]
        incident_context = IncidentContext(
            root_cause=root_cause,
            faulty_service=faulty_service,
            confidence=confidence,
            supporting_evidence_ids=supporting_evidence_ids,
            topology_service=topology_service,
            abstained=abstained,
        )
        diagnosis_summary = f"root_cause={analysis.get('root_cause')}; evidence_count={len(analysis_evidence)}"
        prompt_evidence_count = (
            getattr(self.engine, "last_prompt_evidence_count", None)
            if detected
            else None
        )
        if prompt_evidence_count is not None:
            diagnosis_summary += f"; prompt_evidence_count={prompt_evidence_count}"
        diagnosis_summary += f"; calibrated_confidence={confidence:.3f}; abstained={abstained}"
        trace.append(self._trace(AgentPhase.DIAGNOSE, diagnosis_summary))

        actions = (
            self._build_actions(problem_id, analysis.get("actions", []))
            if detected and not abstained
            else []
        )
        trace.append(self._trace(AgentPhase.PLAN, f"recommended_actions={len(actions)}"))

        actionable = [action for action in actions if action.action_type in self.safety.ALLOWED_ACTIONS]
        if mode == "repair" and actionable:
            approval_status = "approved" if approved else "pending"
            trace.append(self._trace(AgentPhase.APPROVE, f"approval_status={approval_status}"))
            if approved:
                successful_actions: list[RecommendedAction] = []
                for action in actionable:
                    allowed, reason = self.safety.validate(
                        action,
                        approved=True,
                        context=incident_context,
                    )
                    if not allowed:
                        error = reason if error is None else f"{error}; {reason}"
                        continue
                    try:
                        dry_run_result = self.provider.execute(
                            action,
                            approved=True,
                            dry_run=True,
                        )
                    except Exception as exc:
                        dry_run_result = self._failed_execution(
                            action,
                            dry_run=True,
                            message=f"Dry run failed: {exc}",
                        )
                    executed.append(dry_run_result)
                    tool_calls += 1
                    if not dry_run_result.success:
                        error = self._append_error(error, dry_run_result.message)
                        continue
                    try:
                        result = self.provider.execute(
                            action,
                            approved=True,
                            dry_run=False,
                        )
                    except Exception as exc:
                        result = self._failed_execution(
                            action,
                            dry_run=False,
                            message=f"Execution failed: {exc}",
                        )
                    executed.append(result)
                    tool_calls += 1
                    if result.success:
                        successful_actions.append(action)
                    else:
                        error = self._append_error(error, result.message)
                trace.append(self._trace(AgentPhase.EXECUTE, f"executed_records={len(executed)}"))
                if successful_actions:
                    try:
                        verification = self.provider.verify(
                            problem_id,
                            successful_actions[0],
                        )
                    except Exception as exc:
                        verification = VerificationResult(
                            healthy=False,
                            checks=[f"Verification failed: {exc}"],
                            message="Recovery verification could not be completed",
                        )
                        error = self._append_error(error, str(exc))
                    tool_calls += 1
                    trace.append(
                        self._trace(
                            AgentPhase.VERIFY,
                            f"healthy={verification.healthy}",
                        )
                    )
                else:
                    verification = VerificationResult(
                        healthy=False,
                        checks=["No approved write action completed successfully"],
                        message="Recovery was not verified because execution failed",
                    )
                    trace.append(
                        self._trace(AgentPhase.VERIFY, "healthy=False; no successful action")
                    )
        elif mode == "repair" and not actionable:
            approval_status = "not_required"
            trace.append(self._trace(AgentPhase.APPROVE, "No whitelisted remediation is available"))
        else:
            approval_status = "pending" if actionable else "not_required"

        risk_level = self._max_risk(actions)
        trace.append(self._trace(AgentPhase.REPORT, "Structured incident report generated"))
        finished_at = datetime.now(UTC)
        return IncidentReport(
            run_id=run_id,
            problem_id=problem_id,
            provider=type(self.provider).__name__,
            diagnosis_engine=type(self.engine).__name__,
            model_name=getattr(self.engine, "model_name", "unknown"),
            variant=variant,
            started_at=started_at,
            finished_at=finished_at,
            detected=detected,
            faulty_service=faulty_service,
            root_cause=root_cause,
            confidence=max(0.0, min(1.0, confidence)),
            raw_confidence=max(0.0, min(1.0, raw_confidence)),
            confidence_temperature=self.settings.confidence_temperature,
            abstained=abstained,
            abstention_reason=abstention_reason,
            topology_analysis=topology_analysis,
            incident_context=incident_context,
            excluded_causes=list(analysis.get("excluded_causes", [])),
            evidence=analysis_evidence,
            recommended_actions=actions,
            risk_level=risk_level,
            approval_status=approval_status,
            executed_actions=executed,
            verification=verification,
            response_time_seconds=round(time.monotonic() - monotonic_start, 4),
            tool_call_count=tool_calls,
            token_usage=max(
                0,
                int(getattr(self.engine, "token_usage", 0))
                - engine_tokens_start,
            ),
            phase_trace=trace,
            error=error,
        )

    def remediate(self, report: IncidentReport, *, approved: bool) -> IncidentReport:
        """Resume a diagnosed incident at the approval boundary.

        This is used by the long-running dashboard so approval acts on the same
        injected incident instead of starting a second experiment.
        """
        started = time.monotonic()
        actionable = [
            action
            for action in report.recommended_actions
            if action.action_type in self.safety.ALLOWED_ACTIONS
        ]
        report.phase_trace.append(
            self._trace(
                AgentPhase.APPROVE,
                f"approval_status={'approved' if approved else 'rejected'}",
            )
        )
        report.approval_status = "approved" if approved else "rejected"
        if not approved or not actionable:
            report.finished_at = datetime.now(UTC)
            report.response_time_seconds = round(
                report.response_time_seconds + time.monotonic() - started,
                4,
            )
            return report

        executed = list(report.executed_actions)
        successful_actions: list[RecommendedAction] = []
        context = report.incident_context or self._context_from_report(report)
        for action in actionable:
            allowed, reason = self.safety.validate(
                action,
                approved=True,
                context=context,
            )
            if not allowed:
                report.error = self._append_error(report.error, reason)
                continue
            try:
                dry_run_result = self.provider.execute(
                    action,
                    approved=True,
                    dry_run=True,
                )
            except Exception as exc:
                dry_run_result = self._failed_execution(
                    action,
                    dry_run=True,
                    message=f"Dry run failed: {exc}",
                )
            executed.append(dry_run_result)
            report.tool_call_count += 1
            if not dry_run_result.success:
                report.error = self._append_error(report.error, dry_run_result.message)
                continue
            try:
                result = self.provider.execute(
                    action,
                    approved=True,
                    dry_run=False,
                )
            except Exception as exc:
                result = self._failed_execution(
                    action,
                    dry_run=False,
                    message=f"Execution failed: {exc}",
                )
            executed.append(result)
            report.tool_call_count += 1
            if result.success:
                successful_actions.append(action)
            else:
                report.error = self._append_error(report.error, result.message)

        report.executed_actions = executed
        report.phase_trace.append(
            self._trace(AgentPhase.EXECUTE, f"executed_records={len(executed)}")
        )
        if successful_actions:
            try:
                report.verification = self.provider.verify(
                    report.problem_id,
                    successful_actions[0],
                )
            except Exception as exc:
                report.verification = VerificationResult(
                    healthy=False,
                    checks=[f"Verification failed: {exc}"],
                    message="Recovery verification could not be completed",
                )
                report.error = self._append_error(report.error, str(exc))
            report.tool_call_count += 1
            report.phase_trace.append(
                self._trace(
                    AgentPhase.VERIFY,
                    f"healthy={report.verification.healthy}",
                )
            )
        else:
            report.verification = VerificationResult(
                healthy=False,
                checks=["No approved write action completed successfully"],
                message="Recovery was not verified because execution failed",
            )
            report.phase_trace.append(
                self._trace(AgentPhase.VERIFY, "healthy=False; no successful action")
            )
        report.phase_trace.append(
            self._trace(AgentPhase.REPORT, "Incident report updated after remediation")
        )
        report.finished_at = datetime.now(UTC)
        report.response_time_seconds = round(
            report.response_time_seconds + time.monotonic() - started,
            4,
        )
        return report

    @staticmethod
    def _context_from_report(report: IncidentReport) -> IncidentContext:
        supporting = [
            item.evidence_id
            for item in report.evidence
            if item.kind != "runbook"
            and item.service == report.faulty_service
            and item.anomaly_score >= 0.75
        ][:12]
        topology_service = (
            report.topology_analysis.selected_service
            if report.topology_analysis
            else None
        )
        return IncidentContext(
            root_cause=report.root_cause,
            faulty_service=report.faulty_service,
            confidence=report.confidence,
            supporting_evidence_ids=supporting,
            topology_service=topology_service,
            abstained=report.abstained,
        )

    def _build_actions(self, problem_id: str, raw_actions: list[dict]) -> list[RecommendedAction]:
        actions: list[RecommendedAction] = []
        for raw in raw_actions:
            action_type = raw.get("action_type", "manual")
            parameters = dict(raw.get("parameters", {}))
            parameters["problem_id"] = problem_id
            if action_type == "restore_deployment_image":
                parameters.setdefault("container", "hotel-reserv-geo")
                parameters.setdefault("image", "yinfangchen/hotelreservation:latest")
            actions.append(
                RecommendedAction(
                    action_type=action_type,
                    target=raw.get("target") or "unknown",
                    namespace=self.settings.evidenceops_namespace,
                    parameters=parameters,
                    rationale=raw.get("rationale", "No rationale supplied"),
                    risk_level=raw.get("risk_level", "medium"),
                    verification=raw.get("verification", "Verify workload health"),
                    rollback=raw.get("rollback", "Restore the previous known-good state"),
                )
            )
        return actions

    @staticmethod
    def _trace(phase: AgentPhase, summary: str) -> PhaseTrace:
        now = datetime.now(UTC)
        return PhaseTrace(phase=phase, started_at=now, finished_at=now, summary=summary)

    @staticmethod
    def _max_risk(actions: list[RecommendedAction]) -> str:
        if not actions:
            return "none"
        order = {"low": 1, "medium": 2, "high": 3}
        return max((action.risk_level for action in actions), key=order.get)

    @staticmethod
    def _append_error(current: str | None, message: str) -> str:
        return message if current is None else f"{current}; {message}"

    @staticmethod
    def _failed_execution(
        action: RecommendedAction,
        *,
        dry_run: bool,
        message: str,
    ) -> ExecutedAction:
        now = datetime.now(UTC)
        return ExecutedAction(
            action_type=action.action_type,
            target=action.target,
            approved=True,
            dry_run=dry_run,
            success=False,
            message=message,
            started_at=now,
            finished_at=now,
        )
