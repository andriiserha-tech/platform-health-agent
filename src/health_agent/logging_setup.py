"""Logging is configured once, by the entry point — never at import time."""

import logging


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # The Azure SDK is very chatty at INFO.
    logging.getLogger("azure").setLevel(logging.WARNING)
