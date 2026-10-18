"""Suite-wide isolation: no test reads the developer's shell config or writes real data.

Two fixtures, both autouse:

- `_hermetic_env` (per test) drops every SKILL_EVOLUTION_* variable inherited from the
  shell and points each runtime path the pipeline resolves at call time -- history,
  proposals, reports, labels, session DB, Claude Code home -- at a per-test temp dir. The
  processed-session state file is redirected through the `STATE_FILE` module globals of
  state.py and fetch_sessions.py instead of its env var: that is how the state tests
  already redirect it, and the env var would override their own patches. The
  shell this repo is developed in sets provider variables, and a test that silently picked
  one up would pass here and fail in CI, or worse, reach a real provider.
- `_real_paths_untouched` (per session) fingerprints the real files and directories a
  leaking test would damage, and fails the run if any changed. It exists because a fixture
  that patched the wrong module global let the conformance tests write to, and then prune,
  the real ~/.hermes/skill_evolution_state.json on every run -- 101 real entries were lost
  before anyone noticed. Prevention alone is one forgotten monkeypatch away from that.

A test that checks a *default* path must delenv the variable itself; the defaults are
what this fixture deliberately keeps out of reach.
"""

import os
import sys
from typing import Dict, List, Optional, Tuple

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import fetch_sessions  # noqa: E402
import state  # noqa: E402

# Captured at import, from the developer's real environment, before any fixture runs.
_REAL_HOME = os.path.expanduser("~")
_REAL_CLAUDE_CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()

WATCHED_FILES = [
    os.path.join(_REAL_HOME, ".hermes", "skill_evolution_state.json"),
    os.path.join(_REAL_HOME, ".claude", "skill_evolution_state.json"),
    os.path.join(REPO_ROOT, "eval_history.jsonl"),
    os.path.join(REPO_ROOT, "eval_history.archive.jsonl"),
]
if _REAL_CLAUDE_CONFIG_DIR:
    WATCHED_FILES.append(os.path.join(_REAL_CLAUDE_CONFIG_DIR, "skill_evolution_state.json"))

WATCHED_DIRS = [
    os.path.join(REPO_ROOT, "proposals"),
    os.path.join(REPO_ROOT, "reports"),
    os.path.join(REPO_ROOT, "labels"),
    os.path.join(_REAL_HOME, ".hermes", "skills"),
    os.path.join(_REAL_HOME, ".claude", "skills"),
]
if _REAL_CLAUDE_CONFIG_DIR:
    WATCHED_DIRS.append(os.path.join(_REAL_CLAUDE_CONFIG_DIR, "skills"))


def fingerprint(path: str) -> Optional[Tuple]:
    """(size, mtime_ns) for a file, the sorted (name, mtime_ns) listing of a directory's
    first level, or None when the path does not exist. lstat, so a dangling symlink in a
    skills tree reads as itself instead of raising."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not os.path.isdir(path):
        return (st.st_size, st.st_mtime_ns)
    listing = []
    for name in os.listdir(path):
        try:
            listing.append((name, os.lstat(os.path.join(path, name)).st_mtime_ns))
        except FileNotFoundError:
            continue
    return tuple(sorted(listing))


def snapshot(paths: List[str]) -> Dict[str, Optional[Tuple]]:
    return {path: fingerprint(path) for path in paths}


def changed_paths(before: Dict[str, Optional[Tuple]]) -> List[str]:
    return [path for path, print_ in before.items() if fingerprint(path) != print_]


@pytest.fixture(autouse=True, scope="session")
def _real_paths_untouched():
    before = snapshot(WATCHED_FILES + WATCHED_DIRS)
    yield
    changed = changed_paths(before)
    if changed:
        pytest.fail("the test suite modified real paths: " + ", ".join(changed), pytrace=False)


@pytest.fixture(autouse=True)
def _hermetic_env(tmp_path_factory, monkeypatch):
    for key in list(os.environ):
        if key.startswith("SKILL_EVOLUTION_"):
            monkeypatch.delenv(key)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    base = tmp_path_factory.mktemp("runtime")
    state_file = str(base / "skill_evolution_state.json")
    monkeypatch.setattr(state, "STATE_FILE", state_file)
    monkeypatch.setattr(fetch_sessions, "STATE_FILE", state_file)
    monkeypatch.setenv("SKILL_EVOLUTION_HISTORY_PATH", str(base / "eval_history.jsonl"))
    monkeypatch.setenv("SKILL_EVOLUTION_PROPOSALS_DIR", str(base / "proposals"))
    monkeypatch.setenv("SKILL_EVOLUTION_QUALITY_REPORT_DIR", str(base / "reports"))
    monkeypatch.setenv("SKILL_EVOLUTION_LABELS_PATH", str(base / "labels" / "review_labels.jsonl"))
    monkeypatch.setenv("SKILL_EVOLUTION_DB_PATH", str(base / "state.db"))
    monkeypatch.setenv("SKILL_EVOLUTION_CLAUDE_CODE_HOME", str(base / "claude"))
