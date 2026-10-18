"""Tests for skill quality tracking."""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import skill_quality
from proposal import ProposedChange, SkillEvolutionProposal


def datetime_fromisoformat(s):
    """Proxy to the real datetime.fromisoformat, used when skill_quality.datetime is mocked."""
    return datetime.fromisoformat(s)


class TestCreateSyntheticProposal:
    """Test synthetic proposal creation for quality evaluation."""

    def test_creates_proposal_with_correct_structure(self):
        """Synthetic proposal should have field=body with same old/new values."""
        skill_name = "test-skill"
        skill_body = "# Test Skill\n\nThis is a test skill body."
        
        proposal = skill_quality.create_synthetic_proposal(skill_name, skill_body)
        
        assert proposal.target_skill == skill_name
        assert len(proposal.proposed_changes) == 1
        change = proposal.proposed_changes[0]
        assert change.field == "body"
        assert change.old_value == skill_body
        assert change.new_value == skill_body
        assert proposal.confidence == 1.0
        assert "quality_check" in proposal.proposal_id

    def test_proposal_type_is_improve_existing(self):
        """Synthetic proposal should be IMPROVE_EXISTING type."""
        from proposal import ProposalType
        
        proposal = skill_quality.create_synthetic_proposal("test", "body")
        assert proposal.type == ProposalType.IMPROVE_EXISTING


class TestGetPreviousScore:
    """Test reading previous scores from eval_history.jsonl."""

    def test_returns_none_when_no_history(self, tmp_path):
        """Should return None when skill has no evaluation history."""
        history_path = str(tmp_path / "eval_history.jsonl")
        
        with patch("evaluate.read_history") as mock_read:
            mock_read.return_value = []
            result = skill_quality.get_previous_score("nonexistent-skill")
        
        assert result is None

    def test_returns_most_recent_passing_score(self, tmp_path):
        """Should return the most recent passing score."""
        history = [
            {"target": "skill:test", "score": 0.5, "passed": True, "timestamp": "2026-07-01T00:00:00Z"},
            {"target": "skill:test", "score": 0.7, "passed": True, "timestamp": "2026-07-15T00:00:00Z"},
            {"target": "skill:test", "score": 0.3, "passed": False, "timestamp": "2026-07-20T00:00:00Z"},
        ]
        
        with patch("evaluate.read_history") as mock_read:
            mock_read.return_value = history
            result = skill_quality.get_previous_score("test")
        
        assert result == 0.7  # Most recent passing score

    def test_skips_failed_entries(self):
        """Should skip failed entries and find the most recent passing one."""
        history = [
            {"target": "skill:test", "score": 0.8, "passed": True},
            {"target": "skill:test", "score": 0.4, "passed": False},
            {"target": "skill:test", "score": 0.3, "passed": False},
        ]
        
        with patch("evaluate.read_history") as mock_read:
            mock_read.return_value = history
            result = skill_quality.get_previous_score("test")
        
        assert result == 0.8


