"""Contract tests for optimize_skill.py against the REAL `gepa` package.

Every other gepa test monkeypatches `optimize_skill._require_gepa` to return a
hand-built fake (see `_make_fake_gepa` in test_optimize_skill.py). That keeps the
suite runnable without the optional extra, but it means those tests assert the code
against its own assumptions rather than against gepa's actual API surface -- a
module/function shadowing mismatch is invisible to them.

These tests close that gap: they exercise the symbols `run_gepa_optimization()`
depends on against the installed package, and skip cleanly when the optional
`[optimizer]` extra is absent.
"""

import dataclasses
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import optimize_skill

pytest.importorskip("gepa", reason="optional [optimizer] extra not installed")


def test_require_gepa_exposes_the_symbols_run_gepa_optimization_uses():
    """_require_gepa()'s return value must carry every symbol the call site reads off it.

    Regression guard: gepa's top-level package deliberately exposes
    `optimize_anything` as a *submodule* (see gepa/__init__.py), so reading these
    names off the top-level `gepa` package yields a non-callable module and three
    AttributeErrors.
    """
    gepa_ns = optimize_skill._require_gepa()

    assert callable(getattr(gepa_ns, "optimize_anything", None)), (
        "optimize_anything must resolve to the callable function, not the submodule"
    )
    for symbol in ("GEPAConfig", "EngineConfig", "ReflectionConfig"):
        assert hasattr(gepa_ns, symbol), f"missing {symbol}"


def test_optimize_anything_accepts_the_kwargs_the_call_site_passes():
    gepa_ns = optimize_skill._require_gepa()
    params = inspect.signature(gepa_ns.optimize_anything).parameters

    for kwarg in ("seed_candidate", "evaluator", "objective", "config"):
        assert kwarg in params, f"gepa.optimize_anything has no {kwarg!r} parameter"


def test_config_dataclasses_accept_the_fields_the_call_site_sets():
    gepa_ns = optimize_skill._require_gepa()

    def field_names(cls):
        return {f.name for f in dataclasses.fields(cls)}

    assert {"engine", "reflection"} <= field_names(gepa_ns.GEPAConfig)
    assert "max_metric_calls" in field_names(gepa_ns.EngineConfig)
    assert "reflection_lm" in field_names(gepa_ns.ReflectionConfig)


def test_the_exact_config_run_gepa_optimization_builds_is_constructible():
    """Build the real config object the same way run_gepa_optimization() does."""
    gepa_ns = optimize_skill._require_gepa()

    config = gepa_ns.GEPAConfig(
        engine=gepa_ns.EngineConfig(max_metric_calls=optimize_skill.DEFAULT_MAX_METRIC_CALLS),
        reflection=gepa_ns.ReflectionConfig(reflection_lm=optimize_skill._reflection_lm_adapter),
    )

    assert config.engine.max_metric_calls == optimize_skill.DEFAULT_MAX_METRIC_CALLS
    assert config.reflection.reflection_lm is optimize_skill._reflection_lm_adapter
