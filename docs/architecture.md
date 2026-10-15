# Architecture

```mermaid
flowchart LR
    U[CLI / Streamlit] --> S[Deterministic state machine]
    S --> O[AIOpsLab orchestrator]
    O --> K[Kind / Hotel Reservation]
    K --> P[Prometheus metrics]
    K --> L[Pod logs]
    K --> E[Kubernetes state and events]
    P --> N[Typed EvidenceItem layer]
    L --> N
    E --> N
    R[BM25 runbooks] --> S
    N --> S
    T[Service dependency graph] --> G[Topology-aware ranker]
    N --> G
    G --> S
    S --> M[OpenAI-compatible model or evidence-only baseline]
    M --> C[Temperature scaling and abstention]
    C --> I[IncidentReport JSON / Markdown]
    S --> A{Human approval?}
    A -->|No| I
    A -->|Yes| W[Typed context + confidence + semantic allowlist + dry run]
    W --> X[Restore image or restart Deployment]
    X --> V[Rollout, readiness, endpoints, health verification]
    V --> I
```

The language model receives normalized read-only evidence. It never executes a command. The state machine crosses the write boundary only after explicit approval, observed-evidence support, confidence and topology agreement, and policy validation.
