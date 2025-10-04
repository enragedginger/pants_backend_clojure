"""Clojure backend for Pants."""

from clojure_backend.target_types import (
    ClojureSourceTarget,
    ClojureSourcesGeneratorTarget,
    rules as target_type_rules,
)


def target_types():
    """Register target types with Pants."""
    return [
        ClojureSourceTarget,
        ClojureSourcesGeneratorTarget,
    ]


def rules():
    """Register rules with Pants."""
    return [
        *target_type_rules(),
    ]
