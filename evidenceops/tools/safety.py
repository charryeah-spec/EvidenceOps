from __future__ import annotations

import re
from typing import ClassVar

from evidenceops.models import IncidentContext, RecommendedAction

_K8S_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


class SafetyPolicy:
    ALLOWED_ACTIONS: ClassVar[set[str]] = {
        "restart_deployment",
        "restore_deployment_image",
    }

    def __init__(
        self,
        namespace: str = "test-hotel-reservation",
        min_confidence: float = 0.65,
    ) -> None:
        self.namespace = namespace
        self.min_confidence = min_confidence

    def validate(
        self,
        action: RecommendedAction,
        *,
        approved: bool,
        context: IncidentContext | None = None,
    ) -> tuple[bool, str]:
        if not approved:
            return False, "Explicit approval is required"
        if action.action_type not in self.ALLOWED_ACTIONS:
            return False, f"Action {action.action_type} is not on the write whitelist"
        if action.namespace != self.namespace:
            return False, "Namespace is outside the approved scope"
        if not _K8S_NAME.fullmatch(action.target):
            return False, "Target is not a valid Kubernetes resource name"
        if context is None:
            return False, "A structured incident context is required"
        if context.abstained:
            return False, "Writes are blocked because diagnosis abstained"
        if context.confidence < self.min_confidence:
            return False, "Diagnosis confidence is below the write threshold"
        if not context.supporting_evidence_ids:
            return False, "No observed evidence supports this write"

        expected_action = {
            "application_image_misconfiguration": "restore_deployment_image",
            "pod_failure": "restart_deployment",
        }.get(context.root_cause)
        if expected_action is None:
            return False, "This incident class is recommendation-only"
        if action.action_type != expected_action:
            return False, "Action does not match the diagnosed incident class"
        if not context.faulty_service or action.target != context.faulty_service:
            return False, "Action target does not match the localized service"
        if context.topology_service and context.topology_service != context.faulty_service:
            return False, "Topology and localization disagree on the target service"
        if action.action_type == "restore_deployment_image":
            if action.target != "geo":
                return False, "Only the approved geo deployment may be restored"
            container = action.parameters.get("container")
            image = action.parameters.get("image")
            if container != "hotel-reserv-geo":
                return False, "Only the geo container is approved for image restoration"
            if image != "yinfangchen/hotelreservation:latest":
                return False, "Image is not the approved known-good Hotel Reservation image"
        if action.action_type == "restart_deployment" and action.target != "user":
            return False, "Only the approved user deployment may be restarted"
        return True, "Approved by safety policy"
