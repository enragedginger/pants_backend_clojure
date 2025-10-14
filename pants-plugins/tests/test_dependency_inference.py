"""Tests for Clojure dependency inference."""

import pytest

from clojure_backend.dependency_inference import (
    parse_clojure_namespace,
    parse_clojure_requires,
    parse_clojure_imports,
    namespace_to_path,
    path_to_namespace,
    class_to_path,
    is_jdk_class,
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


# ===== Import parsing tests =====


def test_parse_clojure_imports_vector_syntax():
    """Test parsing vector syntax :import forms."""
    source = "(ns foo (:import [java.util Date ArrayList]))"
    assert parse_clojure_imports(source) == {
        "java.util.Date",
        "java.util.ArrayList"
    }


def test_parse_clojure_imports_multiple_packages():
    """Test parsing imports from multiple packages."""
    source = """(ns foo
      (:import [java.util Date ArrayList HashMap]
               [java.io File InputStream OutputStream]))"""
    assert parse_clojure_imports(source) == {
        "java.util.Date",
        "java.util.ArrayList",
        "java.util.HashMap",
        "java.io.File",
        "java.io.InputStream",
        "java.io.OutputStream"
    }


def test_parse_clojure_imports_single_class_syntax():
    """Test parsing single fully-qualified class syntax."""
    source = """(ns foo
      (:import java.util.Date
               java.io.File))"""
    assert parse_clojure_imports(source) == {
        "java.util.Date",
        "java.io.File"
    }


def test_parse_clojure_imports_mixed_syntax():
    """Test parsing mixed vector and single-class syntax."""
    source = """(ns foo
      (:import java.util.Date
               [java.io File Reader Writer]))"""
    result = parse_clojure_imports(source)
    assert "java.util.Date" in result
    assert "java.io.File" in result
    assert "java.io.Reader" in result
    assert "java.io.Writer" in result


def test_parse_clojure_imports_nested_packages():
    """Test parsing imports with deeply nested packages."""
    source = """(ns foo
      (:import [java.util.concurrent.atomic AtomicInteger AtomicLong]))"""
    assert parse_clojure_imports(source) == {
        "java.util.concurrent.atomic.AtomicInteger",
        "java.util.concurrent.atomic.AtomicLong"
    }


def test_parse_clojure_imports_inner_classes():
    """Test parsing imports with inner classes (containing $)."""
    source = """(ns foo
      (:import [java.util Map$Entry]))"""
    assert parse_clojure_imports(source) == {"java.util.Map$Entry"}


def test_parse_clojure_imports_custom_classes():
    """Test parsing imports of custom (non-JDK) classes."""
    source = """(ns foo
      (:import [com.fasterxml.jackson.databind ObjectMapper JsonNode]
               [com.example.custom MyClass]))"""
    assert parse_clojure_imports(source) == {
        "com.fasterxml.jackson.databind.ObjectMapper",
        "com.fasterxml.jackson.databind.JsonNode",
        "com.example.custom.MyClass"
    }


def test_parse_clojure_imports_with_require():
    """Test parsing :import alongside :require."""
    source = """(ns foo
      (:require [clojure.string :as str])
      (:import [java.util Date]))"""
    # Only imports, not requires
    assert parse_clojure_imports(source) == {"java.util.Date"}


def test_parse_clojure_imports_no_imports():
    """Test parsing when there are no :import forms."""
    source = """(ns foo
      (:require [clojure.string :as str]))"""
    assert parse_clojure_imports(source) == set()


def test_parse_clojure_imports_empty_import():
    """Test parsing empty :import form."""
    source = "(ns foo (:import))"
    assert parse_clojure_imports(source) == set()


def test_parse_clojure_imports_real_example():
    """Test parsing a realistic example with multiple import styles."""
    source = """(ns example.json-processor
      (:require [clojure.string :as str])
      (:import [com.fasterxml.jackson.databind ObjectMapper JsonNode]
               [java.io File InputStream]
               java.util.Date))"""
    result = parse_clojure_imports(source)
    assert "com.fasterxml.jackson.databind.ObjectMapper" in result
    assert "com.fasterxml.jackson.databind.JsonNode" in result
    assert "java.io.File" in result
    assert "java.io.InputStream" in result
    assert "java.util.Date" in result


# ===== class_to_path tests =====


def test_class_to_path_simple():
    """Test converting simple class names to paths."""
    assert class_to_path("com.example.Foo") == "com/example/Foo.java"
    assert class_to_path("java.util.HashMap") == "java/util/HashMap.java"


def test_class_to_path_nested_packages():
    """Test converting classes in deeply nested packages."""
    assert class_to_path("com.fasterxml.jackson.databind.ObjectMapper") == \
        "com/fasterxml/jackson/databind/ObjectMapper.java"


def test_class_to_path_inner_class():
    """Test converting inner class names (strips after $)."""
    assert class_to_path("java.util.Map$Entry") == "java/util/Map.java"
    assert class_to_path("com.example.Outer$Inner$Nested") == "com/example/Outer.java"


# ===== is_jdk_class tests =====


def test_is_jdk_class_java_package():
    """Test identifying java.* classes as JDK."""
    assert is_jdk_class("java.util.Date") is True
    assert is_jdk_class("java.io.File") is True
    assert is_jdk_class("java.lang.String") is True


def test_is_jdk_class_javax_package():
    """Test identifying javax.* classes as JDK."""
    assert is_jdk_class("javax.swing.JFrame") is True
    assert is_jdk_class("javax.sql.DataSource") is True


def test_is_jdk_class_sun_package():
    """Test identifying sun.* classes as JDK (internal)."""
    assert is_jdk_class("sun.misc.Unsafe") is True


def test_is_jdk_class_jdk_package():
    """Test identifying jdk.* classes as JDK (JDK 9+ modules)."""
    assert is_jdk_class("jdk.internal.misc.Unsafe") is True


def test_is_jdk_class_non_jdk():
    """Test that non-JDK classes are not identified as JDK."""
    assert is_jdk_class("com.example.Foo") is False
    assert is_jdk_class("com.fasterxml.jackson.databind.ObjectMapper") is False
    assert is_jdk_class("org.apache.commons.lang3.StringUtils") is False
    assert is_jdk_class("clojure.lang.IFn") is False
