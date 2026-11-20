"""Guards for the OctoPrint 2.x circular-import workaround.

OctoPrint 2.0.x (still in 2.0.1 stable) has a circular import:
octoprint.printer imports octoprint.filemanager, whose storage.printer module
imports PrinterFile back from the half-loaded octoprint.printer. The plugin
imports octoprint.filemanager first to sidestep it. Without that, the cycle
raises an ImportError that the plugin's try/except takes for OctoPrint 1.x,
and connector support disappears without a single error message.

Both checks run in a fresh interpreter: inside this pytest process,
octoprint.filemanager is long since imported by the other tests and the
OctoPrint package itself, which would hide the cycle completely.
"""

import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _octoprint_major():
    try:
        return int(version("OctoPrint").split(".")[0])
    except (PackageNotFoundError, ValueError):
        return None


requires_octoprint_2 = pytest.mark.skipif(
    (_octoprint_major() or 0) < 2,
    reason="the connector framework only exists on OctoPrint 2.x",
)


def _run_fresh(code):
    """Runs code in a new interpreter that imports the plugin from the repo."""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@requires_octoprint_2
def test_connector_framework_survives_a_fresh_import():
    # Fails when the workaround is removed while the cycle still exists.
    result = _run_fresh(
        "import sys, octoprint_autoconnectplus as plugin;"
        " sys.exit(0 if plugin.ConnectedPrinter is not None else 1)"
    )
    assert result.returncode == 0, (
        "ConnectedPrinter is None on OctoPrint 2.x - the circular-import"
        " workaround in octoprint_autoconnectplus/__init__.py is missing or"
        " no longer enough.\n" + result.stderr
    )


@requires_octoprint_2
@pytest.mark.xfail(
    strict=True,
    reason=(
        "OctoPrint's octoprint.printer <-> octoprint.filemanager cycle."
        " When this passes (XPASS), OctoPrint has fixed it and the"
        " `import octoprint.filemanager` workaround can be removed."
    ),
)
def test_octoprint_imports_connected_printer_without_the_workaround():
    result = _run_fresh(
        "from octoprint.printer.connection import ConnectedPrinter"
    )
    assert result.returncode == 0, result.stderr