class TestEvaluateSkillQuality:
    """Test individual skill evaluation."""

    def test_returns_none_when_skill_not_found(self):
        """Should return None when skill cannot be resolved."""
        with patch("evaluate.installed_skill_body") as mock_body:
            mock_body.return_value = None
            result = skill_quality.evaluate_skill_quality("nonexistent")
        
        assert result is None

    def test_evaluates_skill_and_returns_result(self):
        """Should evaluate skill and return SkillQualityResult."""
        skill_body = "# Test Skill\n\nBody content."
        
        with patch("evaluate.installed_skill_body") as mock_body, \
             patch("skill_quality.get_previous_score") as mock_prev, \
             patch("evaluate.evaluate_skill_text") as mock_eval, \
             patch("evaluate.append_history") as mock_append:
            
            mock_body.return_value = skill_body
            mock_prev.return_value = 0.6
            
            # Mock evaluation results
            from evaluate import EvalResult
            mock_eval.return_value = [
                EvalResult(score=0.8, feedback="Good skill", passed=True, evaluator_name="llm_judge"),
                EvalResult(score=1.0, feedback="", passed=True, evaluator_name="deterministic"),
            ]
            
            result = skill_quality.evaluate_skill_quality("test-skill")
        
        assert result is not None
        assert result.skill_name == "test-skill"
        assert result.current_score == 0.9  # Mean of 0.8 and 1.0
        assert result.previous_score == 0.6
        assert result.delta == pytest.approx(0.3)
        assert result.feedback == "Good skill"
        assert result.passed is True
        assert result.evaluated_at  # Should have timestamp

    def test_calculates_delta_correctly(self):
        """Should calculate delta as current - previous."""
        with patch("evaluate.installed_skill_body") as mock_body, \
             patch("skill_quality.get_previous_score") as mock_prev, \
             patch("evaluate.evaluate_skill_text") as mock_eval, \
             patch("evaluate.append_history"):
            
            mock_body.return_value = "body"
            mock_prev.return_value = 0.5
            
            from evaluate import EvalResult
            mock_eval.return_value = [
                EvalResult(score=0.7, feedback="", passed=True, evaluator_name="llm_judge"),
            ]
            
            result = skill_quality.evaluate_skill_quality("test")
        
        assert result.delta == pytest.approx(0.2)

    def test_delta_is_none_when_no_previous_score(self):
        """Should have delta=None when no previous evaluation exists."""
        with patch("evaluate.installed_skill_body") as mock_body, \
             patch("skill_quality.get_previous_score") as mock_prev, \
             patch("evaluate.evaluate_skill_text") as mock_eval, \
             patch("evaluate.append_history"):
            
            mock_body.return_value = "body"
            mock_prev.return_value = None
            
            from evaluate import EvalResult
            mock_eval.return_value = [
                EvalResult(score=0.7, feedback="", passed=True, evaluator_name="llm_judge"),
            ]
            
            result = skill_quality.evaluate_skill_quality("test")
        
        assert result.delta is None


