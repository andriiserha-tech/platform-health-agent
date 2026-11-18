# Databricks notebook source
# MAGIC %md
# MAGIC # Platform health check
# MAGIC Runs the `platform-health-agent` wheel interactively. Settings come from a secret scope
# MAGIC (see the project README). Remediation is a **dry run** unless `remediate` is `true`.

# COMMAND ----------

dbutils.widgets.text(
    "wheel_path", "/Volumes/main/default/wheels/platform_health_agent-0.1.0-py3-none-any.whl"
)
dbutils.widgets.text("secret_scope", "health-agent")
dbutils.widgets.dropdown("remediate", "false", ["false", "true"])
dbutils.widgets.dropdown("notify", "false", ["false", "true"])

# COMMAND ----------

# MAGIC %pip install --quiet $wheel_path

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import logging

from health_agent import Settings, render_text, run_health_check

logging.basicConfig(level=logging.INFO, force=True)

settings = Settings.from_databricks_secrets(dbutils, scope=dbutils.widgets.get("secret_scope"))
report = run_health_check(
    settings,
    remediate=dbutils.widgets.get("remediate") == "true",
    notify=dbutils.widgets.get("notify") == "true",
)
print(render_text(report))

# COMMAND ----------

if report.findings:
    display(spark.createDataFrame(report.to_rows()))
