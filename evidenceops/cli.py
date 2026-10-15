from __future__ import annotations

import csv
import json
import platform
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from evidenceops.agent import EvidenceOpsAgent
from evidenceops.comparison import run_model_comparison
from evidenceops.confidence import (
    calibration_from_arrays,
    fit_temperature,
    temperature_scale,
)
from evidenceops.config import PROJECT_ROOT, Settings
from evidenceops.evaluation import load_suite, run_suite
from evidenceops.llm import OpenAICompatibleDiagnosisEngine, RuleBasedDiagnosisEngine
from evidenceops.providers.fixture import FixtureProvider
from evidenceops.reporting import write_report
from evidenceops.snapshot import summarize_reports

app = typer.Typer(no_args_is_help=True, help="Evidence-driven AIOps diagnosis and safe remediation")
console = Console()


def _build_agent(
    provider_name: str,
    engine_name: str,
    model_name: str | None = None,
) -> EvidenceOpsAgent:
    settings = Settings(evidenceops_provider=provider_name)
    if provider_name == "fixture":
        provider = FixtureProvider()
    elif provider_name == "aiopslab":
        from evidenceops.providers.aiopslab import AIOpsLabProvider
        provider = AIOpsLabProvider(settings)
    else:
        raise typer.BadParameter("provider must be fixture or aiopslab")
    if engine_name == "rule" or (engine_name == "auto" and not settings.llm_configured):
        engine = RuleBasedDiagnosisEngine()
    elif engine_name in {"api", "auto"}:
        engine = OpenAICompatibleDiagnosisEngine(settings, model_name=model_name)
    else:
        raise typer.BadParameter("engine must be auto, rule, or api")
    return EvidenceOpsAgent(provider, settings, engine)


@app.command()
def run(
    problem: str = typer.Option(..., help="AIOpsLab problem id"),
    mode: str = typer.Option("diagnose", help="diagnose or repair"),
    approve: bool = typer.Option(False, help="Explicitly approve whitelisted write actions"),
    provider: str = typer.Option("fixture", help="fixture or aiopslab"),
    engine: str = typer.Option("auto", help="auto, rule, or api"),
    variant: str = typer.Option("full", help="raw, evidence, or full"),
    output: Path = typer.Option(PROJECT_ROOT / "results" / "raw"),
) -> None:
    if mode not in {"diagnose", "repair"}:
        raise typer.BadParameter("mode must be diagnose or repair")
    agent = _build_agent(provider, engine)
    report = agent.run(problem, mode=mode, approved=approve, variant=variant)
    json_path, md_path = write_report(report, output)
    console.print_json(json.dumps(report.model_dump(mode="json"), ensure_ascii=False))
    console.print(f"Saved [bold]{json_path}[/bold] and [bold]{md_path}[/bold]")
    if report.error:
        raise typer.Exit(code=2)


@app.command()
def evaluate(
    suite: Path = typer.Option(PROJECT_ROOT / "configs" / "cases.yaml", exists=True),
    provider: str = typer.Option("fixture", help="fixture or aiopslab"),
    engine: str = typer.Option("auto", help="auto, rule, or api"),
    variant: str = typer.Option("full", help="raw, evidence, or full"),
    repair: bool = typer.Option(
        False,
        help="Approve and execute remediation for auto-remediable cases",
    ),
    output: Path = typer.Option(PROJECT_ROOT / "results"),
) -> None:
    agent = _build_agent(provider, engine)
    _, metrics = run_suite(
        agent,
        load_suite(suite),
        output,
        variant=variant,
        repair=repair,
    )
    console.print_json(json.dumps(metrics, ensure_ascii=False))


