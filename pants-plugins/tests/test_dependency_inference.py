"""Tests for Clojure namespace utility functions."""

import pytest

from pants_backend_clojure.utils.namespace_parser import (
    namespace_to_path,
    path_to_namespace,
    class_to_path,
    is_jdk_class,
)


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
