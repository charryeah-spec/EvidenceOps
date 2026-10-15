from __future__ import annotations

from typing import Protocol

from evidenceops.models import (
    ExecutedAction,
    ObservationBundle,
    RecommendedAction,
    VerificationResult,
)


class TelemetryProvider(Protocol):
    def prepare(self, problem_id: str) -> str: ...

    def collect(self, problem_id: str) -> ObservationBundle: ...

    def execute(self, action: RecommendedAction, *, approved: bool, dry_run: bool) -> ExecutedAction: ...

    def verify(self, problem_id: str, action: RecommendedAction | None = None) -> VerificationResult: ...

    def cleanup(self, problem_id: str) -> None: ...

