import csv
import json

from typer.testing import CliRunner

from evidenceops.cli import app


def test_fit_calibration_writes_deployable_temperature(tmp_path):
    records = tmp_path / "records.csv"
    with records.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["raw_confidence", "diagnosis_correct"],
        )
        writer.writeheader()
        writer.writerows(
            [
                {"raw_confidence": "0.95", "diagnosis_correct": "true"},
                {"raw_confidence": "0.90", "diagnosis_correct": "false"},
                {"raw_confidence": "0.70", "diagnosis_correct": "true"},
                {"raw_confidence": "0.60", "diagnosis_correct": "false"},
            ]
        )
    output = tmp_path / "calibration.json"

    result = CliRunner().invoke(
        app,
        ["fit-calibration", "--records", str(records), "--output", str(output)],
    )

    assert result.exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["sample_count"] == 4
    assert payload["temperature"] > 0
    assert payload["deployment_setting"].startswith("CONFIDENCE_TEMPERATURE=")
    assert "held-out" in payload["warning"]
