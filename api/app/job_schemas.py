"""Validated job admission HTTP schemas shared by job and batch routes."""
from pydantic import BaseModel, ConfigDict, Field

from .gpu import loras as lora_registry


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = "mock"
    shutdown_after_job: bool = True
    max_runtime_minutes: int = Field(default=60, ge=1, le=1440)
    idle_grace_minutes: int = 0


class JobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    model_id: str
    workflow_key: str
    workflow_version: int | str
    params: dict = Field(default_factory=dict)
    asset_ids: list[str] = Field(default_factory=list, max_length=10)
    loras: list[dict] = Field(default_factory=list, max_length=lora_registry.MAX_LORAS)
    preset_id: str | None = None
    execution_policy: Policy = Field(default_factory=Policy)


