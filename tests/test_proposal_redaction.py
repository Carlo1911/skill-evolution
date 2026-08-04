"""Tests for save_proposal()'s redaction-on-write (U10, R19)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from proposal import ProposalType, ProposedChange, SkillEvolutionProposal, save_proposal, load_proposal


def test_save_proposal_redacts_secret_in_rendered_content(tmp_path):
    proposal = SkillEvolutionProposal(
        proposal_id="secret-test-001",
        type=ProposalType.IMPROVE_EXISTING,
        target_skill="test-skill",
        confidence=0.9,
        summary="Contains a leaked key sk-ant-api-should-not-persist",
        rationale="Session showed sk-ant-api-should-not-persist in output.",
        proposed_changes=[ProposedChange(field="body", new_value="Body text.")],
    )

    path = save_proposal(proposal, directory=str(tmp_path))

    with open(path) as f:
        written = f.read()

    assert "sk-ant-api-should-not-persist" not in written
    assert "[REDACTED]" in written


def test_save_proposal_without_secrets_is_unaffected(tmp_path):
    proposal = SkillEvolutionProposal(
        proposal_id="clean-001",
        type=ProposalType.IMPROVE_EXISTING,
        target_skill="test-skill",
        confidence=0.9,
        summary="A perfectly clean summary",
        rationale="Nothing sensitive here.",
        proposed_changes=[ProposedChange(field="body", new_value="Body text.")],
    )

    path = save_proposal(proposal, directory=str(tmp_path))
    reloaded = load_proposal(path)

    assert reloaded.summary == "A perfectly clean summary"
