# First-party Provider Expansion — OpenAI & Gemini

- **Date:** 2026-07-28
- **Status:** **Shipped 2026-07-28** (commits `607151e`, `90b64cb`)
- **Author:** Carlo
- **Tags:** `provider`, `openai`, `gemini`, `evaluation`
- **Upstream product contract:** `docs/plans/2026-07-23-001-feat-skill-evolution-evaluation-plan.md` — R7, KD5, KD6. Already `implementation-ready`.
- **Upstream contract classification:** Implementation-ready plan with deferred item (R7's first-party provider branches). This plan implements that deferred item.

> **Status as of 2026-07-28.** All of U1–U3 shipped as written; U4 was a no-op by design.
> Every Definition-of-Done item below is met: `PROVIDER_CALLERS` has five entries,
> `resolve_provider()` resolves both new names through all three tiers with no new
> resolution logic, both branches fail closed on a missing key and on a malformed response,
> `GEMINI_API_KEY` is in `fetch_sessions.SECRET_PATTERNS`, and the unknown-provider message
> lists both names via `sorted(PROVIDER_CALLERS)`. Tests: `tests/test_evaluate_provider_openai.py`
> (11), `tests/test_evaluate_provider_gemini.py` (12), plus additions to
> `tests/test_evaluate_providers.py` and `tests/test_fetch_sessions_secrets.py`.
>
> **A follow-up review pass (`90b64cb`) found one defect this plan did not anticipate.**
> Making the base URL operator-configurable for two more providers newly exposed a latent
> gap in the shared `_post_json()`: `urllib.request.Request()` was constructed *outside*
> its `try/except`, so a scheme-less base-URL override raised a bare `ValueError` rather
> than `ProviderError` — and `optimize_skill._reflection_lm_adapter()` caught only
> `ProviderError`, so an unattended GEPA run would crash mid-flight (`gepa==0.1.4`'s engine
> defaults to `raise_on_exception=True`). Both were fixed; widening the except clause to
> `ValueError` also closed the pre-existing `json.JSONDecodeError`/`UnicodeDecodeError`
> escape, since both subclass `ValueError`. The same pass updated `SKILL.md`'s config table
> (which this plan's scope note had not covered) and added the OpenAI empty-`choices` test
> that the Gemini side already had.
>
> **Still deferred, unchanged:** MiniMax and Kimi K2 first-party branches, and a generic
> OpenAI-compatible catch-all adapter. Neither adapter has been exercised against a live
> endpoint — both are unit-tested against a stubbed `_post_json` only.

---

## Product Contract

### One-sentence description

Add first-party OpenAI and Gemini provider adapters to `scripts/evaluate.py`'s `PROVIDER_CALLERS` registry, following the same stdlib-only `_post_json()`/`_call_<provider>()` pattern the three existing providers (`claude`, `ollama`, `opencode`) already follow and wired into the same env-var selection mechanism (`SKILL_EVOLUTION_PROVIDER`, `SKILL_EVOLUTION_<EVALUATOR>_PROVIDER`).

### Problem Frame

R7 in the evaluation plan lists "OpenAI, Gemini, MiniMax, and Kimi K2" as fast-follow additions and shipped only three provider branches (Claude, Ollama, OpenCode Zen). The architecture is proven — `PROVIDER_CALLERS` dict, each `_call_<name>()` function, stdlib-only `urllib` via `_post_json()`, fail-closed on missing keys/malformed responses — but OpenAI and Gemini are the two missing first-party branches most likely to be configured by any user who has eval infrastructure. Adding them is mechanical, follows an established pattern, and closes R7's main gap (MiniMax/Kimi K2 remain deferred per user priority).

### Requirements

- R7a. OpenAI provider adapter: `_call_openai()` registered in `PROVIDER_CALLERS` as `"openai"`, configured via `OPENAI_API_KEY`, `SKILL_EVOLUTION_OPENAI_BASE_URL` (default `https://api.openai.com/v1`), `SKILL_EVOLUTION_OPENAI_MODEL` (default `gpt-4o`). Follows the same OpenAI-compatible `/chat/completions` request/response shape as the existing `_call_opencode()` and `_call_ollama()` — `choices[0].message.content`.
- R7b. Gemini provider adapter: `_call_gemini()` registered in `PROVIDER_CALLERS` as `"gemini"`, configured via `GEMINI_API_KEY`, `SKILL_EVOLUTION_GEMINI_BASE_URL` (default `https://generativelanguage.googleapis.com`), `SKILL_EVOLUTION_GEMINI_MODEL` (default `gemini-2.0-flash`). Uses Google AI's `v1beta/models/{model}:generateContent` endpoint with a different request/response shape (`contents[0].parts[0].text` → `candidates[0].content.parts[0].text`).
- R7c. Both branches fail closed on missing API keys (matching `_call_opencode()`'s posture) and on malformed response shapes (matching all three existing branches).
- R7d. Both provider names are resolved by the existing `resolve_provider()` mechanism — explicit arg > per-evaluator override (`SKILL_EVOLUTION_<NAME>_PROVIDER`) > global default (`SKILL_EVOLUTION_PROVIDER`) — with no new resolution logic.
- R7e. The error message for unknown providers (`call_provider()` line ~544) automatically lists the two new names alongside existing ones via `sorted(PROVIDER_CALLERS)` — no message change needed.
- R7f. `GEMINI_API_KEY` is added as a known pattern in `fetch_sessions.py`'s `SECRET_PATTERNS` list, matching the already-present `OPENAI_API_KEY`, so it is also caught by the secret-redaction layer (`redact_secrets()` in `evaluate.py`). Only the env-var *name* is added to the substring patterns — Google Gemini API keys are opaque alphanumeric strings with no stable prefix, so the line-level scan catches them via `GEMINI_API_KEY=` rather than a regex on the key value itself.

### Scope Boundaries

**In scope:**
- OpenAI adapter + tests
- Gemini adapter + tests
- Adding `GEMINI_API_KEY` to `SECRET_PATTERNS` in `fetch_sessions.py`
- Updating `PROVIDER_CALLERS` registration and the provider-env-var documentation

**Not in scope (deferred for later):**
- MiniMax and Kimi K2 adapter branches — still deferred per R7's original scope note ("fast-follow once the owner configures credentials")
- A generic OpenAI-compatible catch-all adapter (e.g., `SKILL_EVOLUTION_OPENAI_COMPATIBLE_BASE_URL`) for third-party OpenAI-proxies — that would be another adapter branch, not a generalization of the existing ones
- Updating the overall evaluation plan (R7) status banner — each provider branch is a self-contained unit; the status banner is a property of the evaluation plan itself, not of this implementation plan
- Migration to the production cron path — unchanged scope boundary from the evaluation plan's R16

### Key Decisions

- KD5 (from evaluation plan) — global default provider with per-evaluator override. Unchanged; both new providers use the same mechanism.
- KD6 (from evaluation plan) — extend Hermes's own provider convention rather than adopting LiteLLM. Unchanged; both branches are stdlib-only `_post_json()` adapters.

**New KDs specific to this plan:**

- KTDP1. **Gemini uses header-based auth (`x-goog-api-key`) rather than query-parameter auth.** The Gemini REST API accepts both `?key=` query param and `x-goog-api-key` header; using the header keeps the API key out of URLs (matching how OpenCode Zen uses `Authorization: Bearer`, and consistent with the codebase's convention that credentials belong in headers). This is a minor security hygiene choice, not a functional difference.
- KTDP2. **Gemini uses the `v1beta` API version, not `v1`.** The `v1beta` endpoint is the recommended stable-preview surface for Gemini 2.0 models and is what the official Google AI Python SDK targets. Since this adapter is stdlib-only and makes direct REST calls, `v1beta` is the documented, versioned surface. Overridable via `SKILL_EVOLUTION_GEMINI_BASE_URL` if Google moves the API.

---

## Planning Contract

### Product Contract preservation

No R/KD IDs from the upstream evaluation plan are added, removed, or renumbered. R7's scope is expanded internally (R7a–R7f are new sub-items that sit under R7). This section and the Implementation Units below are additive.

### Key Technical Decisions

- KTD1. **One new `_call_<provider>()` function per branch**, each registered in the existing `PROVIDER_CALLERS` dict at module scope. Same pattern as `_call_claude()`/`_call_ollama()`/`_call_opencode()`. Governs R7a, R7b.
- KTD2. **Gemini request translation**: the existing `_post_json()` is general enough — Gemini's request body shape (`{"contents": [{"parts": [{"text": prompt}]}]}`) is different from the OpenAI-compatible one (`{"model": ..., "messages": [...]}`) but is just a different JSON body; `_call_gemini()` builds the Gemini-shaped body before calling `_post_json()`. No new transport abstraction is needed. Governs R7b.
- KTD3. **Gemini response parsing**: reads `candidates[0].content.parts[0].text` (vs. `choices[0].message.content` for OpenAI-compatible). Raises `ProviderError` on missing keys with the actual payload included in the message, matching the pattern of the three existing callers. Governs R7b, R7c.
- KTD4. **Env var naming** follows the established `SKILL_EVOLUTION_<PROVIDER>_<SETTING>` convention:
  - `SKILL_EVOLUTION_OPENAI_BASE_URL` / `SKILL_EVOLUTION_OPENAI_MODEL`
  - `SKILL_EVOLUTION_GEMINI_BASE_URL` / `SKILL_EVOLUTION_GEMINI_MODEL`
  - `OPENAI_API_KEY` / `GEMINI_API_KEY` (following each platform's own convention — the same posture as `ANTHROPIC_API_KEY` and `OPENCODE_API_KEY`)
  Governs R7a, R7b.
- KTD5. **No default `SKILL_EVOLUTION_<PROVIDER>_BASE_URL` constant exported at module scope.** The three existing callers are inconsistent: `_call_opencode()` exports `OPENCODE_BASE_URL_ENV_VAR` as a named module constant, `_call_ollama()` does not. Following the simpler pattern (no named constant) for both new branches, since no other module references these fallback URLs. The env-var name is documented in the function docstring and in this plan. Governs R7a, R7b.
- KTD6. **Tests follow existing conventions**: a `test_evaluate_providers.py` reader for basic registration/resolution/redaction tests, and standalone `test_evaluate_provider_openai.py` / `test_evaluate_provider_gemini.py` files for the request/response shape and auth tests (matching how `test_evaluate_provider_opencode.py` exists alongside `test_evaluate_providers.py`). Governs KTV1–KTV3.

### Key Verification Points

- KTV1. `_call_openai()` sends correct URL, body shape, auth header, and parses the response correctly. Verified by unit tests stubbing `_post_json` (matching `test_evaluate_provider_opencode.py`'s `captured_post` fixture pattern).
- KTV2. `_call_gemini()` sends the Google-shaped request body and parses the Gemini-shaped response correctly. Verified by the same stubbed-`_post_json` fixture, with an additional test for the different response path.
- KTV3. Both new providers resolve through `resolve_provider()` — global default, per-evaluator override, explicit arg. Verified by tests in `test_evaluate_providers.py` (matching `test_global_provider_used_when_no_override` and `test_per_evaluator_override_takes_precedence`).
- KTV4. Missing `OPENAI_API_KEY` and missing `GEMINI_API_KEY` both raise `ProviderError` with an actionable message. Verified by dedicated tests per branch.
- KTV5. `GEMINI_API_KEY` is caught by `redact_secrets()`. Verified by adding a line to `test_fetch_sessions_secrets.py` or `test_evaluate_providers.py`'s redaction tests.
- KTV6. All existing tests continue to pass: `pytest` yields 236+ passed (the existing test count plus new tests).

### Sources / Research

- OpenAI Chat Completions API: https://platform.openai.com/docs/api-reference/chat
- Google AI Gemini API (v1beta): https://ai.google.dev/api/generate-content
- Existing branch implementations in `scripts/evaluate.py`: `_call_claude()` (Anthropic Messages API), `_call_ollama()` (OpenAI-compatible `/chat/completions`), `_call_opencode()` (OpenAI-compatible with bearer auth)
- Existing test patterns in `tests/test_evaluate_providers.py` and `tests/test_evaluate_provider_opencode.py`
- `fetch_sessions.py` line ~31: `SECRET_PATTERNS` list already includes `"OPENAI_API_KEY"`; `"GEMINI_API_KEY"` is the additive change

---

## Implementation Units

### Phase A — OpenAI provider adapter

#### U1. `_call_openai()` function

**Goal:** Add OpenAI as a resolvable provider name, wired into `PROVIDER_CALLERS`.

**Requirements:** R7a, R7c, R7d.

**Dependencies:** None — the `_post_json()` helper handles all transport; the OpenAI API is OpenAI-compatible (same response shape as `_call_opencode()` and `_call_ollama()`).

**Files:**
- `scripts/evaluate.py` — add `_call_openai()` function + register in `PROVIDER_CALLERS`
- `tests/test_evaluate_provider_openai.py` (new)
- `tests/test_evaluate_providers.py` — add registration/resolution assertions

**Approach:**
```python
def _call_openai(prompt: str, timeout: int = 60) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ProviderError(
            "OpenAI provider selected but OPENAI_API_KEY is not set. "
            "Export it (do not commit it) before running evaluators against OpenAI."
        )
    base_url = os.environ.get("SKILL_EVOLUTION_OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("SKILL_EVOLUTION_OPENAI_MODEL", "gpt-4o")
    payload = _post_json(
        f"{base_url.rstrip('/')}/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": prompt}]},
        {"authorization": f"Bearer {api_key}"},
        timeout, "OpenAI",
    )
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ProviderError(f"Unexpected OpenAI response shape: {payload}") from e
```

Registration: `PROVIDER_CALLERS["openai"] = _call_openai` — one line alongside the existing three entries.

**Tests (KTV1, KTV3, KTV4):**
- `test_is_registered`: `"openai" in evaluate.PROVIDER_CALLERS`
- `test_resolves_globally`: `SKILL_EVOLUTION_PROVIDER=openai` → `resolve_provider() == "openai"`
- `test_resolves_per_evaluator`: `SKILL_EVOLUTION_LLM_JUDGE_PROVIDER=openai` → `resolve_provider(evaluator_name="llm_judge") == "openai"`
- `test_default_url_and_model` (captured_post fixture): default URL is `https://api.openai.com/v1/chat/completions`, default model `gpt-4o`
- `test_url_and_model_overridable` (captured_post fixture): env vars `SKILL_EVOLUTION_OPENAI_BASE_URL`/`SKILL_EVOLUTION_OPENAI_MODEL` are respected
- `test_sends_bearer_token` (captured_post fixture): header is `Authorization: Bearer <key>`
- `test_missing_api_key_fails_closed`: no `OPENAI_API_KEY` → `ProviderError` with actionable message
- `test_malformed_response_raises`: no `choices` key → `ProviderError`
- `test_redaction_runs_before_request` (matching `test_evaluate_provider_opencode.py:110`)
- `test_api_key_not_in_url` (matching `test_evaluate_provider_opencode.py:121`)

---

### Phase B — Gemini provider adapter

#### U2. `_call_gemini()` function

**Goal:** Add Gemini as a resolvable provider name, wired into `PROVIDER_CALLERS`.

**Requirements:** R7b, R7c, R7d.

**Dependencies:** None — `_post_json()` handles transport; `_call_gemini()` handles the Google-specific request shaping and response parsing.

**Files:**
- `scripts/evaluate.py` — add `_call_gemini()` function + register in `PROVIDER_CALLERS`
- `tests/test_evaluate_provider_gemini.py` (new)
- `tests/test_evaluate_providers.py` — add registration/resolution assertions

**Approach:**
```python
def _call_gemini(prompt: str, timeout: int = 60) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ProviderError(
            "Gemini provider selected but GEMINI_API_KEY is not set. "
            "Export it (do not commit it) before running evaluators against Gemini."
        )
    base_url = os.environ.get("SKILL_EVOLUTION_GEMINI_BASE_URL",
                               "https://generativelanguage.googleapis.com")
    model = os.environ.get("SKILL_EVOLUTION_GEMINI_MODEL", "gemini-2.0-flash")
    payload = _post_json(
        f"{base_url.rstrip('/')}/v1beta/models/{model}:generateContent",
        {"contents": [{"parts": [{"text": prompt}]}]},
        {"x-goog-api-key": api_key},
        timeout, "Gemini",
    )
    try:
        return payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise ProviderError(f"Unexpected Gemini response shape: {payload}") from e
```

Registration: `PROVIDER_CALLERS["gemini"] = _call_gemini` — one line.

**Tests (KTV2, KTV3, KTV4):**
- `test_is_registered`: `"gemini" in evaluate.PROVIDER_CALLERS`
- `test_resolves_globally` / `test_resolves_per_evaluator` — same pattern as U1
- `test_default_url_and_model`: default URL is `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent`, default model `gemini-2.0-flash`
- `test_sends_gemini_shaped_body` (captured_post fixture): body is `{"contents": [{"parts": [{"text": "..."}]}]}` not `{"messages": [...]}`
- `test_parses_gemini_shaped_response`: stub returns `{"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}` → returns `"ok"`
- `test_handles_empty_candidates`: stub returns `{"candidates": []}` → `ProviderError`
- `test_sends_x_goog_api_key_header` (captured_post fixture): header is `x-goog-api-key: <key>`
- `test_missing_api_key_fails_closed`: no `GEMINI_API_KEY` → `ProviderError` with actionable message
- `test_malformed_response_raises`: no `candidates` key → `ProviderError`
- `test_redaction_runs_before_request`
- `test_base_url_and_model_overridable`

---

### Phase C — Secret pattern and documentation

#### U3. Add `GEMINI_API_KEY` to `SECRET_PATTERNS`

**Goal:** Ensure Gemini API keys that appear in session text or proposals are caught by the redaction layer before they reach a provider or disk.

**Requirements:** R7f.

**Dependencies:** U2 (adds the env var).

**Files:**
- `scripts/fetch_sessions.py` — one addition to the `SECRET_PATTERNS` list

**Approach:**
Insert `"GEMINI_API_KEY"` into the `SECRET_PATTERNS` list at `fetch_sessions.py:31` in alphabetical position, between the existing `"ANTHROPIC_API_KEY"` and `"-----BEGIN"` entries.

**Tests:**
- `test_gemini_api_key_in_secret_patterns` — verify the string `"GEMINI_API_KEY"` is in `fetch_sessions.SECRET_PATTERNS` (existing `test_fetch_sessions_secrets.py` is the natural home, but adding to `test_evaluate_providers.py`'s redaction tests is equivalent — the redaction is re-exported via `evaluate.redact_secrets()`)
- `test_redact_secrets_removes_gemini_env_var` — `"GEMINI_API_KEY=AIza..."` → `"[REDACTED]"` (in `test_evaluate_providers.py`)
- `test_redact_secrets_preserves_unrelated_lines` — already tested generically; no new test needed unless the existing one doesn't cover the Gemini pattern
- `test_redact_skipped_for_unknown_patterns` — Gemini key values with no `GEMINI_API_KEY=` prefix pass through (only the env-var *name* is a known pattern, not the opaque value itself)

#### U4. Plan cleanup (no code change)

**Goal:** No stale references to the "fast-follow" status of OpenAI/Gemini.

**Files:** None in this plan — the evaluation plan itself (the upstream product contract) is a separate artifact and updating its status banner is explicitly out of scope per this plan's scope boundaries.

---

## Verification Contract

### Test execution

Before merging:

```bash
pytest tests/test_evaluate_providers.py tests/test_evaluate_provider_openai.py tests/test_evaluate_provider_gemini.py tests/test_fetch_sessions_secrets.py -v
```

Expected: all existing tests still pass, all new tests pass.

### Definition of Done

All of:

1. `_call_openai()` and `_call_gemini()` exist in `scripts/evaluate.py`, each accepting `(prompt: str, timeout: int = 60) -> str` and registered in `PROVIDER_CALLERS`.
2. `PROVIDER_CALLERS` has five entries: `claude`, `ollama`, `opencode`, `openai`, `gemini`.
3. `resolve_provider()` resolves both new names via all three resolution tiers (global default, per-evaluator override, explicit arg).
4. Missing `OPENAI_API_KEY` raises `ProviderError` with an actionable message — no silent fallback to an unauthenticated request.
5. Missing `GEMINI_API_KEY` raises `ProviderError` with an actionable message — no silent fallback.
6. Malformed response shapes from both providers raise `ProviderError` — fail-closed per R21.
7. `GEMINI_API_KEY` appears in `fetch_sessions.SECRET_PATTERNS`.
8. `redact_secrets()` removes a `GEMINI_API_KEY=...` line before it reaches a provider or disk.
9. All new tests exist and pass. All existing tests continue to pass.
10. The CLI error message for an unknown provider name lists both `"gemini"` and `"openai"` in its available-providers list.
