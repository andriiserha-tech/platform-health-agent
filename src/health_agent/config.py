"""Typed, validated settings loaded once from the environment (and an optional .env file)."""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def running_on_databricks() -> bool:
    """True inside a Databricks notebook or job (set by every Databricks runtime)."""
    return "DATABRICKS_RUNTIME_VERSION" in os.environ


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HEALTH_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Azure Data Factory — all three or none.
    adf_subscription_id: str | None = None
    adf_resource_group: str | None = None
    adf_factory_name: str | None = None

    # Service principal for ADF. Optional locally (az login works); required on a Databricks
    # cluster, where DefaultAzureCredential has nothing to authenticate with.
    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    azure_client_secret: SecretStr | None = None

    # Databricks. Inside a notebook/job both can be empty: the SDK uses the runtime's own auth.
    # Elsewhere the token is optional too; the SDK falls back to Azure CLI / managed identity.
    databricks_host: str | None = None
    databricks_token: SecretStr | None = None
    # Force Databricks monitoring on/off; default: on when a host is set or running on Databricks.
    enable_databricks: bool | None = None

    # Teams Workflows webhook.
    teams_webhook_url: SecretStr | None = None
    teams_timeout_seconds: float = Field(default=10, gt=0)
    # Post to Teams only when the overall status is at least this bad ("ok" = always post).
    teams_notify_on: Literal["ok", "warning", "critical"] = "warning"

    # Thresholds.
    lookback_hours: int = Field(default=24, ge=1, le=24 * 30)
    adf_stuck_minutes: int = Field(default=120, ge=1)
    cluster_stuck_minutes: int = Field(default=20, ge=1)

    # Remediation guard-rails.
    remediation_opt_in_tag: str = "auto_heal"
    remediation_cluster_allowlist: str = ""
    remediation_max_restarts_per_run: int = Field(default=3, ge=0)
    remediation_cooldown_minutes: int = Field(default=60, ge=0)

    @field_validator("databricks_host")
    @classmethod
    def _normalize_host(cls, value: str | None) -> str | None:
        if not value:
            return None
        value = value.strip().rstrip("/")
        if not value.startswith("https://"):
            value = f"https://{value.removeprefix('http://')}"
        return value

    @model_validator(mode="after")
    def _adf_all_or_nothing(self) -> Settings:
        parts = [self.adf_subscription_id, self.adf_resource_group, self.adf_factory_name]
        if any(parts) and not all(parts):
            raise ValueError(
                "ADF config is partial: set all of ADF_SUBSCRIPTION_ID, "
                "ADF_RESOURCE_GROUP and ADF_FACTORY_NAME, or none of them"
            )
        return self

    @property
    def adf_enabled(self) -> bool:
        return bool(self.adf_subscription_id)

    @property
    def databricks_enabled(self) -> bool:
        if self.enable_databricks is not None:
            return self.enable_databricks
        return bool(self.databricks_host) or running_on_databricks()

    @classmethod
    def from_databricks_secrets(
        cls, dbutils: Any, scope: str = "health-agent", **overrides: Any
    ) -> Settings:
        """Build settings inside a Databricks notebook from a secret scope.

        Secret keys are the setting names, either snake_case or kebab-case
        (e.g. ``teams-webhook-url``). Explicit ``overrides`` win over secrets,
        secrets win over environment variables.
        """
        available = {meta.key for meta in dbutils.secrets.list(scope)}
        values: dict[str, Any] = {}
        for name in cls.model_fields:
            for key in (name, name.replace("_", "-")):
                if key in available:
                    values[name] = dbutils.secrets.get(scope=scope, key=key)
                    break
        values.update(overrides)
        return cls(**values)

    @property
    def cluster_allowlist(self) -> frozenset[str]:
        return frozenset(
            item.strip() for item in self.remediation_cluster_allowlist.split(",") if item.strip()
        )
