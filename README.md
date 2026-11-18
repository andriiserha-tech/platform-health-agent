# platform-health-agent

Monitors **Azure Data Factory** and **Databricks**, posts a summary to **Microsoft Teams**, and can
restart stuck Databricks clusters behind strict guard-rails. Ships as a Python wheel that runs:

- locally, from the command line: `health-agent`
- in a **Databricks notebook**: `from health_agent import run_health_check`
- as a scheduled **Databricks job**, deployed with the included Asset Bundle

## What it checks

| Source | Finding | Severity |
|---|---|---|
| ADF | pipeline has failed runs in the window | CRITICAL; WARNING if its latest run succeeded |
| ADF | pipeline's latest run was cancelled | WARNING |
| ADF | run queued or in progress longer than `ADF_STUCK_MINUTES` | WARNING |
| Databricks | cluster in `ERROR` or `UNKNOWN` | CRITICAL |
| Databricks | cluster terminated with `CLIENT_ERROR`, `SERVICE_FAULT` or `CLOUD_FAILURE` in the window (normal auto-termination is ignored) | WARNING |
| Databricks | cluster in `PENDING`, `RESTARTING`, `RESIZING` or `TERMINATING` longer than `CLUSTER_STUCK_MINUTES` | WARNING |
| Databricks | job has failed or timed-out runs in the window | CRITICAL; WARNING if its latest run succeeded |
| Any | the collector itself failed (auth, network) | CRITICAL; other sources still run |

ADF results are read across all pages. How long a cluster has been in its current state comes
from its latest cluster event, because the cluster list API doesn't return that.

## Auto-remediation (stuck clusters)

Off by default. Without `--remediate` / `remediate=True`, every decision is reported as `dry_run`.
A cluster is restarted only if **all** of these hold:

1. It opted in: custom tag `auto_heal=true` (tag name is configurable), or its id is in
   `REMEDIATION_CLUSTER_ALLOWLIST`.
2. It is not a job or pipeline cluster.
3. It is stuck in `RESIZING`. The restart API does nothing unless the cluster is running, so
   `PENDING`, `RESTARTING` and `TERMINATING` clusters are reported but left alone.
4. It was not restarted within `REMEDIATION_COOLDOWN_MINUTES`.
5. Fewer than `REMEDIATION_MAX_RESTARTS_PER_RUN` clusters have been restarted in this run.

If one restart fails, the error is recorded and the agent moves on to the next cluster.

## Configuration

All settings are environment variables with the `HEALTH_AGENT_` prefix (or a local `.env`;
see [`.env.example`](.env.example)). On Databricks they can come from a **secret scope** instead.
Secret keys are the setting names without the prefix, in kebab-case, for example
`teams-webhook-url`.

| Setting | Notes |
|---|---|
| `ADF_SUBSCRIPTION_ID`, `ADF_RESOURCE_GROUP`, `ADF_FACTORY_NAME` | all three, or none to skip ADF |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | service principal for ADF; **needed on Databricks**. Locally `az login` is enough |
| `DATABRICKS_HOST`, `DATABRICKS_TOKEN` | not needed inside Databricks; elsewhere the token is optional (Azure CLI auth also works) |
| `ENABLE_DATABRICKS` | force on/off; default is on when a host is set or running on Databricks |
| `TEAMS_WEBHOOK_URL` | Teams **Workflows** webhook (Adaptive Card) |
| `TEAMS_NOTIFY_ON` | `ok` (always post), `warning` (default), `critical` |
| `LOOKBACK_HOURS` (24), `ADF_STUCK_MINUTES` (120), `CLUSTER_STUCK_MINUTES` (20) | thresholds |
| `REMEDIATION_*` | see above |

**Permissions:** the service principal needs *Data Factory Contributor* or a reader role that
includes `pipelineruns/queryByFactory` on the factory. The Databricks identity needs to list
clusters and job runs, plus *Can Restart* on clusters that opt in to remediation.

### Teams webhook

Teams → channel → **Workflows** → *Post to a channel when a webhook request is received*, then
copy the URL. The old "Incoming Webhook" connectors (MessageCard) have been retired.

### Databricks secret scope

```bash
databricks secrets create-scope health-agent
databricks secrets put-secret health-agent teams-webhook-url
databricks secrets put-secret health-agent adf-subscription-id
databricks secrets put-secret health-agent adf-resource-group
databricks secrets put-secret health-agent adf-factory-name
databricks secrets put-secret health-agent azure-tenant-id
databricks secrets put-secret health-agent azure-client-id
databricks secrets put-secret health-agent azure-client-secret
```

## Usage

### Local

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in
health-agent --no-notify             # dry run, print only
health-agent --json --no-notify      # machine-readable
health-agent --fail-on critical      # exit 1 when CRITICAL (for schedulers)
```

### Databricks notebook

Build the wheel (`python -m build --wheel`), upload `dist/*.whl` to a Volume or workspace folder,
then use [`notebooks/health_check.py`](notebooks/health_check.py), or:

```python
%pip install /Volumes/main/default/wheels/platform_health_agent-0.1.0-py3-none-any.whl
dbutils.library.restartPython()

from health_agent import Settings, run_health_check, render_text
settings = Settings.from_databricks_secrets(dbutils, scope="health-agent")
report = run_health_check(settings, remediate=False, notify=False)
print(render_text(report))
display(spark.createDataFrame(report.to_rows()))
```

### Databricks job (Asset Bundle)

[`databricks.yml`](databricks.yml) builds the wheel and deploys an hourly serverless job that runs
`health-agent --secret-scope health-agent --fail-on critical`. The job is created **paused**.

```bash
databricks bundle deploy -t dev
databricks bundle run platform_health_check -t dev
```

## Development

```bash
pre-commit install          # gitleaks + ruff on every commit
ruff check . && ruff format --check . && mypy && pytest
```

Layout:

```
src/health_agent/
  config.py           typed settings (env / .env / Databricks secrets)
  models.py           Finding, CollectorResult, HealthReport, Severity
  collectors/         adf.py, databricks.py: read-only health collection
  remediation/        clusters.py: guarded restart of stuck clusters
  reporters/          teams.py (Adaptive Card), text.py
  app.py              run_health_check(): the single orchestration entry point
  cli.py              `health-agent` command and job entry point
```
