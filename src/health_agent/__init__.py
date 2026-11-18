"""Platform health agent: monitors ADF and Databricks, reports to Teams.

Notebook usage::

    from health_agent import Settings, run_health_check, render_text
    settings = Settings.from_databricks_secrets(dbutils, scope="health-agent")
    report = run_health_check(settings)          # dry-run remediation, Teams per threshold
    print(render_text(report))
"""

from health_agent.app import run_health_check
from health_agent.config import Settings
from health_agent.models import Finding, HealthReport, Severity
from health_agent.reporters.text import render_text

__version__ = "0.1.0"
__all__ = ["Finding", "HealthReport", "Settings", "Severity", "render_text", "run_health_check"]
