"""embedding_similarity must be usable wherever evaluate is, not only from its CLI.

It used to register itself only inside evaluate.py's `__main__` block, so
apply_proposal() and skill_quality.py -- which import evaluate as a library -- raised
"Unknown evaluator" when it was enabled. Each check runs in a fresh interpreter so no
other test's import of embedding_similarity can make it pass by accident.
"""

import os
import subprocess
import sys

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")


def _run(code):
    env = {**os.environ, "SKILL_EVOLUTION_EVALUATORS": "deterministic,embedding_similarity"}
    return subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {SCRIPTS!r}); {code}"],
                          capture_output=True, text=True, env=env)


def test_library_import_resolves_embedding_similarity():
    proc = _run("import proposal, evaluate; print([e.name for e in evaluate.get_enabled_evaluators()])")

    assert proc.returncode == 0, proc.stderr
    assert "embedding_similarity" in proc.stdout


def test_resolving_the_evaluator_does_not_load_fastembed():
    proc = _run("import evaluate; evaluate.get_enabled_evaluators(); print('fastembed' in sys.modules)")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False"
