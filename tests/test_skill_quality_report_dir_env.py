"""Tests for scripts/skill-quality-report.sh env-var handling."""

import os
import subprocess
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRAPPER = os.path.join(REPO_ROOT, "scripts", "skill-quality-report.sh")


def test_wrapper_is_executable():
    """The wrapper must be marked executable for cron to run it."""
    assert os.access(WRAPPER, os.X_OK), f"{WRAPPER} is not executable"


def test_wrapper_respects_quality_report_dir_env_var(tmp_path, monkeypatch):
    """When SKILL_EVOLUTION_QUALITY_REPORT_DIR is set, the wrapper uses it.

    We invoke the wrapper with a stub python3 that captures the --output value
    instead of running skill_quality.py, so the test exercises the bash expansion
    (SCRIPT_DIR, env var, mkdir) without requiring a real LLM call.
    """
    custom_dir = tmp_path / "custom-reports"
    custom_dir.mkdir()

    captured_output = tmp_path / "captured-output"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "python3"
    stub.write_text(f"""#!/bin/sh
while [ $# -gt 0 ]; do
  if [ "$1" = "--output" ]; then
    echo "$2" > {captured_output}
    touch "$2"
  fi
  shift
done
exit 0
""")
    stub.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + ":" + env.get("PATH", "")
    env["SKILL_EVOLUTION_QUALITY_REPORT_DIR"] = str(custom_dir)

    result = subprocess.run(
        [WRAPPER],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, f"wrapper failed: {result.stderr}"
    assert captured_output.is_file(), f"stub did not capture --output. stderr={result.stderr!r}"

    captured_path = captured_output.read_text().strip()
    actual_dir = os.path.realpath(os.path.dirname(captured_path))
    expected_dir = os.path.realpath(str(custom_dir))
    assert actual_dir == expected_dir, (
        f"captured path {captured_path!r} resolves to {actual_dir!r}, "
        f"not the expected custom dir {expected_dir!r}"
    )
    assert os.path.basename(captured_path).startswith("skill-quality-")
    dated_files = [p for p in os.listdir(expected_dir) if p.startswith("skill-quality-")]
    assert len(dated_files) == 1, f"no dated report under {expected_dir}"


def test_wrapper_default_report_dir_is_repo_reports(tmp_path, monkeypatch):
    """Without the env var, the wrapper falls back to <repo>/reports/.

    We capture the --output argument the wrapper hands to skill_quality.py and
    verify the path it produces is under the default <repo>/reports/ directory.
    """
    captured_output = tmp_path / "captured-output"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "python3"
    stub.write_text(f"""#!/bin/sh
while [ $# -gt 0 ]; do
  if [ "$1" = "--output" ]; then
    echo "$2" > {captured_output}
  fi
  shift
done
exit 0
""")
    stub.chmod(0o755)

    # The stub only records the path: creating it would write an empty report into the
    # real <repo>/reports/ on every run. The env var is dropped from the child's copy of
    # the environment itself -- delenv after os.environ.copy() did not reach the child.
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + ":" + env.get("PATH", "")
    env.pop("SKILL_EVOLUTION_QUALITY_REPORT_DIR", None)

    result = subprocess.run(
        [WRAPPER],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, f"wrapper failed: {result.stderr}"
    assert captured_output.is_file(), f"stub did not capture --output. stderr={result.stderr!r}"

    captured_path = captured_output.read_text().strip()
    expected_reports_dir = os.path.realpath(os.path.join(REPO_ROOT, "reports"))
    actual_reports_dir = os.path.realpath(os.path.dirname(captured_path))
    assert actual_reports_dir == expected_reports_dir, (
        f"default report path {captured_path!r} resolves to {actual_reports_dir!r}, "
        f"not the expected {expected_reports_dir!r}"
    )
    assert os.path.basename(captured_path).startswith("skill-quality-")
