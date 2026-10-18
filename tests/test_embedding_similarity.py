"""Tests for embedding similarity evaluator."""

import pytest
from unittest.mock import Mock, patch
import sys
import os

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import evaluate
from embedding_similarity import EmbeddingSimilarityEvaluator, cosine_similarity


class TestCosineSimilarity:
    """Tests for cosine_similarity function."""
    
    def test_identical_vectors(self):
        """Test cosine similarity of identical vectors is 1.0."""
        vec = [1.0, 2.0, 3.0]
        assert cosine_similarity(vec, vec) == pytest.approx(1.0)
    
    def test_orthogonal_vectors(self):
        """Test cosine similarity of orthogonal vectors is 0.0."""
        vec1 = [1.0, 0.0]
        vec2 = [0.0, 1.0]
        assert cosine_similarity(vec1, vec2) == pytest.approx(0.0)
    
    def test_opposite_vectors(self):
        """Test cosine similarity of opposite vectors is -1.0."""
        vec1 = [1.0, 2.0, 3.0]
        vec2 = [-1.0, -2.0, -3.0]
        assert cosine_similarity(vec1, vec2) == pytest.approx(-1.0)
    
    def test_zero_vector(self):
        """Test cosine similarity with zero vector is 0.0."""
        vec1 = [1.0, 2.0, 3.0]
        vec2 = [0.0, 0.0, 0.0]
        assert cosine_similarity(vec1, vec2) == 0.0
    
    def test_similar_vectors(self):
        """Test cosine similarity of similar vectors."""
        vec1 = [1.0, 2.0, 3.0]
        vec2 = [1.1, 2.1, 3.1]
        similarity = cosine_similarity(vec1, vec2)
        assert similarity > 0.99  # Very similar