@app.command("summarize-reports")
def summarize_saved_reports(
    reports: Path = typer.Option(..., exists=True, file_okay=False),
    suite: Path = typer.Option(
        PROJECT_ROOT / "configs" / "research-pilot-cases.yaml",
        exists=True,
    ),
    output: Path = typer.Option(PROJECT_ROOT / "results" / "snapshot"),
    label: str = typer.Option("snapshot"),
) -> None:
    """Re-evaluate saved reports with a hashed, auditable selection manifest."""
    try:
        _, metrics, manifest = summarize_reports(
            reports,
            load_suite(suite),
            output,
            label=label,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print_json(
        json.dumps({"metrics": metrics, "manifest": manifest}, ensure_ascii=False)
    )


@app.command()
def compare(
    models: str = typer.Option(
        "rule",
        help="Comma-separated model specs. Use 'rule' or API model names.",
    ),
    suite: Path = typer.Option(PROJECT_ROOT / "configs" / "cases.yaml", exists=True),
    provider: str = typer.Option("fixture", help="fixture or aiopslab"),
    variant: str = typer.Option("full", help="raw, evidence, or full"),
    output: Path = typer.Option(PROJECT_ROOT / "results" / "comparison"),
) -> None:
    """Compare rule and OpenAI-compatible models under one metric contract."""
    model_specs = [item.strip() for item in models.split(",") if item.strip()]

    def build(spec: str) -> EvidenceOpsAgent:
        if spec == "rule":
            return _build_agent(provider, "rule")
        return _build_agent(provider, "api", model_name=spec)

    try:
        result = run_model_comparison(
            build,
            model_specs,
            load_suite(suite),
            output,
            variant=variant,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print_json(json.dumps(result, ensure_ascii=False))


@app.command("fit-calibration")
def fit_calibration(
    records: Path = typer.Option(..., exists=True, help="Calibration-split records CSV"),
    output: Path = typer.Option(
        PROJECT_ROOT / "results" / "confidence-calibration.json",
    ),
) -> None:
    """Fit temperature scaling on a calibration split, never on final test claims."""
    with records.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"raw_confidence", "diagnosis_correct"}
    if not rows or not required.issubset(rows[0]):
        raise typer.BadParameter(
            "CSV must contain raw_confidence and diagnosis_correct; run evaluate first"
        )
    try:
        confidences = [float(row["raw_confidence"]) for row in rows]
        outcomes = [row["diagnosis_correct"].strip().lower() == "true" for row in rows]
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter("Calibration columns contain invalid values") from exc
    temperature = fit_temperature(confidences, outcomes)
    calibrated = [temperature_scale(value, temperature) for value in confidences]
    result = {
        "sample_count": len(rows),
        "temperature": temperature,
        "before": calibration_from_arrays(confidences, outcomes),
        "after_on_calibration_split": calibration_from_arrays(calibrated, outcomes),
        "deployment_setting": f"CONFIDENCE_TEMPERATURE={temperature:.3f}",
        "warning": (
            "This parameter was fitted on the supplied calibration split. "
            "Report final ECE/Brier only on a separate held-out test split."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print_json(json.dumps(result, ensure_ascii=False))
    console.print(f"Saved [bold]{output}[/bold]")


@app.command()
def doctor() -> None:
    """Show local prerequisites without changing the machine."""
    table = Table("Component", "Status", "Version")
    table.add_row("python", "available", f"{platform.python_version()} ({sys.executable})")
    version_commands = {
        "docker": ["docker", "--version"],
        "kind": ["kind", "version"],
        "kubectl": ["kubectl", "version", "--client"],
        "helm": ["helm", "version", "--short"],
        "poetry (optional)": ["poetry", "--version"],
    }
    for label, version_command in version_commands.items():
        command = version_command[0]
        path_result = subprocess.run(
            ["/usr/bin/env", "which", command], capture_output=True, text=True, check=False
        )
        if path_result.returncode:
            status = "optional" if "optional" in label else "missing"
            table.add_row(label, status, "-")
            continue
        version_result = subprocess.run(
            version_command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        version = (version_result.stdout or version_result.stderr).splitlines()[0] if (version_result.stdout or version_result.stderr) else "installed"
        table.add_row(label, "available", version[:100])
    console.print(table)
