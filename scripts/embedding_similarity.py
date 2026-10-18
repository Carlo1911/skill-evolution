"""Embedding similarity evaluator for semantic comparison.

This evaluator uses embeddings to detect:
- Duplicate skills (similarity > threshold with existing skills)
- Semantic drift (similarity < threshold between baseline and new version)
- Poor grounding (similarity < threshold between proposal and source sessions)

Configuration:
    SKILL_EVOLUTION_EMBEDDING_DUPLICATE_THRESHOLD: Max similarity before flagging as duplicate (default: 0.90)
    SKILL_EVOLUTION_EMBEDDING_DRIFT_THRESHOLD: Min similarity before flagging as drift (default: 0.90)
    SKILL_EVOLUTION_EMBEDDING_GROUNDING_THRESHOLD: Min similarity for grounding (default: 0.60)

Usage:
    This evaluator is opt-in. Add to SKILL_EVOLUTION_EVALUATORS:
        SKILL_EVOLUTION_EVALUATORS=deterministic,llm_judge,regression,embedding_similarity
    
    The evaluator mode is determined by the context:
    - If context contains "existing_skills": runs duplicate_detection
    - If context contains "baseline": runs drift_detection
    - If context contains "sessions": runs grounding_check
    With none of them (or an empty one) it abstains as "not applicable" rather than
    reporting a pass it never measured.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from evaluate import Evaluator, EvalResult, register_evaluator
from fetch_sessions import env_float

from embedding_backends import EmbeddingBackend, get_embedding_backend


# The default model (BAAI/bge-small-en-v1.5) truncates every input at 512 tokens, about
# 2 KB of English, while installed skills run to ~9 KB at the median. Whole-body drift
# embeddings therefore compare only each body's opening and come out identical for most
# real changes. Drift compares windows that fit instead, with headroom under the limit.
DRIFT_WINDOW_CHARS = 1500

_HEADING = re.compile(r"^#{1,6} ", re.MULTILINE)


def _windows(text: str, limit: int = DRIFT_WINDOW_CHARS) -> List[str]:
    """Split `text` at markdown headings, then any section over `limit` at line breaks
    (or hard, for a single line longer than `limit`)."""
    starts = [m.start() for m in _HEADING.finditer(text)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    sections = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]

    windows: List[str] = []
    for section in sections:
        current = ""
        for line in section.splitlines(keepends=True):
            while len(line) > limit:
                if current:
                    windows.append(current)
                    current = ""
                windows.append(line[:limit])
                line = line[limit:]
            if len(current) + len(line) > limit:
                windows.append(current)
                current = ""
            current += line
        if current.strip():
            windows.append(current)
    return [w for w in windows if w.strip()] or [text[:limit]]


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
        """Read thresholds; the backend is built on first use, not here.

        evaluate.get_enabled_evaluators() instantiates evaluators on every
        run_evaluators() call, so building the backend here would load the model for
        every target of every proposal -- and a missing fastembed would raise outside
        the per-evaluator fail-closed wrapper instead of failing this evaluator.
        """
        self._backend: Optional[EmbeddingBackend] = None

        # Defaults calibrated on 176 installed skills with the default model: at 0.90,
        # 11% of skills have a neighbour above the duplicate line (0.85 flagged 31%), and
        # the weighted drift similarity of real applied changes is 0.98-1.00 while
        # wholesale rewrites score 0.64-0.73.
        self.duplicate_threshold = env_float("SKILL_EVOLUTION_EMBEDDING_DUPLICATE_THRESHOLD", 0.90)
        self.drift_threshold = env_float("SKILL_EVOLUTION_EMBEDDING_DRIFT_THRESHOLD", 0.90)
        self.grounding_threshold = env_float("SKILL_EVOLUTION_EMBEDDING_GROUNDING_THRESHOLD", 0.60)

    @property
    def backend(self) -> EmbeddingBackend:
        if self._backend is None:
            self._backend = get_embedding_backend()
        return self._backend
    
    def evaluate(self, content: str, context: Optional[Dict[str, Any]] = None) -> EvalResult:
        """Evaluate content using embedding similarity.
        
        Args:
            content: The content to evaluate (skill body, proposal, etc.)
            context: Optional context dict containing:
                - existing_skills: (name, body) pairs of installed skills for duplicate detection
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
            return self._not_applicable("no existing skills, baseline or sessions to compare against")
    
    def _check_duplicates(self, content: str, existing_skills: List[Tuple[str, str]]) -> EvalResult:
        """Check if content is too similar to existing skills (duplicate detection).

        Each body is embedded whole, so the model sees its opening (frontmatter and
        introduction) -- the part that best tells one skill from another.

        Args:
            content: New content to check
            existing_skills: (name, body) pairs of installed skills

        Returns:
            EvalResult scored higher-is-better: 1.0 when it passes, 1 - similarity when
            it blocks. The raw similarity and the closest skill go in the feedback.
        """
        if not existing_skills:
            return self._not_applicable("no installed skills to compare against")
        
        try:
            names = [name for name, _ in existing_skills]
            content_embedding = self.backend.embed([content])[0]
            existing_embeddings = self.backend.embed([body for _, body in existing_skills])
            
            similarities = [
                cosine_similarity(content_embedding, existing_emb)
                for existing_emb in existing_embeddings
            ]
            closest = max(range(len(similarities)), key=similarities.__getitem__)
            max_similarity = similarities[closest]

            # Between distinct installed skills the nearest-neighbour similarity already
            # runs ~0.67-0.97 with the default model, so scoring a pass as 1 - similarity
            # would record every original skill at 0.03-0.33 and drag gate means down.
            if max_similarity > self.duplicate_threshold:
                return EvalResult(
                    score=1.0 - max_similarity,
                    passed=False,
                    evaluator_name=self.name,
                    feedback=f"Content is too similar to existing skill '{names[closest]}' (similarity: {max_similarity:.3f}, threshold: {self.duplicate_threshold:.3f}). Possible duplicate."
                )
            
            return EvalResult(
                score=1.0,
                passed=True,
                evaluator_name=self.name,
                feedback=f"No duplicate detected (closest: '{names[closest]}', similarity: {max_similarity:.3f})"
            )
            
        except Exception as e:
            return self._fail(f"Embedding generation failed: {str(e)}")
    
    def _check_drift(self, content: str, baseline: str) -> EvalResult:
        """Check if content has drifted too far from baseline (drift detection).

        Both texts are split into windows that fit the model (see DRIFT_WINDOW_CHARS).
        Each content window is matched to its most similar baseline window, and the drift
        similarity is the mean of those matches weighted by window length -- how much of
        the new text still corresponds to the old. Not the minimum: measured on installed
        skills, the single worst window scored an added section (0.64-0.75) no better than
        a wholesale rewrite (0.54-0.66), while the weighted mean kept real, applied changes
        and added sections at 0.98-1.00 and rewrites at 0.64-0.73.

        Args:
            content: New content to check
            baseline: Original/baseline content
            
        Returns:
            EvalResult with similarity score and feedback
        """
        if content == baseline:
            return self._not_applicable("content is identical to the baseline; nothing changed")
        try:
            content_windows = _windows(content)
            content_embeddings = self.backend.embed(content_windows)
            baseline_embeddings = self.backend.embed(_windows(baseline))

            matches = [max(cosine_similarity(c, b) for b in baseline_embeddings)
                       for c in content_embeddings]
            weights = [len(w) for w in content_windows]
            similarity = sum(m * w for m, w in zip(matches, weights)) / sum(weights)
            
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
            return self._not_applicable("no sessions to check grounding against")
        
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