class TestEmbeddingSimilarityEvaluator:
    """Tests for EmbeddingSimilarityEvaluator."""
    
    @pytest.fixture
    def mock_backend(self):
        """Create a mock embedding backend."""
        backend = Mock()
        backend.embed = Mock()
        return backend
    
    @pytest.fixture
    def evaluator(self, mock_backend):
        """Create an evaluator with mock backend.

        The backend is created lazily on first use, so the patch must stay active for
        the whole test, not just construction.
        """
        with patch('embedding_similarity.get_embedding_backend', return_value=mock_backend):
            yield EmbeddingSimilarityEvaluator()
    
    def test_evaluator_registration(self):
        """Test that EmbeddingSimilarityEvaluator is registered."""
        # Import to trigger registration
        import embedding_similarity
        
        # Check if registered
        assert "embedding_similarity" in evaluate.REGISTRY
    
    def test_evaluator_not_in_defaults(self):
        """Test that embedding_similarity is not in default evaluators."""
        assert "embedding_similarity" not in evaluate.DEFAULT_EVALUATORS
    
    def test_no_context_is_not_applicable(self, evaluator, mock_backend):
        """No comparison context means nothing was compared: abstain, never a silent pass."""
        result = evaluator.evaluate("test content", context=None)

        assert result.not_applicable is True
        assert "not applicable" in result.feedback
        mock_backend.embed.assert_not_called()

    def test_construction_does_not_build_the_backend(self):
        """get_enabled_evaluators() instantiates per call; that must not load a model."""
        with patch('embedding_similarity.get_embedding_backend') as factory:
            EmbeddingSimilarityEvaluator()
        factory.assert_not_called()
    
    def test_duplicate_detection_pass(self, evaluator, mock_backend):
        """Test duplicate detection passes when similarity is below threshold."""
        # Mock embeddings: content is different from existing skills
        # Using vectors that will have low cosine similarity (< 0.85)
        mock_backend.embed.side_effect = [
            [[1.0, 0.0, 0.0]],  # content embedding
            [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]  # existing skills embeddings (orthogonal)
        ]
        
        result = evaluator.evaluate(
            "new content",
            context={"existing_skills": [("skill1", "body 1"), ("skill2", "body 2")]}
        )
        
        assert result.passed is True
        assert result.score == 1.0  # a passing duplicate check never drags the mean down
    
    def test_duplicate_detection_fail(self, evaluator, mock_backend):
        """Test duplicate detection fails when similarity is above threshold."""
        # Mock embeddings: content is very similar to existing skill
        # Using vectors that will have high cosine similarity (> 0.85)
        mock_backend.embed.side_effect = [
            [[1.0, 0.1, 0.0]],  # content embedding
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]  # existing skills embeddings (first is very similar)
        ]
        
        result = evaluator.evaluate(
            "duplicate content",
            context={"existing_skills": [("similar-skill", "body"), ("different-skill", "body")]}
        )
        
        assert result.passed is False
        assert result.score < 0.15  # 1 - similarity: higher is better, like every evaluator
        assert "too similar" in result.feedback.lower()
        assert "similar-skill" in result.feedback
    
    def test_drift_detection_pass(self, evaluator, mock_backend):
        """Test drift detection passes when similarity is above threshold."""
        # Mock embeddings: content is similar to baseline
        # Using vectors that will have high cosine similarity (> 0.70)
        mock_backend.embed.side_effect = [
            [[1.0, 0.2, 0.0]],  # content embedding
            [[1.0, 0.0, 0.0]]  # baseline embedding (similar)
        ]
        
        result = evaluator.evaluate(
            "improved content",
            context={"baseline": "original content"}
        )
        
        assert result.passed is True
        assert result.score > 0.70  # Above drift threshold
    
    def test_drift_detection_fail(self, evaluator, mock_backend):
        """Test drift detection fails when similarity is below threshold."""
        # Mock embeddings: content is very different from baseline
        # Using vectors that will have low cosine similarity (< 0.70)
        mock_backend.embed.side_effect = [
            [[1.0, 0.0, 0.0]],  # content embedding
            [[0.0, 1.0, 0.0]]  # baseline embedding (orthogonal)
        ]
        
        result = evaluator.evaluate(
            "completely different content",
            context={"baseline": "original content"}
        )
        
        assert result.passed is False
        assert result.score < 0.70  # Below drift threshold
        assert "drift" in result.feedback.lower()
    
    def test_grounding_check_pass(self, evaluator, mock_backend):
        """Test grounding check passes when similarity is above threshold."""
        # Mock embeddings: content is well grounded in sessions
        # Using vectors that will have high cosine similarity (> 0.60)
        mock_backend.embed.side_effect = [
            [[1.0, 0.2, 0.0]],  # content embedding
            [[1.0, 0.0, 0.0], [1.0, 0.1, 0.0]]  # session embeddings (similar)
        ]
        
        result = evaluator.evaluate(
            "well grounded proposal",
            context={"sessions": ["session1", "session2"]}
        )
        
        assert result.passed is True
        assert result.score > 0.60  # Above grounding threshold
    
    def test_grounding_check_fail(self, evaluator, mock_backend):
        """Test grounding check fails when similarity is below threshold."""
        # Mock embeddings: content is not grounded in sessions
        # Using vectors that will have low cosine similarity (< 0.60)
        mock_backend.embed.side_effect = [
            [[1.0, 0.0, 0.0]],  # content embedding
            [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]  # session embeddings (orthogonal)
        ]
        
        result = evaluator.evaluate(
            "ungrounded proposal",
            context={"sessions": ["session1", "session2"]}
        )
        
        assert result.passed is False
        assert result.score < 0.60  # Below grounding threshold
        assert "ground" in result.feedback.lower()
    
    def test_empty_existing_skills(self, evaluator):
        """Test duplicate detection with no existing skills."""
        result = evaluator.evaluate(
            "content",
            context={"existing_skills": []}
        )
        
        assert result.not_applicable is True
    
    def test_empty_sessions(self, evaluator):
        """Test grounding check with no sessions."""
        result = evaluator.evaluate(
            "content",
            context={"sessions": []}
        )
        
        assert result.not_applicable is True
    
    def test_embedding_error_handling(self, evaluator, mock_backend):
        """Test that embedding errors are handled gracefully."""
        mock_backend.embed.side_effect = Exception("Embedding failed")
        
        result = evaluator.evaluate(
            "content",
            context={"baseline": "baseline"}
        )
        
        assert result.passed is False
        assert result.score == 0.0
        assert "Embedding generation failed" in result.feedback
    
    def test_custom_thresholds(self):
        """Test that custom thresholds can be set via environment variables."""
        with patch.dict(os.environ, {
            "SKILL_EVOLUTION_EMBEDDING_DUPLICATE_THRESHOLD": "0.90",
            "SKILL_EVOLUTION_EMBEDDING_DRIFT_THRESHOLD": "0.80",
            "SKILL_EVOLUTION_EMBEDDING_GROUNDING_THRESHOLD": "0.70"
        }):
            mock_backend = Mock()
            with patch('embedding_similarity.get_embedding_backend', return_value=mock_backend):
                evaluator = EmbeddingSimilarityEvaluator()
                
                assert evaluator.duplicate_threshold == 0.90
                assert evaluator.drift_threshold == 0.80
                assert evaluator.grounding_threshold == 0.70
    
    def test_mode_priority_duplicate(self, evaluator, mock_backend):
        """Test that duplicate detection takes priority when multiple contexts are present."""
        mock_backend.embed.side_effect = [
            [[0.9, 0.8, 0.7]],
            [[0.91, 0.81, 0.71]]
        ]
        
        # Both existing_skills and baseline present
        result = evaluator.evaluate(
            "content",
            context={
                "existing_skills": [("skill1", "body")],
                "baseline": "baseline"
            }
        )
        
        # Should run duplicate detection (checks existing_skills)
        assert "similar" in result.feedback.lower()
    
    def test_mode_priority_drift(self, evaluator, mock_backend):
        """Test that drift detection runs when baseline and sessions are present."""
        mock_backend.embed.side_effect = [
            [[0.9, 0.8, 0.7]],
            [[0.85, 0.75, 0.65]]
        ]
        
        # Both baseline and sessions present
        result = evaluator.evaluate(
            "content",
            context={
                "baseline": "baseline",
                "sessions": ["session1"]
            }
        )
        
        # Should run drift detection (checks baseline)
        assert "drift" in result.feedback.lower() or "similarity" in result.feedback.lower()


