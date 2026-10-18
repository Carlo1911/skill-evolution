"""Literal blocks (`old_value: |-`) in a hand-written proposal's changes.

The nightly analyzer writes proposal files by hand. The single-line form cannot hold a
newline and b64 is only what save_proposal() writes, so a multi-line anchor or section had
no expressible form: 11 of 48 real proposal files shipped with no new_value at all.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import proposal as proposal_module
from host_sandbox import HOSTS, build_host
from proposal import _parse_frontmatter, load_proposal, save_proposal


def _write(tmp_path, frontmatter: str, name="p1.md"):
    path = tmp_path / name
    path.write_text("---\n" + frontmatter + "---\n\nBody.\n")
    return str(path)


BLOCK_PROPOSAL = """proposal_id: p1
type: improve_existing
target_skill: demo
confidence: 0.9
status: proposed
proposed_changes:
  - field: body
    old_value: |-
      ## Steps

      Step one.
    new_value: |-
      ## Steps

      Step one.

      Step one and a half.
    description: add a step
session_ids:
  - s1
"""


def test_literal_blocks_keep_every_line_and_inner_blank_lines(tmp_path):
    p = load_proposal(_write(tmp_path, BLOCK_PROPOSAL))

    change = p.proposed_changes[0]
    assert change.old_value == "## Steps\n\nStep one."
    assert change.new_value == "## Steps\n\nStep one.\n\nStep one and a half."
    assert change.description == "add a step"
    assert p.session_ids == ["s1"]


def test_pipe_keeps_one_trailing_newline_and_dash_strips_it():
    data = _parse_frontmatter(
        "proposed_changes:\n"
        "  - field: body\n"
        "    old_value: |\n"
        "      line\n"
        "\n"
        "    new_value: |-\n"
        "      line\n"
    )
    change = data["proposed_changes"][0]
    assert change["old_value"] == "line\n"
    assert change["new_value"] == "line"


def test_block_followed_by_a_second_change(tmp_path):
    text = BLOCK_PROPOSAL.replace(
        "session_ids:",
        "  - field: description\n    new_value: \"new description\"\nsession_ids:")
    p = load_proposal(_write(tmp_path, text))

    assert [c.field for c in p.proposed_changes] == ["body", "description"]
    assert p.proposed_changes[1].new_value == "new description"


def test_create_new_body_block_starting_with_frontmatter(tmp_path):
    text = """proposal_id: c1
type: create_new
confidence: 0.9
status: proposed
proposed_changes:
  - field: name
    new_value: "fresh-skill"
  - field: body
    new_value: |-
      ---
      name: fresh-skill
      description: Does a thing.
      ---

      # Fresh skill
"""
    p = load_proposal(_write(tmp_path, text, "c1.md"))

    body = [c for c in p.proposed_changes if c.field == "body"][0].new_value
    assert body.startswith("---\nname: fresh-skill\n")
    assert body.endswith("# Fresh skill")
    assert evaluate._structural_problem(p) is None


def test_block_content_is_never_b64_decoded():
    data = _parse_frontmatter(
        "proposed_changes:\n  - field: body\n    new_value: |-\n      b64:aGVsbG8=\n")
    assert data["proposed_changes"][0]["new_value"] == "b64:aGVsbG8="


def test_under_indented_block_is_empty_not_an_error(tmp_path):
    text = BLOCK_PROPOSAL.replace("      ## Steps\n\n      Step one.\n    new_value",
                                  "    ## Steps\n    new_value")
    p = load_proposal(_write(tmp_path, text))
    assert p.proposed_changes[0].old_value == ""


def test_round_trip_through_save_proposal(tmp_path):
    first = load_proposal(_write(tmp_path, BLOCK_PROPOSAL))
    out = tmp_path / "out"
    first.source_path = None
    path = save_proposal(first, directory=str(out))
    again = load_proposal(path)

    assert again.proposed_changes == first.proposed_changes
    assert "b64:" in open(path).read()


INSTALLED = ("---\nname: demo\ndescription: d\n---\n\n## Steps\n\nStep one.\n\nStep two.\n\n"
             + "Context line kept as is.\n" * 20)


@pytest.mark.parametrize("host_name", HOSTS)
def test_a_multi_line_anchor_applies_end_to_end(host_name, tmp_path, monkeypatch):
    box = build_host(host_name, tmp_path, monkeypatch)
    box.write_skill("demo", INSTALLED)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_TARGETS", "skill")
    p = load_proposal(_write(tmp_path, BLOCK_PROPOSAL))

    result = proposal_module.apply_proposal(p, min_confidence=0.5,
                                            directory=str(tmp_path / "proposals"))

    assert result["can_apply"] is True, result
    expected = INSTALLED.replace("Step one.\n", "Step one.\n\nStep one and a half.\n", 1)
    if host_name == "hermes":
        patch = result["instructions"][0]
        assert patch["old_value"] == "## Steps\n\nStep one."
        assert patch["new_value"].endswith("Step one and a half.")
    else:
        assert box.read_skill("demo") == expected


def test_the_skill_md_example_is_a_complete_proposal(tmp_path):
    """The nightly job loads SKILL.md as its instructions, and its proposals copied the
    example's shape -- which used to carry no old_value/new_value. Keep the example a
    proposal the gate can evaluate."""
    skill_md = open(os.path.join(os.path.dirname(__file__), "..", "SKILL.md")).read()
    after = skill_md.split("Each proposal is a structured markdown file", 1)[1]
    example = after.split("```yaml\n", 1)[1].split("```", 1)[0]
    path = tmp_path / "example.md"
    path.write_text(example + "\nBody.\n")

    p = load_proposal(str(path))

    change = p.proposed_changes[0]
    assert change.old_value and change.new_value
    assert change.new_value.startswith(change.old_value)
    assert evaluate._structural_problem(p) is None
