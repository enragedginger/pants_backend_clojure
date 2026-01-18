"""Tests for Clojure symbol mapping."""

from __future__ import annotations

import pytest

from pants_backend_clojure.clojure_symbol_mapping import (
    _namespace_matches_pattern,
)


class TestNamespaceMatchesPattern:
    """Tests for the _namespace_matches_pattern helper function."""

    def test_exact_match(self) -> None:
        """Test exact namespace matching."""
        assert _namespace_matches_pattern("ring.core", "ring.core") is True
        assert _namespace_matches_pattern("ring.core", "ring.other") is False
        assert _namespace_matches_pattern("ring.core", "ring") is False

    def test_recursive_glob_matches_base(self) -> None:
        """Test that .** pattern matches the base namespace itself."""
        assert _namespace_matches_pattern("ring", "ring.**") is True
        assert _namespace_matches_pattern("ring.middleware", "ring.middleware.**") is True

    def test_recursive_glob_matches_children(self) -> None:
        """Test that .** pattern matches child namespaces."""
        assert _namespace_matches_pattern("ring.core", "ring.**") is True
        assert _namespace_matches_pattern("ring.middleware.cookies", "ring.**") is True
        assert _namespace_matches_pattern("ring.middleware.session", "ring.middleware.**") is True

    def test_recursive_glob_no_partial_match(self) -> None:
        """Test that .** doesn't match partial namespace segments."""
        # "ring.**" should not match "ring-extra.core" (different root)
        assert _namespace_matches_pattern("ring-extra.core", "ring.**") is False
        # "ring.**" should not match "ringmaster.core" (prefix match but not segment)
        assert _namespace_matches_pattern("ringmaster.core", "ring.**") is False

    def test_different_namespaces(self) -> None:
        """Test that unrelated namespaces don't match."""
        assert _namespace_matches_pattern("compojure.core", "ring.**") is False
        assert _namespace_matches_pattern("other.ns", "ring.middleware.**") is False

    def test_deep_nesting(self) -> None:
        """Test deeply nested namespace matching."""
        assert _namespace_matches_pattern(
            "ring.middleware.anti-forgery.impl.detail", "ring.**"
        ) is True
        assert _namespace_matches_pattern(
            "ring.middleware.anti-forgery.impl.detail", "ring.middleware.**"
        ) is True
        assert _namespace_matches_pattern(
            "ring.middleware.anti-forgery.impl.detail", "ring.middleware.anti-forgery.**"
        ) is True

    def test_hyphenated_namespaces(self) -> None:
        """Test namespaces with hyphens (common in Clojure)."""
        assert _namespace_matches_pattern("my-app.core", "my-app.**") is True
        assert _namespace_matches_pattern("ring.middleware.anti-forgery", "ring.**") is True
        assert _namespace_matches_pattern(
            "clojure.tools.logging", "clojure.tools.**"
        ) is True

    def test_single_segment_namespace(self) -> None:
        """Test single-segment namespaces."""
        assert _namespace_matches_pattern("cheshire", "cheshire") is True
        assert _namespace_matches_pattern("cheshire", "cheshire.**") is True
        assert _namespace_matches_pattern("cheshire.core", "cheshire.**") is True


class TestClojureNamespaceMapping:
    """Integration tests for ClojureNamespaceMapping would go here.

    These would require a full RuleRunner setup with JVM subsystem configuration,
    which is more involved. The basic tests above verify the pattern matching logic.
    """
    pass