class TestDuplicateScoring:
    """Duplicate scores follow the higher-is-better convention (KTD2)."""

    @pytest.fixture
    def evaluator(self):
        backend = Mock()
        with patch('embedding_similarity.get_embedding_backend', return_value=backend):
            yield EmbeddingSimilarityEvaluator(), backend

    @staticmethod
    def _vec(similarity):
        # Unit vector at the given cosine similarity to [1, 0].
        return [similarity, (1 - similarity ** 2) ** 0.5]

    def test_near_duplicate_fails_with_inverted_score_and_names_the_skill(self, evaluator):
        ev, backend = evaluator
        backend.embed.side_effect = [[[1.0, 0.0]], [self._vec(0.95), self._vec(0.2)]]

        result = ev.evaluate("new", {"existing_skills": [("deploy-helper", "a"), ("other", "b")]})

        assert result.passed is False
        assert result.score == pytest.approx(0.05)
        assert "deploy-helper" in result.feedback

    def test_original_skill_passes_with_full_score_and_reports_its_neighbour(self, evaluator):
        ev, backend = evaluator
        backend.embed.side_effect = [[[1.0, 0.0]], [self._vec(0.80)]]

        result = ev.evaluate("new", {"existing_skills": [("closest-skill", "a")]})

        assert result.passed is True
        assert result.score == 1.0
        assert "0.800" in result.feedback
        assert "closest-skill" in result.feedback


