"""Phase 0 smoke tests — prove the package imports, the CLI is wired, and the
fixture is well-formed. No network, no heavy deps."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from lessons import __version__
from lessons.cli import app

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.json"


def test_version_string():
    assert __version__


def test_cli_help_lists_all_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("ingest", "extract", "enrich", "serve", "version"):
        assert cmd in result.output


def test_version_command():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "lessons" in result.output


def test_pass_stubs_report_not_implemented():
    # ingest is implemented (Phase 2); extract/enrich/serve remain stubs.
    for cmd in ("extract", "enrich", "serve"):
        result = runner.invoke(app, [cmd])
        assert result.exit_code == 0
        assert "not implemented yet" in result.output


def test_fixture_is_wellformed():
    data = json.loads(FIXTURE.read_text())
    assert data["video"]["id"]
    assert data["lines"]
    assert all("start_s" in line and "text" in line for line in data["lines"])
    assert data["expected"]["nodes"]
