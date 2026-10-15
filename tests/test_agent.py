from evidenceops.agent import EvidenceOpsAgent
from evidenceops.config import PROJECT_ROOT, Settings
from evidenceops.llm import RuleBasedDiagnosisEngine
from evidenceops.providers.fixture import FixtureProvider
from evidenceops.tools.langchain_tools import build_read_tools


def make_agent():
    settings = Settings(runbook_dir=PROJECT_ROOT / "knowledge" / "runbooks")
    return EvidenceOpsAgent(FixtureProvider(), settings, RuleBasedDiagnosisEngine())


def test_diagnosis_has_multisource_evidence():
    report = make_agent().run("misconfig_app_hotel_res-detection-1")
    assert report.detected is True
    assert report.root_cause == "application_image_misconfiguration"
    assert report.model_name == "rule-based-v2-evidence-only"
    assert {item.kind for item in report.evidence} >= {"metric", "log", "k8s_state", "runbook"}
    assert report.executed_actions == []
    assert report.approval_status == "pending"
    assert report.topology_analysis
    assert report.topology_analysis.selected_service == "geo"
    assert report.incident_context
    assert report.incident_context.supporting_evidence_ids


def test_network_fault_retrieves_matching_runbook():
    report = make_agent().run("network_loss_hotel_res-detection-1")

    runbook_sources = {
        item.source for item in report.evidence if item.kind == "runbook"
    }
    assert any(source.endswith("network-loss.md") for source in runbook_sources)
    assert len(runbook_sources) == 1


def test_pod_fault_excludes_distractor_image_runbook():
    report = make_agent().run("pod_failure_hotel_res-detection-1")

    runbook_sources = [
        item.source for item in report.evidence if item.kind == "runbook"
    ]

    assert len(runbook_sources) == 1
    assert runbook_sources[0].endswith("pod-failure.md")


def test_normal_detection_gate_blocks_hallucinated_incident():
    class HallucinatingEngine:
        token_usage = 10
        model_name = "test-model"

        def analyze(self, problem_id, detected, service, evidence, variant):
            raise AssertionError("The model must not be called for a healthy window")

    settings = Settings(runbook_dir=PROJECT_ROOT / "knowledge" / "runbooks")
    report = EvidenceOpsAgent(
        FixtureProvider(), settings, HallucinatingEngine()
    ).run("noop_detection_hotel_reservation-1")

    assert report.detected is False
    assert report.root_cause is None
    assert report.recommended_actions == []
    assert report.error is None
    assert report.token_usage == 0


def test_low_confidence_diagnosis_abstains_and_blocks_actions():
    class UncertainEngine:
        token_usage = 0
        model_name = "uncertain"

        def analyze(self, problem_id, detected, service, evidence, variant):
            return {
                "root_cause": "pod_failure",
                "confidence": 0.4,
                "excluded_causes": [],
                "actions": [
                    {
                        "action_type": "restart_deployment",
                        "target": "user",
                        "risk_level": "low",
                        "rationale": "uncertain",
                        "verification": "verify",
                        "rollback": "rollback",
                    }
                ],
            }

    settings = Settings(runbook_dir=PROJECT_ROOT / "knowledge" / "runbooks")
    report = EvidenceOpsAgent(FixtureProvider(), settings, UncertainEngine()).run(
        "pod_failure_hotel_res-detection-1",
        mode="repair",
        approved=True,
    )

    assert report.detected is True
    assert report.abstained is True
    assert report.abstention_reason
    assert report.recommended_actions == []
    assert report.executed_actions == []


def test_repair_requires_approval():
    report = make_agent().run("pod_failure_hotel_res-detection-1", mode="repair", approved=False)
    assert report.approval_status == "pending"
    assert report.executed_actions == []


def test_approved_repair_runs_dry_run_and_verifies():
    report = make_agent().run("pod_failure_hotel_res-detection-1", mode="repair", approved=True)
    assert report.approval_status == "approved"
    assert len(report.executed_actions) == 2
    assert report.executed_actions[0].dry_run is True
    assert report.executed_actions[1].dry_run is False
    assert report.verification and report.verification.healthy is True


def test_dashboard_can_resume_same_incident_after_approval():
    agent = make_agent()
    report = agent.run("pod_failure_hotel_res-detection-1", mode="diagnose")

    updated = agent.remediate(report, approved=True)

    assert updated.approval_status == "approved"
    assert len(updated.executed_actions) == 2
    assert updated.verification and updated.verification.healthy is True


def test_langchain_surface_exposes_separate_read_only_tools():
    names = {tool.name for tool in build_read_tools(FixtureProvider())}

    assert names >= {
        "query_metrics",
        "search_logs",
        "inspect_kubernetes",
        "query_kubernetes_events",
        "verify_health",
    }


class FailingCollectProvider(FixtureProvider):
    def collect(self, problem_id):
        raise RuntimeError("telemetry unavailable")


class FailingExecuteProvider(FixtureProvider):
    def execute(self, action, *, approved, dry_run):
        raise RuntimeError("write backend unavailable")


def test_provider_failure_returns_machine_readable_report():
    settings = Settings(runbook_dir=PROJECT_ROOT / "knowledge" / "runbooks")
    agent = EvidenceOpsAgent(
        FailingCollectProvider(),
        settings,
        RuleBasedDiagnosisEngine(),
    )

    report = agent.run("pod_failure_hotel_res-detection-1")

    assert report.detected is False
    assert report.error and "telemetry unavailable" in report.error
    assert report.phase_trace[-1].phase == "REPORT"


def test_failed_write_is_not_reported_as_successful_recovery():
    settings = Settings(runbook_dir=PROJECT_ROOT / "knowledge" / "runbooks")
    agent = EvidenceOpsAgent(
        FailingExecuteProvider(),
        settings,
        RuleBasedDiagnosisEngine(),
    )

    report = agent.run(
        "pod_failure_hotel_res-detection-1",
        mode="repair",
        approved=True,
    )

    assert report.executed_actions
    assert not any(item.success for item in report.executed_actions)
    assert report.verification and report.verification.healthy is False
    assert report.error and "write backend unavailable" in report.error