class TestEvaluateAllSkills:
    """Test bulk skill evaluation."""

    def test_evaluates_all_skills_when_no_filter(self):
        """Should evaluate all installed skills when no filter is provided."""
        mock_skills = [
            {"name": "skill-1"},
            {"name": "skill-2"},
            {"name": "skill-3"},
        ]
        
        with patch("host.get_adapter") as mock_adapter, \
             patch("skill_quality.evaluate_skill_quality") as mock_eval:
            
            mock_adapter.return_value.iter_skills.return_value = mock_skills
            mock_eval.side_effect = [
                skill_quality.SkillQualityResult(
                    skill_name="skill-1",
                    current_score=0.7,
                    previous_score=None,
                    delta=None,
                    feedback="",
                    evaluated_at="2026-08-01T00:00:00Z",
                    passed=True,
                ),
                skill_quality.SkillQualityResult(
                    skill_name="skill-2",
                    current_score=0.5,
                    previous_score=None,
                    delta=None,
                    feedback="",
                    evaluated_at="2026-08-01T00:00:00Z",
                    passed=True,
                ),
                skill_quality.SkillQualityResult(
                    skill_name="skill-3",
                    current_score=0.9,
                    previous_score=None,
                    delta=None,
                    feedback="",
                    evaluated_at="2026-08-01T00:00:00Z",
                    passed=True,
                ),
            ]
            
            results = skill_quality.evaluate_all_skills()
        
        assert len(results) == 3
        # Should be sorted by score (ascending)
        assert results[0].skill_name == "skill-2"
        assert results[1].skill_name == "skill-1"
        assert results[2].skill_name == "skill-3"

    def test_filters_to_specific_skills(self):
        """Should only evaluate specified skills when skill_names is provided."""
        mock_skills = [
            {"name": "skill-1"},
            {"name": "skill-2"},
            {"name": "skill-3"},
        ]
        
        with patch("host.get_adapter") as mock_adapter, \
             patch("skill_quality.evaluate_skill_quality") as mock_eval:
            
            mock_adapter.return_value.iter_skills.return_value = mock_skills
            mock_eval.return_value = skill_quality.SkillQualityResult(
                skill_name="skill-1",
                current_score=0.7,
                previous_score=None,
                delta=None,
                feedback="",
                evaluated_at="2026-08-01T00:00:00Z",
                passed=True,
            )
            
            results = skill_quality.evaluate_all_skills(skill_names=["skill-1"])
        
        assert len(results) == 1
        assert results[0].skill_name == "skill-1"

    def test_skips_recently_evaluated_skills_when_since_given(self):
        """Should skip skills evaluated within the last N days (cost control),
        unless the skill was explicitly requested via skill_names."""
        mock_skills = [
            {"name": "skill-recent"},   # evaluated yesterday -> skip
            {"name": "skill-stale"},    # never evaluated -> keep
            {"name": "skill-old"},      # evaluated 60 days ago -> keep
        ]
        
        with patch("host.get_adapter") as mock_adapter, \
             patch("skill_quality.evaluate_skill_quality") as mock_eval, \
             patch("skill_quality.get_last_evaluation_timestamp") as mock_last_ts:
            
            mock_adapter.return_value.iter_skills.return_value = mock_skills
            mock_last_ts.side_effect = lambda name: {
                "skill-recent": 1754064000,   # 2026-08-01 UTC (recent)
                "skill-stale": None,
                "skill-old": 1751385600,      # 2026-07-01 UTC (31 days ago, just past cutoff)
            }[name]
            
            mock_eval.return_value = skill_quality.SkillQualityResult(
                skill_name="x",
                current_score=0.7,
                previous_score=None,
                delta=None,
                feedback="",
                evaluated_at="2026-08-01T00:00:00Z",
                passed=True,
            )
            
            # since_days=30 with today=2026-08-01 -> cutoff ~2026-07-02
            with patch("skill_quality.datetime") as mock_dt:
                mock_dt.now.return_value.timestamp.return_value = 1754064000
                mock_dt.fromisoformat.side_effect = datetime_fromisoformat
                results = skill_quality.evaluate_all_skills(since_days=30)
        
        evaluated_names = [c.args[0] for c in mock_eval.call_args_list]
        assert evaluated_names == ["skill-stale", "skill-old"]

    def test_since_never_skips_explicitly_requested_skills(self):
        """skill_names bypasses the --since skip: an explicit request always evaluates."""
        mock_skills = [
            {"name": "skill-recent"},
        ]
        
        with patch("host.get_adapter") as mock_adapter, \
             patch("skill_quality.evaluate_skill_quality") as mock_eval, \
             patch("skill_quality.get_last_evaluation_timestamp") as mock_last_ts:
            
            mock_adapter.return_value.iter_skills.return_value = mock_skills
            mock_last_ts.return_value = 1754064000  # recent
            
            mock_eval.return_value = skill_quality.SkillQualityResult(
                skill_name="skill-recent",
                current_score=0.7,
                previous_score=None,
                delta=None,
                feedback="",
                evaluated_at="2026-08-01T00:00:00Z",
                passed=True,
            )
            
            with patch("skill_quality.datetime") as mock_dt:
                mock_dt.now.return_value.timestamp.return_value = 1754064000
                mock_dt.fromisoformat.side_effect = datetime_fromisoformat
                results = skill_quality.evaluate_all_skills(
                    skill_names=["skill-recent"], since_days=30
                )
        
        assert mock_eval.call_count == 1
        assert len(results) == 1

    def test_filters_by_below_threshold(self):
        """Should only include skills below threshold when specified."""
        with patch("host.get_adapter") as mock_adapter, \
             patch("skill_quality.evaluate_skill_quality") as mock_eval:
            
            mock_adapter.return_value.iter_skills.return_value = [
                {"name": "skill-1"},
                {"name": "skill-2"},
            ]
            
            mock_eval.side_effect = [
                skill_quality.SkillQualityResult(
                    skill_name="skill-1",
                    current_score=0.5,
                    previous_score=None,
                    delta=None,
                    feedback="",
                    evaluated_at="2026-08-01T00:00:00Z",
                    passed=True,
                ),
                skill_quality.SkillQualityResult(
                    skill_name="skill-2",
                    current_score=0.8,
                    previous_score=None,
                    delta=None,
                    feedback="",
                    evaluated_at="2026-08-01T00:00:00Z",
                    passed=True,
                ),
            ]
            
            results = skill_quality.evaluate_all_skills(below_threshold=0.7)
        
        assert len(results) == 1
        assert results[0].skill_name == "skill-1"


