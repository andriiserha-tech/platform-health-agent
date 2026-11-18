"""CLI entry point `health-agent`; also the Databricks python_wheel_task entry point."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from health_agent.app import run_health_check
from health_agent.config import Settings
from health_agent.logging_setup import configure_logging
from health_agent.models import Severity
from health_agent.reporters.text import render_text

FAIL_ON = {"never": None, "warning": Severity.WARNING, "critical": Severity.CRITICAL}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="health-agent",
        description="Check ADF and Databricks health and report to Teams.",
    )
    p.add_argument(
        "--remediate",
        action="store_true",
        help="actually restart eligible stuck clusters (default: dry-run only)",
    )
    p.add_argument("--no-notify", action="store_true", help="do not post to Teams")
    p.add_argument("--lookback-hours", type=int, help="override HEALTH_AGENT_LOOKBACK_HOURS")
    p.add_argument(
        "--fail-on",
        choices=list(FAIL_ON),
        default="never",
        help="exit non-zero at this status, so a scheduled job shows as failed",
    )
    p.add_argument("--json", action="store_true", help="print findings as JSON")
    p.add_argument("--secret-scope", help="(on Databricks) load settings from this secret scope")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def load_settings(args: argparse.Namespace) -> Settings:
    overrides = {}
    if args.lookback_hours:
        overrides["lookback_hours"] = args.lookback_hours
    if args.secret_scope:
        from health_agent.databricks_utils import get_dbutils

        return Settings.from_databricks_secrets(get_dbutils(), args.secret_scope, **overrides)
    return Settings(**overrides)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)

    report = run_health_check(
        load_settings(args), remediate=args.remediate, notify=not args.no_notify
    )

    if args.json:
        print(json.dumps(report.to_rows(), default=str, indent=2))
    else:
        print(render_text(report))

    threshold = FAIL_ON[args.fail_on]
    return 1 if threshold is not None and report.severity >= threshold else 0


if __name__ == "__main__":
    sys.exit(main())
