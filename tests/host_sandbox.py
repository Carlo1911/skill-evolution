"""Build a throwaway host -- Hermes or Claude Code -- with real files on disk.

Not a conftest: tests import `build_host` explicitly and parametrize over HOSTS, so the same
assertion runs against both adapters' on-disk layouts and write paths.

- hermes: skills under `<tmp>/hermes/skills/<category>/<name>/SKILL.md`, a `state.db` with
  the minimal live schema, its own state file.
- claude_code: skills under `<tmp>/claude/skills/<name>/SKILL.md` (real directories, not
  symlinks, so the adapter writes them), sessions as `projects/p/<id>.jsonl`.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fetch_sessions  # noqa: E402
import host  # noqa: E402
import skill_index  # noqa: E402

HOSTS = ["hermes", "claude_code"]


class Sandbox:
    def __init__(self, name: str, root: Path, monkeypatch):
        self.name = name
        self.root = root
        self.monkeypatch = monkeypatch
        self.state_file = root / "skill_evolution_state.json"
        self.db_path = root / "state.db"
        root.mkdir(parents=True, exist_ok=True)
        if name == "hermes":
            self.skills_root = root / "skills"
            original_scan = skill_index.scan_skills
            skills_root = str(self.skills_root)
            # scan_skills()'s default root was bound at import, so the zero-arg call the
            # adapter makes is redirected; an explicit root passes through untouched.
            monkeypatch.setattr(
                skill_index, "scan_skills",
                lambda skills_dir=skills_root, extra_roots=None: original_scan(skills_dir, extra_roots),
            )
            monkeypatch.setattr(skill_index, "SKILLS_DIR", skills_root)
            monkeypatch.setenv(fetch_sessions.STATE_FILE_ENV_VAR, str(self.state_file))
            self._init_db()
        else:
            self.skills_root = root / "skills"
            monkeypatch.setenv(host.CLAUDE_CODE_HOME_ENV_VAR, str(root))
            monkeypatch.delenv(fetch_sessions.STATE_FILE_ENV_VAR, raising=False)
        self.skills_root.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(host.HOST_ENV_VAR, name)

    @property
    def adapter(self):
        return host.get_adapter(self.name)

    # ── skills ──────────────────────────────────────────────────────

    def skill_path(self, skill: str) -> Path:
        if self.name == "hermes":
            return self.skills_root / "general" / skill / "SKILL.md"
        return self.skills_root / skill / "SKILL.md"

    def write_skill(self, skill: str, body: str) -> Path:
        path = self.skill_path(skill)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return path

    def read_skill(self, skill: str) -> str:
        return self.skill_path(skill).read_text()

    # ── sessions ────────────────────────────────────────────────────

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS sessions "
                     "(id TEXT PRIMARY KEY, source TEXT NOT NULL, model TEXT, title TEXT, started_at)")
        conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, session_id TEXT, "
                     "role TEXT, content TEXT, timestamp REAL, tool_calls TEXT)")
        conn.commit()
        conn.close()

    def add_sessions(self, sessions: Iterable[Tuple[str, float]]) -> None:
        """Add (session_id, started_at_epoch) sessions with one user message each."""
        if self.name == "hermes":
            conn = sqlite3.connect(self.db_path)
            for sid, ts in sessions:
                conn.execute("INSERT INTO sessions (id, source, model, title, started_at) "
                             "VALUES (?, 'cli', 'm', 't', ?)", (sid, ts))
                conn.execute("INSERT INTO messages (session_id, role, content, timestamp) "
                             "VALUES (?, 'user', 'hola', ?)", (sid, ts))
            conn.commit()
            conn.close()
            return
        project = self.root / "projects" / "p"
        project.mkdir(parents=True, exist_ok=True)
        for sid, ts in sessions:
            stamp = datetime.fromtimestamp(ts, timezone.utc).isoformat()
            record = {"type": "user", "sessionId": sid, "timestamp": stamp,
                      "message": {"role": "user", "content": "hola"}}
            (project / f"{sid}.jsonl").write_text(json.dumps(record) + "\n")

    def mark_processed(self, session_ids) -> None:
        self.adapter.mark_processed(list(session_ids))

    def processed(self):
        return set(self.adapter.iter_processed())


def build_host(name: str, tmp_path: Path, monkeypatch) -> Sandbox:
    return Sandbox(name, tmp_path / name, monkeypatch)
