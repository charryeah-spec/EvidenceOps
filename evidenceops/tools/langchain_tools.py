from __future__ import annotations

from langchain_core.tools import StructuredTool

from evidenceops.providers.base import TelemetryProvider


def build_read_tools(provider: TelemetryProvider) -> list[StructuredTool]:
    """Expose provider reads as LangChain tools while keeping writes outside the LLM loop."""
    cache = {}

    def bundle(problem_id: str):
        if problem_id not in cache:
            cache[problem_id] = provider.collect(problem_id)
        return cache[problem_id]

    def by_kind(problem_id: str, *kinds: str) -> dict:
        evidence = [
            item.model_dump(mode="json")
            for item in bundle(problem_id).evidence
            if item.kind in kinds
        ]
        return {"problem_id": problem_id, "evidence": evidence}

    def collect_observations(problem_id: str) -> str:
        """Collect normalized metrics, logs, and Kubernetes evidence for a problem."""
        return bundle(problem_id).model_dump_json()

    def query_metrics(problem_id: str) -> dict:
        """Return only Prometheus metric evidence for the active problem."""
        return by_kind(problem_id, "metric")

    def search_logs(problem_id: str) -> dict:
        """Return only bounded application and infrastructure log evidence."""
        return by_kind(problem_id, "log")

    def inspect_kubernetes(problem_id: str) -> dict:
        """Return normalized Pod, container image, Service, and Endpoint state."""
        return by_kind(problem_id, "k8s_state")

    def query_kubernetes_events(problem_id: str) -> dict:
        """Return Kubernetes event evidence for the active problem."""
        return by_kind(problem_id, "k8s_event")

    def verify_health(problem_id: str) -> str:
        """Run read-only recovery checks for a problem."""
        return provider.verify(problem_id).model_dump_json()

    return [
        StructuredTool.from_function(collect_observations),
        StructuredTool.from_function(query_metrics),
        StructuredTool.from_function(search_logs),
        StructuredTool.from_function(inspect_kubernetes),
        StructuredTool.from_function(query_kubernetes_events),
        StructuredTool.from_function(verify_health),
    ]
