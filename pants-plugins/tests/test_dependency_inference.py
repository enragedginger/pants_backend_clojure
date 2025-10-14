"""Tests for Clojure dependency inference."""

import pytest

from clojure_backend.dependency_inference import (
    parse_clojure_namespace,
    parse_clojure_requires,
    namespace_to_path,
    path_to_namespace,
)


def test_parse_clojure_namespace():
    """Test extracting namespace from Clojure source."""
    source = "(ns example.project-a.core)"
    assert parse_clojure_namespace(source) == "example.project-a.core"


def test_parse_clojure_namespace_with_docstring():
    """Test extracting namespace with docstring."""
    source = """(ns example.project-a.core
      "This is a docstring")"""
    assert parse_clojure_namespace(source) == "example.project-a.core"


def test_parse_clojure_requires_simple():
    """Test parsing simple :require forms."""
    source = """(ns example.foo
      (:require [example.bar :as bar]
                [example.baz]))"""

    result = parse_clojure_requires(source)
    assert result == {"example.bar", "example.baz"}


def test_parse_clojure_requires_with_refer():
    """Test parsing :require with :refer."""
    source = """(ns example.foo
      (:require [clojure.test :refer [deftest is testing]]))"""

    result = parse_clojure_requires(source)
    assert result == {"clojure.test"}


def test_parse_clojure_use_simple():
    """Test parsing simple :use forms."""
    source = """(ns example.foo
      (:use [example.qux]))"""

    result = parse_clojure_requires(source)
    assert result == {"example.qux"}


def test_parse_clojure_require_and_use():
    """Test parsing both :require and :use forms."""
    source = """(ns example.foo
      (:require [example.bar :as bar])
      (:use [example.qux]))"""

    result = parse_clojure_requires(source)
    assert result == {"example.bar", "example.qux"}


def test_parse_clojure_use_project_c_example():
    """Test parsing the real project-c example with :use."""
    source = """(ns example.project-c.core
      (:use [example.project-a.core]))"""

    result = parse_clojure_requires(source)
    assert result == {"example.project-a.core"}


def test_parse_clojure_mixed_require_and_use():
    """Test parsing mixed :require and :use with multiple namespaces."""
    source = """(ns example.foo
      (:require [example.bar :as bar]
                [example.baz :refer [func1 func2]])
      (:use [example.qux]
            [example.quux]))"""

    result = parse_clojure_requires(source)
    assert result == {"example.bar", "example.baz", "example.qux", "example.quux"}


def test_parse_clojure_requires_filters_non_namespaces():
    """Test that single-word requires (like 'clojure') are filtered out."""
    source = """(ns example.foo
      (:require [example.bar]))"""

    result = parse_clojure_requires(source)
    # Only namespaces with dots should be included
    assert result == {"example.bar"}


def test_namespace_to_path():
    """Test converting namespace to file path."""
    assert namespace_to_path("example.project-a.core") == "example/project_a/core.clj"
    assert namespace_to_path("foo.bar-baz.qux") == "foo/bar_baz/qux.clj"


def test_path_to_namespace():
    """Test converting file path to namespace."""
    assert path_to_namespace("example/project_a/core.clj") == "example.project-a.core"
    assert path_to_namespace("foo/bar_baz/qux.clj") == "foo.bar-baz.qux"
    assert path_to_namespace("example/core.cljc") == "example.core"


def test_namespace_path_roundtrip():
    """Test that namespace <-> path conversion is reversible."""
    namespace = "example.project-a.core-utils"
    path = namespace_to_path(namespace)
    assert path_to_namespace(path) == namespace
