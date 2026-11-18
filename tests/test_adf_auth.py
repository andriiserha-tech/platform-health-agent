from typing import Any

import pytest
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from pydantic import ValidationError

import health_agent.databricks_utils as databricks_utils
from health_agent.collectors.adf import azure_credential
from health_agent.config import Settings
from tests.conftest import ns

SP = {"azure_tenant_id": "t", "azure_client_id": "c", "azure_client_secret": "s"}


@pytest.fixture
def fake_dbutils(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    requested: list[str] = []

    def provider(name: str) -> Any:
        requested.append(name)
        return ns(kind="service-credential", name=name)

    dbutils = ns(credentials=ns(getServiceCredentialsProvider=provider))
    monkeypatch.setattr(databricks_utils, "get_dbutils", lambda: dbutils)
    return requested


def test_service_credential_wins_over_service_principal(fake_dbutils: list[str]) -> None:
    cred = azure_credential(Settings(azure_service_credential="adf-monitor", **SP))
    assert cred.kind == "service-credential"
    assert fake_dbutils == ["adf-monitor"]


def test_service_principal_when_configured(fake_dbutils: list[str]) -> None:
    assert isinstance(azure_credential(Settings(**SP)), ClientSecretCredential)
    assert fake_dbutils == []


def test_default_chain_otherwise(fake_dbutils: list[str]) -> None:
    assert isinstance(azure_credential(Settings()), DefaultAzureCredential)


def test_partial_service_principal_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Service principal config is partial"):
        Settings(azure_client_id="c")
