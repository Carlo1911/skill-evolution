"""Embedding similarity evaluator for semantic comparison.

This evaluator uses embeddings to detect:
- Duplicate skills (similarity > threshold with existing skills)
- Semantic drift (similarity < threshold between baseline and new version)
- Poor grounding (similarity < threshold between proposal and source sessions)

Configuration:
    SKILL_EVOLUTION_EMBEDDING_DUPLICATE_THRESHOLD: Max similarity before flagging as duplicate (default: 0.85)
    SKILL_EVOLUTION_EMBEDDING_DRIFT_THRESHOLD: Min similarity before flagging as drift (default: 0.70)
    SKILL_EVOLUTION_EMBEDDING_GROUNDING_THRESHOLD: Min similarity for grounding (default: 0.60)

Usage:
    This evaluator is opt-in. Add to SKILL_EVOLUTION_EVALUATORS:
        SKILL_EVOLUTION_EVALUATORS=deterministic,llm_judge,regression,embedding_similarity
    
    The evaluator mode is determined by the context:
    - If context contains "existing_skills": runs duplicate_detection
    - If context contains "baseline": runs drift_detection
    - If context contains "sessions": runs grounding_check
"""

import os
from typing import Any, Dict, List, Optional

from evaluate import Evaluator, EvalResult, register_evaluator