class TestGenerateQualityReport:
    """Test report generation."""

    def test_generates_markdown_report(self):
        """Should generate a valid markdown report."""
        results = [
            skill_quality.SkillQualityResult(
                skill_name="skill-1",
                current_score=0.7,
                previous_score=0.6,
                delta=0.1,
                feedback="Good improvement",
                evaluated_at="2026-08-01T00:00:00Z",
                passed=True,
            ),
            skill_quality.SkillQualityResult(
                skill_name="skill-2",
                current_score=0.5,
                previous_score=0.6,
                delta=-0.1,
                feedback="Needs work",
                evaluated_at="2026-08-01T00:00:00Z",
                passed=False,
            ),
        ]
        
        report = skill_quality.generate_quality_report(results, output_format="markdown")
        
        assert "# Skill Quality Report" in report
        assert "Total skills evaluated: 2" in report
        assert "skill-1" in report
        assert "skill-2" in report
        assert "0.70" in report
        assert "0.50" in report
        assert "Improvements" in report
        assert "Regressions" in report

    def test_generates_json_report(self):
        """Should generate valid JSON when format is json."""
        results = [
            skill_quality.SkillQualityResult(
                skill_name="test",
                current_score=0.7,
                previous_score=None,
                delta=None,
                feedback="",
                evaluated_at="2026-08-01T00:00:00Z",
                passed=True,
            ),
        ]
        
        report = skill_quality.generate_quality_report(results, output_format="json")
        parsed = json.loads(report)
        
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["skill_name"] == "test"
        assert parsed[0]["current_score"] == 0.7

    def test_handles_empty_results(self):
        """Should handle empty results gracefully."""
        report = skill_quality.generate_quality_report([], output_format="markdown")
        
        assert "No skills evaluated" in report

    def test_calculates_statistics(self):
        """Should calculate average score correctly."""
        results = [
            skill_quality.SkillQualityResult(
                skill_name="skill-1",
                current_score=0.6,
                previous_score=None,
                delta=None,
                feedback="",
                evaluated_at="2026-08-01T00:00:00Z",
                passed=True,
            ),
            skill_quality.SkillQualityResult(
                skill_name="skill-2",
                current_score=0.8,
                previous_score=None,
                delta=None,
                feedback="",
                evaluated_at="2026-08-01T00:00:00Z",
                passed=True,
            ),
        ]
        
        report = skill_quality.generate_quality_report(results, output_format="markdown")
        
        assert "Average score: 0.70" in report