class TestWindowedDrift:
    """Drift compares windows that fit the model instead of whole bodies (KTD10).

    The default model truncates at 512 tokens, so two whole-body embeddings that differ
    only past the first ~2 KB come out identical.
    """

    @staticmethod
    def _backend_by_text(vectors):
        """A backend embedding each window by looking its text up in `vectors`."""
        backend = Mock()
        backend.embed.side_effect = lambda texts: [vectors(t) for t in texts]
        return backend

    def test_a_change_past_the_model_window_is_seen(self):
        # The change sits past the first 2 KB; the fake backend, like the real model,
        # only "sees" the start of each text it embeds. Whole-body embeddings of these
        # two texts would be identical.
        intro = "# Skill\n\n" + "Intro sentence. " * 80 + "\n"
        steps = "## Steps\n\n" + "Do the thing. " * 80 + "\n"
        old_tail = "## Pitfalls\n\nWatch out for X.\n"
        new_tail = "## Pitfalls\n\nSomething unrelated.\n"
        baseline, content = intro + steps + old_tail, intro + steps + new_tail
        assert len(intro + steps) > 2000

        def vectors(text):
            return [0.0, 1.0] if "unrelated" in text[:2000] else [1.0, 0.0]

        with patch('embedding_similarity.get_embedding_backend',
                   return_value=self._backend_by_text(vectors)):
            result = EmbeddingSimilarityEvaluator().evaluate(content, {"baseline": baseline})

        assert result.score < 1.0
        assert result.passed is True  # a small section changed; the skill did not drift

    def test_replacing_most_of_the_body_by_length_is_drift(self):
        keep = "# Skill\n\nShort intro.\n"
        old_rest = "## Body\n\n" + "Original guidance. " * 60 + "\n"
        new_rest = "## Body\n\n" + "Unrelated replacement. " * 60 + "\n"

        def vectors(text):
            return [0.0, 1.0] if "Unrelated" in text else [1.0, 0.0]

        with patch('embedding_similarity.get_embedding_backend',
                   return_value=self._backend_by_text(vectors)):
            result = EmbeddingSimilarityEvaluator().evaluate(keep + new_rest, {"baseline": keep + old_rest})

        assert result.passed is False
        assert result.score < 0.1

    def test_long_section_is_split_and_no_window_exceeds_the_limit(self):
        import embedding_similarity
        long_body = "# Skill\n\n" + "\n".join(f"line {i} " + "x" * 60 for i in range(200))
        seen = []

        def vectors(text):
            seen.append(text)
            return [1.0, 0.0]

        with patch('embedding_similarity.get_embedding_backend',
                   return_value=self._backend_by_text(vectors)):
            EmbeddingSimilarityEvaluator().evaluate(long_body + "\nnew line\n", {"baseline": long_body})

        assert len(seen) > 2
        assert max(len(t) for t in seen) <= embedding_similarity.DRIFT_WINDOW_CHARS

    def test_identical_content_is_not_applicable_and_embeds_nothing(self):
        backend = Mock()
        with patch('embedding_similarity.get_embedding_backend', return_value=backend):
            result = EmbeddingSimilarityEvaluator().evaluate("same body", {"baseline": "same body"})

        assert result.not_applicable is True
        backend.embed.assert_not_called()


class TestFailClosed:
    def test_missing_fastembed_fails_closed_inside_run_evaluators(self, monkeypatch):
        def no_fastembed():
            raise ImportError("fastembed not installed")

        monkeypatch.setattr(evaluate, "REGISTRY", {"embedding_similarity": EmbeddingSimilarityEvaluator})
        monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "embedding_similarity")
        with patch('embedding_similarity.get_embedding_backend', side_effect=no_fastembed):
            (result,) = evaluate.run_evaluators("body", "skill:x", {"baseline": "old body"})

        assert result.passed is False
        assert result.not_applicable is False
        assert "fastembed" in result.feedback

    def test_non_numeric_threshold_falls_back_like_other_numeric_settings(self, monkeypatch, capsys):
        monkeypatch.setenv("SKILL_EVOLUTION_EMBEDDING_DUPLICATE_THRESHOLD", "high")

        ev = EmbeddingSimilarityEvaluator()

        assert ev.duplicate_threshold == 0.90
        assert "SKILL_EVOLUTION_EMBEDDING_DUPLICATE_THRESHOLD" in capsys.readouterr().err
