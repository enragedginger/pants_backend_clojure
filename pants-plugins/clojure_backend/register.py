"""Clojure backend for Pants."""

from clojure_backend import clj_test_runner, compile_clj
from clojure_backend.target_types import (
    ClojureSourceTarget,
    ClojureSourcesGeneratorTarget,
    ClojureTestTarget,
    ClojureTestsGeneratorTarget,
    rules as target_type_rules,
)


def target_types():
    """Register target types with Pants."""
    return [
        ClojureSourceTarget,
        ClojureSourcesGeneratorTarget,
        ClojureTestTarget,
        ClojureTestsGeneratorTarget,
    ]


def rules():
    """Register rules with Pants."""
    return [
        *target_type_rules(),
        *compile_clj.rules(),
        *clj_test_runner.rules(),
    ]
