from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2
    llm_max_evidence_items: int = Field(default=18, ge=8, le=100)
    llm_max_summary_chars: int = Field(default=600, ge=100, le=4000)
    evidenceops_namespace: str = "test-hotel-reservation"
    evidenceops_provider: str = "fixture"
    aiopslab_path: Path = Field(default=PROJECT_ROOT / "upstream" / "AIOpsLab")
    runbook_dir: Path = Field(default=PROJECT_ROOT / "knowledge" / "runbooks")
    topology_path: Path = Field(default=PROJECT_ROOT / "configs" / "service-topology.yaml")
    topology_enabled: bool = True
    min_diagnosis_confidence: float = Field(default=0.65, ge=0.0, le=1.0)
    confidence_temperature: float = Field(default=1.0, gt=0.0, le=10.0)
    results_dir: Path = Field(default=PROJECT_ROOT / "results")

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)
