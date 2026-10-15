from evidenceops.agent import EvidenceOpsAgent
from evidenceops.comparison import run_model_comparison
from evidenceops.config import PROJECT_ROOT, Settings
from evidenceops.evaluation import load_suite
from evidenceops.llm import RuleBasedDiagnosisEngine
from evidenceops.providers.fixture import FixtureProvider


def test_model_comparison_writes_machine_and_human_readable_outputs(tmp_path):
    settings = Settings(runbook_dir=PROJECT_ROOT / "knowledge" / "runbooks")

    def factory(_spec):
        return EvidenceOpsAgent(FixtureProvider(), settings, RuleBasedDiagnosisEngine())

    result = run_model_comparison(
        factory,
        ["rule-baseline"],
        load_suite(PROJECT_ROOT / "configs" / "cases.yaml")[:2],
        tmp_path,
    )

    assert result["models"][0]["root_cause_accuracy"] == 1.0
    assert (tmp_path / "comparison.json").exists()
    assert (tmp_path / "comparison.csv").exists()
    assert (tmp_path / "comparison.md").exists()
