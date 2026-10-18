"""host._atomic_write_text() must not change file permissions as a side effect.

mkstemp() creates its temp file 0600, and os.replace() keeps the temp file's mode, so a
rewrite silently turned a 0644 eval_history.jsonl into 0600 and every new SKILL.md was
born 0600. A replaced file keeps its mode; a new file gets the mode a plain open() would
give it under the current umask.
"""

import os
import stat
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import host


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o600])
def test_rewrite_keeps_the_existing_mode(tmp_path, mode):
    target = tmp_path / "file.txt"
    target.write_text("old")
    os.chmod(target, mode)

    host._atomic_write_text(target, "new")

    assert target.read_text() == "new"
    assert _mode(target) == mode


def test_new_file_gets_the_umask_default(tmp_path):
    reference = tmp_path / "plain.txt"
    reference.write_text("x")  # what an ordinary open() creates under this umask

    target = tmp_path / "skills" / "new" / "SKILL.md"
    host._atomic_write_text(target, "body")

    assert _mode(target) == _mode(reference)


def test_history_backfill_keeps_the_history_mode(tmp_path):
    history = tmp_path / "eval_history.jsonl"
    history.write_text('{"target": "skill:a", "evaluator_name": "gate", "passed": false, "score": 0.5, '
                       '"feedback": "llm_judge failed closed: Claude provider call failed: HTTP Error 401"}\n')
    os.chmod(history, 0o644)

    report = evaluate.backfill_transport_failures(str(history), apply=True)

    assert report["matched"] == 1
    assert _mode(history) == 0o644
