from types import SimpleNamespace

from evidenceops.config import Settings
from evidenceops.models import EvidenceItem, RecommendedAction
from evidenceops.providers.aiopslab import AIOpsLabProvider
from evidenceops.providers.fixture import FixtureProvider
from evidenceops.signals import SignalDetector


def test_detects_and_localizes_misconfiguration():
    bundle = FixtureProvider().collect("misconfig_app_hotel_res-detection-1")
    detected, confidence = SignalDetector().detect(bundle)
    assert detected is True
    assert confidence > 0.9
    assert SignalDetector().localize(bundle.evidence) == "geo"


def test_normal_scenario_is_not_detected():
    bundle = FixtureProvider().collect("noop_detection_hotel_reservation-1")
    detected, confidence = SignalDetector().detect(bundle)
    assert detected is False
    assert confidence > 0.9


def test_resolve_aiopslab_metrics_path_from_export_message(tmp_path):
    metrics_dir = tmp_path / "metric_20260805_192451"
    metrics_dir.mkdir()
    message = (
        f"Metrics data exported to directory: {metrics_dir}\n\n"
        "Folder structure of exported metrics:\nmetric_20260805_192451/\n"
    )

    assert AIOpsLabProvider._resolve_metrics_path(message) == metrics_dir


def test_verify_ignores_terminating_old_pod(monkeypatch):
    provider = AIOpsLabProvider(Settings())
    provider.problem_id = "test-problem"
    provider.problem = SimpleNamespace(namespace="test-hotel-reservation")

    def fake_kubectl(args, *, check=True):
        return SimpleNamespace(returncode=0, stdout="deployment successfully rolled out", stderr="")

    def fake_json(args):
        resource = args[1]
        if resource == "pods":
            return {
                "items": [
                    {
                        "metadata": {"name": "geo-new"},
                        "status": {"containerStatuses": [{"ready": True}]},
                    },
                    {
                        "metadata": {
                            "name": "geo-old",
                            "deletionTimestamp": "2026-08-29T00:00:00Z",
                        },
                        "status": {"containerStatuses": [{"ready": False}]},
                    },
                ]
            }
        if resource == "endpoints":
            return {"subsets": [{"addresses": [{"ip": "10.0.0.1"}]}]}
        if resource == "deployment":
            return {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "hotel-reserv-geo",
                                    "image": "yinfangchen/hotelreservation:latest",
                                }
                            ]
                        }
                    }
                }
            }
        raise AssertionError(args)

    monkeypatch.setattr(provider, "_kubectl", fake_kubectl)
    monkeypatch.setattr(provider, "_kubectl_json", fake_json)
    action = SimpleNamespace(
        target="geo",
        action_type="restore_deployment_image",
        parameters={
            "container": "hotel-reserv-geo",
            "image": "yinfangchen/hotelreservation:latest",
        },
    )

    result = provider.verify("test-problem", action)

    assert result.healthy is True
    assert "ready_active_pods=1/1" in result.checks


def test_restart_dry_run_uses_server_side_patch(monkeypatch):
    provider = AIOpsLabProvider(Settings())
    captured = {}

    def fake_kubectl(args, *, check=True):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout="validated", stderr="")

    monkeypatch.setattr(provider, "_kubectl", fake_kubectl)
    action = RecommendedAction(
        action_type="restart_deployment",
        target="user",
        namespace="test-hotel-reservation",
        rationale="test",
        verification="test",
        rollback="test",
    )

    result = provider.execute(action, approved=True, dry_run=True)

    assert result.success is True
    assert captured["args"][0] == "patch"
    assert "--dry-run=server" in captured["args"]


def test_localization_prioritizes_active_chaos_selector_over_stale_logs():
    evidence = [
        EvidenceItem(
            evidence_id="stale-geo-log",
            kind="log",
            source="pod/geo-old",
            summary="old startup error",
            service="geo",
            anomaly_score=1.0,
        ),
        EvidenceItem(
            evidence_id="active-loss",
            kind="k8s_event",
            source="chaos/networkchaos/loss",
            summary="active network loss target_service=user",
            service="user",
            anomaly_score=1.0,
        ),
    ]

    assert SignalDetector().localize(evidence) == "user"


def test_localization_prioritizes_bad_image_over_downstream_restart_noise():
    evidence = [
        EvidenceItem(
            evidence_id="image-geo-faulty",
            kind="k8s_state",
            source="pod/geo-current",
            summary="container=geo, image=known-faulty",
            service="geo",
            anomaly_score=1.0,
        ),
        EvidenceItem(
            evidence_id="pod-user-unready",
            kind="k8s_state",
            source="pod/user-current",
            summary="phase=Running, ready=False, restarts=1",
            service="user",
            anomaly_score=0.95,
        ),
        EvidenceItem(
            evidence_id="event-user-backoff",
            kind="k8s_event",
            source="event/BackOff",
            summary="BackOff restarting user",
            service="user",
            anomaly_score=0.9,
        ),
    ]

    assert SignalDetector().localize(evidence) == "geo"


def test_generic_counter_metric_cannot_trigger_fault_by_itself(tmp_path):
    metric = tmp_path / "kpi_container_network_receive_bytes_total.csv"
    metric.write_text("timestamp,value\n1,0\n2,1000000\n", encoding="utf-8")
    provider = AIOpsLabProvider(Settings())

    evidence = provider._read_metric_evidence(metric)

    assert evidence[0].anomaly_score <= 0.6


def test_pod_stability_ignores_terminating_old_pods():
    pods = {
        "items": [
            {
                "metadata": {"name": "current"},
                "status": {"containerStatuses": [{"ready": True}]},
            },
            {
                "metadata": {
                    "name": "old",
                    "deletionTimestamp": "2026-08-29T00:00:00Z",
                },
                "status": {"containerStatuses": [{"ready": False}]},
            },
        ]
    }

    assert AIOpsLabProvider._all_active_pods_ready(pods) is True


def test_empty_chaos_resource_lists_do_not_disable_stabilization():
    resources = {
        "podchaos": {"items": []},
        "networkchaos": {"items": []},
    }

    assert AIOpsLabProvider._has_active_chaos(resources) is False
