"""Exercises evaluate.installed_skill_body()'s REAL delegation chain end to end:
evaluate.py -> host.get_adapter() -> HermesAdapter -> HostAdapter.read_skill_body()
-> self.iter_skills() -> skill_index.scan_skills().

Every other test that touches installed_skill_body() monkeypatches the function itself
wholesale (see tests/test_evaluate_malformed_changes.py), which proves nothing about
whether the real chain still works after U2 rewired it through the host adapter. This
file monkeypatches only skill_index.scan_skills() (the one seam every layer in the
chain is documented to route through as an attribute call), so the real code in
between -- evaluate.installed_skill_body(), host.get_adapter(), HostAdapter's concrete
read_skill_body(), HermesAdapter.iter_skills() -- all actually execute.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import host
import skill_index


def _write_skill(root, category, name, description="does a thing", body_extra=""):
    skill_dir = root / category / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nBody text.{body_extra}\n"
    )


def test_installed_skill_body_real_chain_returns_body_text(tmp_path, monkeypatch):
    _write_skill(tmp_path, "general-skills", "deploy-helper")
    original_scan_skills = skill_index.scan_skills
    monkeypatch.setattr(skill_index, "scan_skills", lambda: original_scan_skills(str(tmp_path)))

    body = evaluate.installed_skill_body("deploy-helper")

    assert body is not None
    assert "deploy-helper" in body
    assert "Body text." in body


def test_installed_skill_body_real_chain_returns_none_for_no_match(tmp_path, monkeypatch):
    original_scan_skills = skill_index.scan_skills
    monkeypatch.setattr(skill_index, "scan_skills", lambda: original_scan_skills(str(tmp_path)))

    assert evaluate.installed_skill_body("does-not-exist") is None


def test_installed_skill_body_real_chain_returns_none_for_ambiguous_match(tmp_path, monkeypatch):
    _write_skill(tmp_path, "general-skills", "dup-skill")
    _write_skill(tmp_path, "devops", "dup-skill")
    original_scan_skills = skill_index.scan_skills
    monkeypatch.setattr(skill_index, "scan_skills", lambda: original_scan_skills(str(tmp_path)))

    assert evaluate.installed_skill_body("dup-skill") is None


def test_installed_skill_body_real_chain_uses_the_hermes_adapter_by_default(tmp_path, monkeypatch):
    """Confirms the real chain resolves to HermesAdapter (not some other registered
    adapter) when SKILL_EVOLUTION_HOST is unset, matching this repo's default."""
    monkeypatch.delenv(host.HOST_ENV_VAR, raising=False)
    _write_skill(tmp_path, "general-skills", "deploy-helper")
    original_scan_skills = skill_index.scan_skills
    monkeypatch.setattr(skill_index, "scan_skills", lambda: original_scan_skills(str(tmp_path)))

    assert isinstance(host.get_adapter(), host.HermesAdapter)
    assert evaluate.installed_skill_body("deploy-helper") is not None