class TestCLI:
    """Test CLI argument parsing and execution."""

    def test_parses_skill_argument(self):
        """Should parse --skill argument correctly."""
        with patch("sys.argv", ["skill_quality.py", "--skill", "test-skill"]), \
             patch("skill_quality.evaluate_all_skills") as mock_eval, \
             patch("skill_quality.generate_quality_report") as mock_report:
            
            mock_eval.return_value = []
            mock_report.return_value = ""
            
            skill_quality.main()
            
            mock_eval.assert_called_once()
            call_kwargs = mock_eval.call_args[1]
            assert call_kwargs["skill_names"] == ["test-skill"]

    def test_parses_output_argument(self, tmp_path):
        """Should parse --output argument correctly."""
        output_file = str(tmp_path / "report.md")
        
        with patch("sys.argv", ["skill_quality.py", "--output", output_file]), \
             patch("skill_quality.evaluate_all_skills") as mock_eval, \
             patch("skill_quality.generate_quality_report") as mock_report:
            
            mock_eval.return_value = []
            mock_report.return_value = "# Report"
            
            skill_quality.main()
            
            assert os.path.exists(output_file)
            with open(output_file) as f:
                assert f.read() == "# Report"

    def test_parses_since_argument(self):
        """Should parse --since argument correctly."""
        with patch("sys.argv", ["skill_quality.py", "--since", "30d"]), \
             patch("skill_quality.evaluate_all_skills") as mock_eval, \
             patch("skill_quality.generate_quality_report") as mock_report:
            
            mock_eval.return_value = []
            mock_report.return_value = ""
            
            skill_quality.main()
            
            call_kwargs = mock_eval.call_args[1]
            assert call_kwargs["since_days"] == 30

    def test_parses_below_argument(self):
        """Should parse --below argument correctly."""
        with patch("sys.argv", ["skill_quality.py", "--below", "0.7"]), \
             patch("skill_quality.evaluate_all_skills") as mock_eval, \
             patch("skill_quality.generate_quality_report") as mock_report:
            
            mock_eval.return_value = []
            mock_report.return_value = ""
            
            skill_quality.main()
            
            call_kwargs = mock_eval.call_args[1]
            assert call_kwargs["below_threshold"] == 0.7

    def test_parses_format_argument(self):
        """Should parse --format argument correctly."""
        with patch("sys.argv", ["skill_quality.py", "--format", "json"]), \
             patch("skill_quality.evaluate_all_skills") as mock_eval, \
             patch("skill_quality.generate_quality_report") as mock_report:
            
            mock_eval.return_value = []
            mock_report.return_value = "[]"
            
            skill_quality.main()
            
            mock_report.assert_called_once_with([], output_format="json")


class TestTransportFailurePropagation:
    """A provider outage during a quality pass must stay distinguishable from a bad skill.

    The tracker builds its own combined gate entry instead of going through
    evaluate_and_record(), so it has to carry `transport_failure` forward itself --
    otherwise find_low_scoring_targets() reads the outage's 0.0 as a quality signal and
    queues the skill for optimization.
    """

    def _run_with_judge(self, tmp_path, monkeypatch, judge_result):
        from evaluate import EvalResult
        history_path = tmp_path / "eval_history.jsonl"
        monkeypatch.setenv("SKILL_EVOLUTION_HISTORY_PATH", str(history_path))
        with patch("evaluate.installed_skill_body", return_value="# Skill\n\nBody."), \
             patch("evaluate.evaluate_skill_text") as mock_eval:
            mock_eval.return_value = [
                judge_result,
                EvalResult(score=1.0, feedback="", passed=True, evaluator_name="deterministic"),
            ]
            skill_quality.evaluate_skill_quality("flaky-skill")
        return history_path

    def test_provider_failure_is_recorded_and_not_a_candidate(self, tmp_path, monkeypatch):
        import optimize_skill
        from evaluate import EvalResult
        history_path = self._run_with_judge(tmp_path, monkeypatch, EvalResult(
            score=0.0, feedback="llm_judge failed closed: HTTP 429", passed=False,
            evaluator_name="llm_judge", transport_failure=True,
        ))

        entries = [json.loads(line) for line in history_path.read_text().splitlines()]
        assert len(entries) == 1
        assert entries[0]["transport_failure"] is True
        assert optimize_skill.find_low_scoring_targets(history_path=str(history_path)) == []

    def test_genuine_low_score_still_becomes_a_candidate(self, tmp_path, monkeypatch):
        import optimize_skill
        from evaluate import EvalResult
        history_path = self._run_with_judge(tmp_path, monkeypatch, EvalResult(
            score=0.0, feedback="procedure is wrong", passed=False, evaluator_name="llm_judge",
        ))

        entries = [json.loads(line) for line in history_path.read_text().splitlines()]
        assert "transport_failure" not in entries[0]
        candidates = optimize_skill.find_low_scoring_targets(history_path=str(history_path))
        assert [c["target"] for c in candidates] == ["skill:flaky-skill"]
