"""Helpers for running on Databricks (notebooks and wheel/python jobs)."""

from __future__ import annotations

from typing import Any


def get_dbutils() -> Any:
    """Return ``dbutils`` both in notebooks and in python_wheel_task / script jobs,
    where it is not injected as a global."""
    try:
        import IPython

        shell = IPython.get_ipython()
        if shell is not None and "dbutils" in shell.user_ns:
            return shell.user_ns["dbutils"]
    except ImportError:
        pass
    try:
        from pyspark.dbutils import DBUtils  # available on Databricks runtimes
        from pyspark.sql import SparkSession

        return DBUtils(SparkSession.builder.getOrCreate())
    except ImportError as exc:
        raise RuntimeError("dbutils is only available when running on Databricks") from exc
