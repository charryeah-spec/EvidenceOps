from __future__ import annotations

import json
from pathlib import Path

from evidenceops.models import IncidentReport


def write_report(report: IncidentReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    md_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path


def to_markdown(report: IncidentReport) -> str:
    lines = [
        f"# Incident Report {report.run_id}",
        "",
        f"- Problem: `{report.problem_id}`",
        f"- Provider: `{report.provider}`",
        f"- Diagnosis engine: `{report.diagnosis_engine}`",
        f"- Model: `{report.model_name}`",
        f"- Variant: `{report.variant}`",
        f"- Started: `{report.started_at.isoformat()}`",
        f"- Finished: `{report.finished_at.isoformat()}`",
        f"- Detected: **{report.detected}**",
        f"- Faulty service: `{report.faulty_service or 'N/A'}`",
        f"- Root cause: `{report.root_cause or 'N/A'}`",
        f"- Calibrated confidence: **{report.confidence:.1%}**",
        f"- Raw confidence: **{report.raw_confidence if report.raw_confidence is not None else report.confidence:.1%}**",
        f"- Confidence temperature: **{report.confidence_temperature:.3f}**",
        f"- Abstained: **{report.abstained}**",
        f"- Abstention reason: {report.abstention_reason or 'N/A'}",
        f"- Risk: **{report.risk_level}**",
        f"- Approval: **{report.approval_status}**",
        f"- Response time: **{report.response_time_seconds:.3f}s**",
        f"- Tool calls: **{report.tool_call_count}**",
        f"- Token usage: **{report.token_usage}**",
        f"- Excluded causes: `{', '.join(report.excluded_causes) or 'N/A'}`",
        "",
        "## Evidence",
        "",
    ]
    lines.extend(
        (
            f"- `{item.evidence_id}` [{item.kind}] "
            f"source=`{item.source}`, service=`{item.service or 'N/A'}`, "
            f"score={item.anomaly_score:.3f}: {item.summary}"
        )
        for item in report.evidence
    )
    lines.extend(["", "## Topology-aware localization", ""])
    if report.topology_analysis:
        topology = report.topology_analysis
        lines.extend(
            [
                f"- Selected service: `{topology.selected_service or 'N/A'}`",
                f"- Ranking confidence: **{topology.confidence:.1%}**",
                f"- Graph: **{topology.graph_node_count} nodes / {topology.graph_edge_count} edges**",
                f"- Explanation: {topology.explanation}",
                "",
                "| Service | Direct | Propagated | Total | Supporting evidence |",
                "|---|---:|---:|---:|---|",
            ]
        )
        lines.extend(
            f"| `{score.service}` | {score.direct_score:.3f} | "
            f"{score.propagated_score:.3f} | {score.total_score:.3f} | "
            f"{', '.join(score.supporting_evidence_ids) or 'N/A'} |"
            for score in topology.scores
        )
    else:
        lines.append("- Topology analysis was not required or was disabled.")
    lines.extend(["", "## Recommended actions", ""])
    if report.recommended_actions:
        for action in report.recommended_actions:
            lines.append(
                f"- `{action.action_type}` on `{action.target}` "
                f"(risk={action.risk_level}): {action.rationale}; "
                f"verify: {action.verification}; rollback: {action.rollback}"
            )
    else:
        lines.append("- No action required.")
    lines.extend(["", "## Executed actions", ""])
    if report.executed_actions:
        lines.extend(
            (
                f"- `{action.action_type}` on `{action.target}`: "
                f"approved={action.approved}, dry_run={action.dry_run}, "
                f"success={action.success}; {action.message.strip()}"
            )
            for action in report.executed_actions
        )
    else:
        lines.append("- No write action executed.")
    lines.extend(["", "## Auditable phase trace", ""])
    lines.extend(f"- **{item.phase}**: {item.summary}" for item in report.phase_trace)
    if report.verification:
        lines.extend(
            [
                "",
                "## Recovery verification",
                "",
                f"- Healthy: **{report.verification.healthy}**",
                f"- {report.verification.message}",
                *[f"- Check: {check}" for check in report.verification.checks],
            ]
        )
    if report.error:
        lines.extend(["", "## Error", "", report.error])
    return "\n".join(lines) + "\n"
