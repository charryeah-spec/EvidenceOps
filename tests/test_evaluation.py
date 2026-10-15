from evidenceops.agent import EvidenceOpsAgent
from evidenceops.config import PROJECT_ROOT, Settings
from evidenceops.evaluation import calculate_metrics, load_suite
from evidenceops.llm import RuleBasedDiagnosisEngine
from evidenceops.models import EvaluationRecord
from evidenceops.providers.fixture import FixtureProvider
from evidenceops.reporting import write_report
from evidenceops.snapshot import summarize_reports


def test_suite_contains_required_24_cases():
    cases = load_suite(PROJECT_ROOT / "configs" / "cases.yaml")
    assert len(cases) == 24
    assert sum(case.expected_detected for case in cases) == 20


def test_metrics_follow_course_definition():
    settings = Settings(runbook_dir=PROJECT_ROOT / "knowledge" / "runbooks")
    agent = EvidenceOpsAgent(FixtureProvider(), settings, RuleBasedDiagnosisEngine())
    cases = load_suite(PROJECT_ROOT / "configs" / "cases.yaml")
    records = [EvaluationRecord(ground_truth=case, report=agent.run(case.problem_id)) for case in cases]
    metrics = calculate_metrics(records)
    assert metrics["course_detection_accuracy"] == 1.0
    assert metrics["overall_accuracy"] == 1.0
    assert metrics["root_cause_accuracy"] == 1.0
    assert metrics["synthetic_fixture_results"] is True
    assert metrics["unique_problem_count"] == 5
    assert metrics["unique_fault_problem_count"] == 4
    assert metrics["repeated_case_count"] == 19
    assert metrics["detection_accuracy_95ci"]["method"] == "Wilson score"
    assert metrics["detection_accuracy_95ci"]["low"] < 1.0


def test_repair_metrics_are_reported_for_approved_cases():
    settings = Settings(runbook_dir=PROJECT_ROOT / "knowledge" / "runbooks")
    agent = EvidenceOpsAgent(FixtureProvider(), settings, RuleBasedDiagnosisEngine())
    cases = load_suite(PROJECT_ROOT / "configs" / "repair-cases.yaml")[:2]
    records = []
    for case in cases:
        records.append(
            EvaluationRecord(
                ground_truth=case,
                report=agent.run(case.problem_id, mode="repair", approved=True),
                variant="full-repair",
            )
        )
        agent.provider.cleanup(case.problem_id)

    metrics = calculate_metrics(records)

    assert metrics["repair_case_count"] == 2
    assert metrics["repair_success_rate"] == 1.0
    assert metrics["avg_recovery_time_seconds"] is not None
    assert metrics["invalid_operation_rate"] == 0.0


def test_abstention_does_not_receive_root_cause_credit():
    settings = Settings(runbook_dir=PROJECT_ROOT / "knowledge" / "runbooks")
    agent = EvidenceOpsAgent(FixtureProvider(), settings, RuleBasedDiagnosisEngine())
    case = load_suite(PROJECT_ROOT / "configs" / "cases.yaml")[0]
    report = agent.run(case.problem_id)
    report.abstained = True

    metrics = calculate_metrics(
        [EvaluationRecord(ground_truth=case, report=report)]
    )

    assert metrics["root_cause_accuracy"] == 0.0
    assert metrics["coverage"] == 0.0


def test_suite_reports_per_run_tokens_instead_of_cumulative_totals():
    class CountingEngine:
        model_name = "counting"

        def __init__(self):
            self.token_usage = 0

        def analyze(self, problem_id, detected, service, evidence, variant):
            self.token_usage += 100
            return RuleBasedDiagnosisEngine().analyze(
                problem_id,
                detected,
                service,
                evidence,
                variant,
            )

    settings = Settings(runbook_dir=PROJECT_ROOT / "knowledge" / "runbooks")
    agent = EvidenceOpsAgent(FixtureProvider(), settings, CountingEngine())
    cases = load_suite(PROJECT_ROOT / "configs" / "cases.yaml")[:2]
    records = [
        EvaluationRecord(ground_truth=case, report=agent.run(case.problem_id))
        for case in cases
    ]

    assert [record.report.token_usage for record in records] == [100, 100]
    assert calculate_metrics(records)["total_token_usage"] == 200


def test_saved_report_snapshot_is_hashed_and_uses_newest_run(tmp_path):
    settings = Settings(runbook_dir=PROJECT_ROOT / "knowledge" / "runbooks")
    agent = EvidenceOpsAgent(FixtureProvider(), settings, RuleBasedDiagnosisEngine())
    cases = load_suite(PROJECT_ROOT / "configs" / "research-pilot-cases.yaml")[:2]
    reports_dir = tmp_path / "reports"
    expected_run_ids = []
    for case in cases:
        first = agent.run(case.problem_id)
        write_report(first, reports_dir)
        latest = agent.run(case.problem_id)
        write_report(latest, reports_dir)
        expected_run_ids.append(latest.run_id)

    records, metrics, manifest = summarize_reports(
        reports_dir,
        cases,
        tmp_path / "summary",
        label="real-qwen",
    )

    assert [record.report.run_id for record in records] == expected_run_ids
    assert metrics["case_count"] == 2
    assert metrics["total_token_usage"] == 0
    assert manifest["selection_policy"].startswith("newest successful")
    assert len(manifest["selected_reports"][0]["sha256"]) == 64
    assert (tmp_path / "summary" / "records-real-qwen.csv").exists()
    assert (tmp_path / "summary" / "figures" / "reliability-real-qwen.png").exists()
