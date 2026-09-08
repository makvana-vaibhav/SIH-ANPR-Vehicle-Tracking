"""Console logging for the lab.

The lab is an interactive tool, not a service, so this is human-readable rather
than JSON — the opposite of the production services' structlog setup, and
deliberately so.
"""

from __future__ import annotations

import logging
import sys

from rich.console import Console
from rich.logging import RichHandler

console = Console(stderr=True)
_configured = False


def setup(verbose: bool = False) -> None:
    global _configured
    if _configured:
        return
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=verbose)],
    )
    # These libraries narrate their own model loading; we do not need it.
    for noisy in ("PIL", "matplotlib", "urllib3", "onnxruntime"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(name)


def die(message: str, code: int = 1) -> None:
    console.print(f"[bold red]error[/bold red] {message}")
    sys.exit(code)
