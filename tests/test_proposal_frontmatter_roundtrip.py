"""Tests for proposal.py's frontmatter round-trip of multi-line proposed_changes values.

save_proposal()/load_proposal() round-trip a proposal through a hand-rolled
frontmatter format. Before this fix, a multi-line new_value/old_value
containing its own "---" (e.g. a full skill body, which starts with its own
YAML frontmatter -- exactly what the GEPA optimizer proposes) silently
collapsed to the literal string "---" on reload, because the frontmatter
parser treats any "---" line as a delimiter and reads change fields one
line at a time. See scripts/proposal.py's _encode_frontmatter_value().
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from proposal import ProposalType, ProposedChange, SkillEvolutionProposal, save_proposal, load_proposal


MULTI_LINE_SKILL_BODY = """---
name: debugging-and-error-recovery
description: Guides systematic root-cause debugging.
---

# Debugging

Step 1. Do X
Step 2. Do Y
"""


def test_multiline_new_value_with_embedded_frontmatter_survives_roundtrip(tmp_path):
    proposal = SkillEvolutionProposal(
        type=ProposalType.IMPROVE_EXISTING,
        target_skill="debugging-and-error-recovery",
        confidence=0.5,
        summary="GEPA-optimized improvement",
        rationale="test rationale",
        proposed_changes=[ProposedChange(field="body", new_value=MULTI_LINE_SKILL_BODY)],
    )

    path = save_proposal(proposal, directory=str(tmp_path))
    loaded = load_proposal(path)

    assert loaded.proposed_changes[0].new_value == MULTI_LINE_SKILL_BODY


def test_multiline_old_value_also_survives_roundtrip(tmp_path):
    proposal = SkillEvolutionProposal(
        type=ProposalType.IMPROVE_EXISTING,
        target_skill="some-skill",
        confidence=0.5,
        summary="test",
        rationale="test rationale",
        proposed_changes=[ProposedChange(
            field="body",
            old_value=MULTI_LINE_SKILL_BODY,
            new_value="short single-line replacement",
        )],
    )

    path = save_proposal(proposal, directory=str(tmp_path))
    loaded = load_proposal(path)

    assert loaded.proposed_changes[0].old_value == MULTI_LINE_SKILL_BODY
    assert loaded.proposed_changes[0].new_value == "short single-line replacement"


def test_short_single_line_value_still_renders_unencoded(tmp_path):
    """Backward compatibility: the common case is untouched by the encoding path."""
    proposal = SkillEvolutionProposal(
        type=ProposalType.IMPROVE_EXISTING,
        target_skill="some-skill",
        confidence=0.85,
        summary="Improve description",
        rationale="short rationale",
        proposed_changes=[ProposedChange(field="description", old_value="Old desc.", new_value="New desc.")],
    )

    rendered = proposal.render()

    assert 'new_value: "New desc."' in rendered
    assert "b64:" not in rendered


def test_secret_in_multiline_new_value_is_redacted_before_encoding(tmp_path):
    body_with_secret = MULTI_LINE_SKILL_BODY + "\nUse key sk-ant-api03-should-be-redacted\n"
    proposal = SkillEvolutionProposal(
        type=ProposalType.IMPROVE_EXISTING,
        target_skill="debugging-and-error-recovery",
        confidence=0.5,
        summary="test",
        rationale="test rationale",
        proposed_changes=[ProposedChange(field="body", new_value=body_with_secret)],
    )

    path = save_proposal(proposal, directory=str(tmp_path))
    with open(path) as f:
        raw_file_content = f.read()
    loaded = load_proposal(path)

    assert "sk-ant-api03-should-be-redacted" not in raw_file_content
    assert "sk-ant-api03-should-be-redacted" not in loaded.proposed_changes[0].new_value
    assert "[REDACTED]" in loaded.proposed_changes[0].new_value
