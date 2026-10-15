from types import SimpleNamespace

from evidenceops.config import Settings
from evidenceops.llm import (
    OpenAICompatibleDiagnosisEngine,
    RuleBasedDiagnosisEngine,
    _canonicalize_root_cause,
    _reconcile_actions,
    _root_cause_from_direct_evidence,
    _select_prompt_evidence,
)
from evidenceops.models import EvidenceItem


def test_openai_compatible_engine_parses_structured_response(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"root_cause":"network_delay","confidence":0.88,'
                                '"excluded_causes":["pod_crash"],"actions":[]}'
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 123},
            }

    def fake_post(url, *, headers, json, timeout):
        captured.update(
            url=url,
            headers=headers,
            payload=json,
            timeout=timeout,
        )
        return FakeResponse()

    monkeypatch.setattr("evidenceops.llm.httpx.post", fake_post)
    settings = Settings(
        llm_base_url="https://llm.example/v1",
        llm_api_key="test-placeholder-key",
        llm_model="test-model",
    )
    engine = OpenAICompatibleDiagnosisEngine(settings)

    result = engine.analyze(
        "network_delay_hotel_res-detection-1",
        True,
        "user",
        [
            EvidenceItem(
                evidence_id="delay",
                kind="metric",
                source="prometheus",
                summary="P95 latency increased",
                service="user",
                anomaly_score=0.9,
            )
        ],
    )

    assert result["root_cause"] == "network_delay"
    assert result["confidence"] == 0.88
    assert engine.token_usage == 123
    assert engine.model_name == "test-model"
    assert engine.last_prompt_evidence_count == 1
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["payload"]["temperature"] == 0
    assert captured["payload"]["model"] == "test-model"
    assert captured["headers"]["Authorization"] == "Bearer test-placeholder-key"
    assert captured["timeout"] == 120


def test_openai_prompt_withholds_problem_id(monkeypatch):
    captured = SimpleNamespace(payload=None)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"root_cause":null,"confidence":0.9,'
                                '"excluded_causes":[],"actions":[]}'
                            )
                        }
                    }
                ]
            }

    def fake_post(_url, *, headers, json, timeout):
        captured.payload = json
        return FakeResponse()

    monkeypatch.setattr("evidenceops.llm.httpx.post", fake_post)
    engine = OpenAICompatibleDiagnosisEngine(
        Settings(
            llm_base_url="https://llm.example/v1",
            llm_api_key="test-placeholder-key",
            llm_model="test-model",
        )
    )

    engine.analyze("secret-ground-truth-id", False, None, [])

    user_content = captured.payload["messages"][1]["content"]
    assert "secret-ground-truth-id" not in user_content
    assert "withheld-to-prevent-ground-truth-leakage" in user_content


def test_rule_baseline_uses_evidence_not_problem_id():
    engine = RuleBasedDiagnosisEngine()
    evidence = [
        EvidenceItem(
            evidence_id="loss-signal",
            kind="metric",
            source="network-observer",
            summary="Packet loss increased to 65% while pods remained Ready",
            service="user",
            anomaly_score=0.97,
        )
    ]

    result = engine.analyze(
        "misleading-name-that-says-pod_failure",
        True,
        "user",
        evidence,
    )

    assert result["root_cause"] == "network_packet_loss"
    assert result["actions"][0]["action_type"] == "manual"


def test_rule_baseline_does_not_treat_runbook_text_as_observation():
    engine = RuleBasedDiagnosisEngine()
    evidence = [
        EvidenceItem(
            evidence_id="backoff",
            kind="k8s_event",
            source="pod/user",
            summary="BackOff restarting failed container",
            service="user",
            anomaly_score=1.0,
        ),
        EvidenceItem(
            evidence_id="distractor-runbook",
            kind="runbook",
            source="runbook/application-image",
            summary="A known-faulty image can cause application errors",
            anomaly_score=0.0,
        ),
    ]

    result = engine.analyze("opaque-scenario", True, "user", evidence)

    assert result["root_cause"] == "pod_failure"
    assert result["actions"][0]["action_type"] == "restart_deployment"


def test_prompt_evidence_is_bounded_localized_and_source_diverse():
    evidence = [
        EvidenceItem(
            evidence_id=f"healthy-{index}",
            kind="k8s_state",
            source=f"pod/healthy-{index}",
            summary="healthy pod",
            service=f"healthy-{index}",
            anomaly_score=0.0,
        )
        for index in range(40)
    ]
    evidence.extend(
        [
            EvidenceItem(evidence_id="target-image", kind="k8s_state", source="pod/geo", summary="faulty image", service="geo", anomaly_score=1.0),
            EvidenceItem(evidence_id="target-log", kind="log", source="pod/geo", summary="application error", service="geo", anomaly_score=0.9),
            EvidenceItem(evidence_id="event", kind="k8s_event", source="event/backoff", summary="BackOff", service="geo", anomaly_score=0.8),
            EvidenceItem(evidence_id="metric", kind="metric", source="prometheus", summary="error rate", anomaly_score=0.7),
            EvidenceItem(evidence_id="runbook", kind="runbook", source="runbook/app", summary="repair guidance"),
        ]
    )

    selected = _select_prompt_evidence(evidence, service="geo", limit=18)

    assert len(selected) <= 18
    assert {"target-image", "target-log", "event", "metric", "runbook"} <= {
        item.evidence_id for item in selected
    }
    assert {item.kind for item in selected} >= {
        "k8s_state",
        "k8s_event",
        "log",
        "metric",
        "runbook",
    }


def test_root_cause_is_normalized_to_evaluation_taxonomy():
    assert (
        _canonicalize_root_cause("image/yinfangchen/geo:app3", [])
        == "application_image_misconfiguration"
    )
    assert _canonicalize_root_cause("high network latency", []) == "network_delay"
    assert _canonicalize_root_cause("CrashLoopBackOff", []) == "pod_failure"


def test_direct_chaos_evidence_overrides_implementation_detail():
    evidence = [
        EvidenceItem(
            evidence_id="chaos-pod-failure",
            kind="k8s_event",
            source="chaos/podchaos/pod-failure",
            summary="active action=pod-failure, fault_signal=pod_failure, target_service=user",
            service="user",
            anomaly_score=1.0,
        )
    ]
    assert _root_cause_from_direct_evidence(evidence) == "pod_failure"
    actions = _reconcile_actions(
        "pod_failure",
        [{"action_type": "restore_deployment_image", "target": "user"}],
        "user",
    )
    assert actions[0]["action_type"] == "restart_deployment"
    assert actions[0]["target"] == "user"
