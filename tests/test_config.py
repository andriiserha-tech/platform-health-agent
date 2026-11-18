import pytest
from pydantic import ValidationError

from health_agent.config import Settings
from tests.conftest import ns


def test_defaults_monitor_nothing() -> None:
    s = Settings()
    assert not s.adf_enabled
    assert not s.databricks_enabled


def test_host_is_normalised() -> None:
    s = Settings(databricks_host="adb-1.azuredatabricks.net/")
    assert s.databricks_host == "https://adb-1.azuredatabricks.net"
    assert s.databricks_enabled


def test_partial_adf_config_is_rejected() -> None:
    with pytest.raises(ValidationError, match="ADF config is partial"):
        Settings(adf_subscription_id="sub")


def test_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEALTH_AGENT_LOOKBACK_HOURS", "6")
    assert Settings().lookback_hours == 6


def test_dotenv_file_is_read(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / ".env").write_text("HEALTH_AGENT_CLUSTER_STUCK_MINUTES=7\n")
    assert Settings().cluster_stuck_minutes == 7


def test_allowlist_parsing() -> None:
    s = Settings(remediation_cluster_allowlist=" a, b ,,c ")
    assert s.cluster_allowlist == frozenset({"a", "b", "c"})


def test_databricks_runtime_enables_databricks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABRICKS_RUNTIME_VERSION", "15.4")
    assert Settings().databricks_enabled
    assert not Settings(enable_databricks=False).databricks_enabled


def test_secrets_are_hidden_in_repr() -> None:
    s = Settings(databricks_host="h", databricks_token="dapi-secret")
    assert "dapi-secret" not in repr(s)


class FakeSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def list(self, scope: str) -> list:  # type: ignore[type-arg]
        return [ns(key=k) for k in self.values]

    def get(self, scope: str, key: str) -> str:
        return self.values[key]


def test_from_databricks_secrets_kebab_and_overrides() -> None:
    dbutils = ns(
        secrets=FakeSecrets(
            {
                "teams-webhook-url": "https://example.test/hook",
                "adf_subscription_id": "sub",
                "adf-resource-group": "rg",
                "adf-factory-name": "df",
                "lookback-hours": "48",
            }
        )
    )
    s = Settings.from_databricks_secrets(dbutils, "scope", lookback_hours=3)
    assert s.teams_webhook_url is not None
    assert s.teams_webhook_url.get_secret_value() == "https://example.test/hook"
    assert s.adf_enabled
    assert s.lookback_hours == 3  # explicit override wins
