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
        """Create an evaluator with mock backend."""
        with patch('embedding_similarity.get_embedding_backend', return_value=mock_backend):
            return EmbeddingSimilarityEvaluator()
    
    def test_evaluator_registration(self):
        """Test that EmbeddingSimilarityEvaluator is registered."""
        # Import to trigger registration
        import embedding_similarity
        
        # Check if registered
        assert "embedding_similarity" in evaluate.REGISTRY
    
    def test_evaluator_not_in_defaults(self):
        """Test that embedding_similarity is not in default evaluators."""
        assert "embedding_similarity" not in evaluate.DEFAULT_EVALUATORS
    
    def test_no_context_returns_pass(self, evaluator):
        """Test that evaluator passes when no context is provided."""
        result = evaluator.evaluate("test content", context=None)
        
        assert result.passed is True
        assert result.score == 1.0
        assert "No embedding context" in result.feedback
    
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
            context={"existing_skills": ["skill1", "skill2"]}
        )
        
        assert result.passed is True
        assert result.score < 0.85  # Below duplicate threshold
    
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
            context={"existing_skills": ["similar skill", "different skill"]}
        )
        
        assert result.passed is False
        assert result.score > 0.85  # Above duplicate threshold
        assert "too similar" in result.feedback.lower()
    
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
        
        assert result.passed is True
        assert result.score == 1.0
        assert "No existing skills" in result.feedback
    
    def test_empty_sessions(self, evaluator):
        """Test grounding check with no sessions."""
        result = evaluator.evaluate(
            "content",
            context={"sessions": []}
        )
        
        assert result.passed is True
        assert result.score == 1.0
        assert "No sessions" in result.feedback
    
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
                "existing_skills": ["skill1"],
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
