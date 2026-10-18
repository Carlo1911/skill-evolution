"""Embedding similarity evaluator for semantic comparison.

This evaluator uses embeddings to detect:
- Duplicate skills (similarity > threshold with existing skills)
- Semantic drift (similarity < threshold between baseline and new version)
- Poor grounding (similarity < threshold between proposal and source sessions)

Configuration:
    SKILL_EVOLUTION_EMBEDDING_DUPLICATE_THRESHOLD: Max similarity before flagging as duplicate (default: 0.90)
    SKILL_EVOLUTION_EMBEDDING_DRIFT_THRESHOLD: Min similarity before flagging as drift (default: 0.90)
    SKILL_EVOLUTION_EMBEDDING_GROUNDING_THRESHOLD: Min similarity for grounding (default: 0.74)

Usage:
    This evaluator is opt-in. Add to SKILL_EVOLUTION_EVALUATORS:
        SKILL_EVOLUTION_EVALUATORS=deterministic,llm_judge,regression,embedding_similarity
    
    The checks are chosen by the context, and every applicable one runs:
    - "existing_skills": duplicate_detection (a new skill), or else
    - "baseline": drift_detection (a changed body) -- the two never co-occur;
    - "sessions": grounding_check, on "grounding_text" (the proposed new text) when given,
      in addition to either of the above.
    The result fails if any check fails and scores the lowest of them. With no check
    applicable it abstains as "not applicable" rather than reporting a pass it never
    measured.
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

DEFAULT_GROUNDING_THRESHOLD = 0.74

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
        # Grounding, calibrated 2026-10-18 on the 17 real proposals with new text and cited
        # sessions (16 valid, 1 applied-then-stale): each proposal's new text against its
        # cited sessions (positives) and against 3 random draws of as many non-cited
        # sessions (50 negatives). Weighted similarity: positives 0.71-0.85 (median 0.80),
        # negatives 0.58-0.80 (median 0.69), AUC 0.96. At 0.74, 16/17 cited pass and 12% of
        # non-cited draws do. The old default, 0.60, passed every negative.
        self.grounding_threshold = env_float("SKILL_EVOLUTION_EMBEDDING_GROUNDING_THRESHOLD",
                                             DEFAULT_GROUNDING_THRESHOLD)

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

        checks = []
        if "existing_skills" in context:
            checks.append(("duplicates", lambda: self._check_duplicates(content, context["existing_skills"])))
        elif "baseline" in context:
            checks.append(("drift", lambda: self._check_drift(content, context["baseline"])))
        if "sessions" in context:
            text = context.get("grounding_text", content)
            checks.append(("grounding", lambda: self._check_grounding(text, context["sessions"])))
        if not checks:
            return self._not_applicable("no existing skills, baseline or sessions to compare against")

        results = [(label, run()) for label, run in checks]
        if len(results) == 1:
            return results[0][1]
        measured = [r for _, r in results if not r.not_applicable]
        feedback = "; ".join(f"{label}: {r.feedback}" for label, r in results)
        if not measured:
            return self._not_applicable(feedback)
        return EvalResult(
            score=min(r.score for r in measured),
            passed=all(r.passed for r in measured),
            evaluator_name=self.name,
            feedback=feedback,
        )
    
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
            
            # A pass scores 1.0, like a duplicate-check pass: any real change measures
            # below 1.0, and feeding that into the regression mean would make a passing,
            # judge-improved change fail regression for being a change at all.
            return EvalResult(
                score=1.0,
                passed=True,
                evaluator_name=self.name,
                feedback=f"Semantic drift within acceptable range (similarity: {similarity:.3f})"
            )
            
        except Exception as e:
            return self._fail(f"Embedding generation failed: {str(e)}")
    
    def _check_grounding(self, content: str, sessions: List[str]) -> EvalResult:
        """Check that `content` -- the proposed new text -- is supported by the sessions
        it cites.

        Windowed like drift, for the same reason (the model reads 512 tokens): each window
        of `content` is matched to its most similar session window, and the result is the
        length-weighted mean of those matches. A section the sessions never discussed
        pulls the mean down in proportion to its length; comparing whole texts would only
        have compared their openings. A pass scores 1.0, like the other checks, so a
        supported change does not drag the regression mean down.

        Args:
            content: Proposed new text to check
            sessions: Message texts of the cited sessions
        """
        if not content.strip():
            return self._not_applicable("no proposed text to check against the sessions")
        session_windows = [w for text in sessions if text and text.strip() for w in _windows(text)]
        if not session_windows:
            return self._not_applicable("no session text to check grounding against")

        try:
            content_windows = _windows(content)
            content_embeddings = self.backend.embed(content_windows)
            session_embeddings = self.backend.embed(session_windows)

            matches = [max(cosine_similarity(c, s) for s in session_embeddings)
                       for c in content_embeddings]
            weights = [len(w) for w in content_windows]
            similarity = sum(m * w for m, w in zip(matches, weights)) / sum(weights)

            if similarity < self.grounding_threshold:
                return EvalResult(
                    score=similarity,
                    passed=False,
                    evaluator_name=self.name,
                    feedback=f"Content is not well grounded in sessions (similarity: {similarity:.3f}, threshold: {self.grounding_threshold:.3f}). May lack evidence from source material."
                )

            return EvalResult(
                score=1.0,
                passed=True,
                evaluator_name=self.name,
                feedback=f"Content is grounded in sessions (similarity: {similarity:.3f})"
            )

        except Exception as e:
            return self._fail(f"Embedding generation failed: {str(e)}")


# Register the evaluator
register_evaluator(EmbeddingSimilarityEvaluator.name, EmbeddingSimilarityEvaluator)