from embedding_backends import EmbeddingBackend, get_embedding_backend


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Calculate cosine similarity between two vectors.
    
    Args:
        vec1: First vector
        vec2: Second vector
        
    Returns:
        Cosine similarity (0.0 to 1.0)
    """
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    magnitude1 = sum(a * a for a in vec1) ** 0.5
    magnitude2 = sum(b * b for b in vec2) ** 0.5
    
    if magnitude1 == 0 or magnitude2 == 0:
        return 0.0
    
    return dot_product / (magnitude1 * magnitude2)


class EmbeddingSimilarityEvaluator(Evaluator):
    """Evaluator that uses embeddings for semantic similarity checks.
    
    This evaluator can operate in three modes:
    1. duplicate_detection: Flags content too similar to existing skills
    2. drift_detection: Flags content too different from baseline
    3. grounding_check: Flags content not grounded in source sessions
    
    The mode is automatically selected based on the context provided.
    """
    
    name = "embedding_similarity"
    
    def __init__(self):
        """Initialize the evaluator with configured backend and thresholds."""
        self.backend: EmbeddingBackend = get_embedding_backend()
        
        self.duplicate_threshold = float(os.getenv(
            "SKILL_EVOLUTION_EMBEDDING_DUPLICATE_THRESHOLD",
            "0.85"
        ))
        self.drift_threshold = float(os.getenv(
            "SKILL_EVOLUTION_EMBEDDING_DRIFT_THRESHOLD",
            "0.70"
        ))
        self.grounding_threshold = float(os.getenv(
            "SKILL_EVOLUTION_EMBEDDING_GROUNDING_THRESHOLD",
            "0.60"
        ))
    
    def evaluate(self, content: str, context: Optional[Dict[str, Any]] = None) -> EvalResult:
        """Evaluate content using embedding similarity.
        
        Args:
            content: The content to evaluate (skill body, proposal, etc.)
            context: Optional context dict containing:
                - existing_skills: List of existing skill bodies for duplicate detection
                - baseline: Baseline skill body for drift detection
                - sessions: List of session texts for grounding check
        
        Returns:
            EvalResult with score (similarity metric) and feedback
        """
        if context is None:
            context = {}
        
        # Determine mode based on context
        if "existing_skills" in context:
            return self._check_duplicates(content, context["existing_skills"])
        elif "baseline" in context:
            return self._check_drift(content, context["baseline"])
        elif "sessions" in context:
            return self._check_grounding(content, context["sessions"])
        else:
            # No context provided, skip evaluation
            return EvalResult(
                score=1.0,
                passed=True,
                evaluator_name=self.name,
                feedback="No embedding context provided, skipping similarity check"
            )
    
    def _check_duplicates(self, content: str, existing_skills: List[str]) -> EvalResult:
        """Check if content is too similar to existing skills (duplicate detection).
        
        Args:
            content: New content to check
            existing_skills: List of existing skill bodies
            
        Returns:
            EvalResult with similarity score and feedback
        """
        if not existing_skills:
            return EvalResult(
                score=1.0,
                passed=True,
                evaluator_name=self.name,
                feedback="No existing skills to compare against"
            )
        
        try:
            # Generate embeddings
            content_embedding = self.backend.embed([content])[0]
            existing_embeddings = self.backend.embed(existing_skills)
            
            # Calculate similarities
            similarities = [
                cosine_similarity(content_embedding, existing_emb)
                for existing_emb in existing_embeddings
            ]
            
            max_similarity = max(similarities)
            
            if max_similarity > self.duplicate_threshold:
                return EvalResult(
                    score=max_similarity,
                    passed=False,
                    evaluator_name=self.name,
                    feedback=f"Content is too similar to existing skill (similarity: {max_similarity:.3f}, threshold: {self.duplicate_threshold:.3f}). Possible duplicate."
                )
            
            return EvalResult(
                score=max_similarity,
                passed=True,
                evaluator_name=self.name,
                feedback=f"No duplicate detected (max similarity: {max_similarity:.3f})"
            )
            
        except Exception as e:
            return self._fail(f"Embedding generation failed: {str(e)}")
    
    def _check_drift(self, content: str, baseline: str) -> EvalResult:
        """Check if content has drifted too far from baseline (drift detection).
        
        Args:
            content: New content to check
            baseline: Original/baseline content
            
        Returns:
            EvalResult with similarity score and feedback
        """
        try:
            # Generate embeddings
            content_embedding = self.backend.embed([content])[0]
            baseline_embedding = self.backend.embed([baseline])[0]
            
            # Calculate similarity
            similarity = cosine_similarity(content_embedding, baseline_embedding)
            
            if similarity < self.drift_threshold:
                return EvalResult(
                    score=similarity,
                    passed=False,
                    evaluator_name=self.name,
                    feedback=f"Content has drifted too far from baseline (similarity: {similarity:.3f}, threshold: {self.drift_threshold:.3f}). Significant semantic change."
                )
            
            return EvalResult(
                score=similarity,
                passed=True,
                evaluator_name=self.name,
                feedback=f"Semantic drift within acceptable range (similarity: {similarity:.3f})"
            )
            
        except Exception as e:
            return self._fail(f"Embedding generation failed: {str(e)}")
    
    def _check_grounding(self, content: str, sessions: List[str]) -> EvalResult:
        """Check if content is grounded in source sessions.
        
        Args:
            content: Content to check (proposal, skill, etc.)
            sessions: List of session texts that should ground this content
            
        Returns:
            EvalResult with average similarity score and feedback
        """
        if not sessions:
            return EvalResult(
                score=1.0,
                passed=True,
                evaluator_name=self.name,
                feedback="No sessions provided for grounding check"
            )
        
        try:
            # Generate embeddings
            content_embedding = self.backend.embed([content])[0]
            session_embeddings = self.backend.embed(sessions)
            
            # Calculate average similarity
            similarities = [
                cosine_similarity(content_embedding, session_emb)
                for session_emb in session_embeddings
            ]
            
            avg_similarity = sum(similarities) / len(similarities)
            
            if avg_similarity < self.grounding_threshold:
                return EvalResult(
                    score=avg_similarity,
                    passed=False,
                    evaluator_name=self.name,
                    feedback=f"Content is not well grounded in sessions (avg similarity: {avg_similarity:.3f}, threshold: {self.grounding_threshold:.3f}). May lack evidence from source material."
                )
            
            return EvalResult(
                score=avg_similarity,
                passed=True,
                evaluator_name=self.name,
                feedback=f"Content is well grounded in sessions (avg similarity: {avg_similarity:.3f})"
            )
            
        except Exception as e:
            return self._fail(f"Embedding generation failed: {str(e)}")


# Register the evaluator
register_evaluator(EmbeddingSimilarityEvaluator.name, EmbeddingSimilarityEvaluator)
