from evidenceops.models import IncidentContext, RecommendedAction
from evidenceops.tools.safety import SafetyPolicy


def action(**overrides):
    data = {
        "action_type": "restart_deployment",
        "target": "reservation-service",
        "namespace": "test-hotel-reservation",
        "rationale": "test",
        "verification": "test",
        "rollback": "test",
    }
    data.update(overrides)
    return RecommendedAction(**data)


def context(**overrides):
    data = {
        "root_cause": "pod_failure",
        "faulty_service": "user",
        "confidence": 0.9,
        "supporting_evidence_ids": ["event-backoff"],
        "topology_service": "user",
    }
    data.update(overrides)
    return IncidentContext(**data)


def test_write_requires_explicit_approval():
    allowed, reason = SafetyPolicy().validate(action(), approved=False)
    assert allowed is False
    assert "approval" in reason.lower()


def test_rejects_invalid_target_and_namespace():
    assert SafetyPolicy().validate(action(target="bad;rm"), approved=True)[0] is False
    assert SafetyPolicy().validate(action(namespace="default"), approved=True)[0] is False


def test_validates_known_good_image():
    image_action = action(action_type="restore_deployment_image", target="geo", parameters={"container": "hotel-reserv-geo", "image": "yinfangchen/hotelreservation:latest"})
    image_context = context(
        root_cause="application_image_misconfiguration",
        faulty_service="geo",
        topology_service="geo",
    )
    assert SafetyPolicy().validate(image_action, approved=True, context=image_context)[0] is True
    bad_image = action(action_type="restore_deployment_image", target="geo", parameters={"container": "hotel-reserv-geo", "image": "attacker.invalid/image:latest"})
    assert SafetyPolicy().validate(bad_image, approved=True, context=image_context)[0] is False


def test_rejects_action_that_does_not_match_incident_class():
    wrong_for_config = action(
        action_type="restart_deployment",
        target="geo",
    )
    config_context = context(
        root_cause="application_image_misconfiguration",
        faulty_service="geo",
        topology_service="geo",
    )
    assert SafetyPolicy().validate(
        wrong_for_config,
        approved=True,
        context=config_context,
    )[0] is False

    wrong_for_pod = action(
        action_type="restore_deployment_image",
        target="geo",
        parameters={
            "container": "hotel-reserv-geo",
            "image": "yinfangchen/hotelreservation:latest",
        },
    )
    assert SafetyPolicy().validate(
        wrong_for_pod,
        approved=True,
        context=context(),
    )[0] is False


def test_write_requires_structured_supported_context():
    policy = SafetyPolicy()
    restart = action(target="user")
    assert policy.validate(restart, approved=True)[0] is False
    assert policy.validate(
        restart,
        approved=True,
        context=context(confidence=0.4),
    )[0] is False
    assert policy.validate(
        restart,
        approved=True,
        context=context(supporting_evidence_ids=[]),
    )[0] is False
