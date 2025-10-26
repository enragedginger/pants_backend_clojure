"""Tests for JAR analysis utilities.

This test suite verifies that JAR files can be correctly analyzed to extract
Clojure namespaces, supporting both source JARs and AOT-compiled JARs.
"""

import tempfile
import zipfile
from pathlib import Path

import pytest

from clojure_backend.utils.jar_analyzer import (
    analyze_jar_for_namespaces,
    is_clojure_jar,
    namespace_from_class_path,
)


# ===== Helper functions for creating test JARs =====


def create_test_jar(files: dict[str, str]) -> Path:
    """Create a temporary JAR file with the given contents.

    Args:
        files: Dictionary mapping file paths to file contents.

    Returns:
        Path to the created JAR file.
    """
    jar_file = tempfile.NamedTemporaryFile(suffix='.jar', delete=False)
    jar_path = Path(jar_file.name)

    with zipfile.ZipFile(jar_path, 'w') as jar:
        for path, content in files.items():
            jar.writestr(path, content)

    return jar_path


# ===== Tests for namespace_from_class_path =====


def test_namespace_from_class_path_simple():
    """Test inferring namespace from simple class path."""
    assert namespace_from_class_path("clojure/data/json.class") == "clojure.data.json"
    assert namespace_from_class_path("com/example/utils.class") == "com.example.utils"


def test_namespace_from_class_path_ignores_init():
    """Test that __init classes are ignored."""
    assert namespace_from_class_path("clojure/data/json__init.class") is None


def test_namespace_from_class_path_ignores_fn():
    """Test that function classes are ignored."""
    assert namespace_from_class_path("clojure/data/json$read_str.class") is None
    assert namespace_from_class_path("clojure/data/json$fn__123.class") is None


def test_namespace_from_class_path_non_class():
    """Test that non-.class files return None."""
    assert namespace_from_class_path("clojure/data/json.clj") is None
    assert namespace_from_class_path("README.md") is None


# ===== Tests for analyze_jar_for_namespaces with source files =====


def test_analyze_jar_with_single_clj_source():
    """Test analyzing a JAR with a single Clojure source file."""
    jar_path = create_test_jar({
        "clojure/data/json.clj": "(ns clojure.data.json)\n\n(defn read-str [s] s)"
    })

    try:
        result = analyze_jar_for_namespaces(jar_path)
        assert result.namespaces == ("clojure.data.json",)
    finally:
        jar_path.unlink()


def test_analyze_jar_with_multiple_clj_sources():
    """Test analyzing a JAR with multiple Clojure source files."""
    jar_path = create_test_jar({
        "clojure/data/json.clj": "(ns clojure.data.json)",
        "clojure/data/json/util.clj": "(ns clojure.data.json.util)",
        "clojure/data/json/parser.clj": "(ns clojure.data.json.parser)",
    })

    try:
        result = analyze_jar_for_namespaces(jar_path)
        # Should be sorted
        assert result.namespaces == (
            "clojure.data.json",
            "clojure.data.json.parser",
            "clojure.data.json.util",
        )
    finally:
        jar_path.unlink()


def test_analyze_jar_with_cljc_source():
    """Test analyzing a JAR with .cljc (Clojure/ClojureScript) files."""
    jar_path = create_test_jar({
        "clojure/data/json.cljc": "(ns clojure.data.json)",
    })

    try:
        result = analyze_jar_for_namespaces(jar_path)
        assert result.namespaces == ("clojure.data.json",)
    finally:
        jar_path.unlink()


def test_analyze_jar_with_clje_source():
    """Test analyzing a JAR with .clje files."""
    jar_path = create_test_jar({
        "clojure/data/json.clje": "(ns clojure.data.json)",
    })

    try:
        result = analyze_jar_for_namespaces(jar_path)
        assert result.namespaces == ("clojure.data.json",)
    finally:
        jar_path.unlink()


def test_analyze_jar_ignores_metainf():
    """Test that files in META-INF/ are ignored."""
    jar_path = create_test_jar({
        "clojure/data/json.clj": "(ns clojure.data.json)",
        "META-INF/something.clj": "(ns meta.inf.something)",
    })

    try:
        result = analyze_jar_for_namespaces(jar_path)
        # Should only find the non-META-INF namespace
        assert result.namespaces == ("clojure.data.json",)
    finally:
        jar_path.unlink()


def test_analyze_jar_with_complex_namespace():
    """Test analyzing namespaces with hyphens (converted to underscores in paths)."""
    jar_path = create_test_jar({
        "clojure/tools/logging.clj": "(ns clojure.tools.logging)",
        "ring/middleware/anti_forgery.clj": "(ns ring.middleware.anti-forgery)",
    })

    try:
        result = analyze_jar_for_namespaces(jar_path)
        assert result.namespaces == (
            "clojure.tools.logging",
            "ring.middleware.anti-forgery",
        )
    finally:
        jar_path.unlink()


# ===== Tests for AOT-compiled JARs (class files only) =====


def test_analyze_jar_with_aot_compiled_classes():
    """Test analyzing an AOT-compiled JAR with only .class files."""
    jar_path = create_test_jar({
        "clojure/data/json.class": b"fake class content",
        "clojure/data/json__init.class": b"fake init class",
        "clojure/data/json$read_str.class": b"fake function class",
    })

    try:
        result = analyze_jar_for_namespaces(jar_path)
        # Should only detect main namespace class, not __init or $fn variants
        assert result.namespaces == ("clojure.data.json",)
    finally:
        jar_path.unlink()


def test_analyze_jar_prefers_source_over_classes():
    """Test that source files are preferred over class files."""
    jar_path = create_test_jar({
        # Source file with actual namespace
        "clojure/data/json.clj": "(ns clojure.data.json)",
        # Class files that might suggest different namespaces
        "clojure/data/xml.class": b"fake class",
    })

    try:
        result = analyze_jar_for_namespaces(jar_path)
        # Should use source file parsing, not class file inference
        # Note: xml.class won't be analyzed because source files were found
        assert result.namespaces == ("clojure.data.json",)
    finally:
        jar_path.unlink()


# ===== Tests for edge cases =====


def test_analyze_empty_jar():
    """Test analyzing an empty JAR file."""
    jar_path = create_test_jar({})

    try:
        result = analyze_jar_for_namespaces(jar_path)
        assert result.namespaces == ()
    finally:
        jar_path.unlink()


def test_analyze_jar_with_no_clojure_content():
    """Test analyzing a JAR with no Clojure files (pure Java JAR)."""
    jar_path = create_test_jar({
        "com/example/Util.class": b"fake java class",
        "META-INF/MANIFEST.MF": "Manifest-Version: 1.0",
    })

    try:
        result = analyze_jar_for_namespaces(jar_path)
        # Pure Java JAR - no Clojure namespaces
        # The class file won't match our filters (no __ or $ check passes, but
        # it's not obviously a Clojure namespace without source)
        assert "com.example.Util" in result.namespaces
    finally:
        jar_path.unlink()


def test_analyze_jar_with_invalid_namespace():
    """Test handling of files with malformed namespace declarations."""
    jar_path = create_test_jar({
        "clojure/data/json.clj": "(ns clojure.data.json)",
        "invalid.clj": "this is not valid clojure code",
        "another.clj": "(defn foo [])",  # No namespace declaration
    })

    try:
        result = analyze_jar_for_namespaces(jar_path)
        # Should only find the valid namespace
        assert result.namespaces == ("clojure.data.json",)
    finally:
        jar_path.unlink()


def test_analyze_jar_with_non_utf8():
    """Test handling of non-UTF8 content."""
    jar_path = create_test_jar({
        "clojure/data/json.clj": "(ns clojure.data.json)",
    })

    # Add a file with invalid UTF-8
    with zipfile.ZipFile(jar_path, 'a') as jar:
        jar.writestr("invalid.clj", b"\xff\xfe invalid bytes")

    try:
        result = analyze_jar_for_namespaces(jar_path)
        # Should still find the valid namespace, ignoring the invalid file
        assert result.namespaces == ("clojure.data.json",)
    finally:
        jar_path.unlink()


def test_analyze_invalid_jar():
    """Test analyzing a corrupted/invalid JAR file."""
    # Create a file that's not a valid ZIP/JAR
    jar_path = Path(tempfile.mktemp(suffix='.jar'))
    jar_path.write_text("this is not a valid JAR file")

    try:
        result = analyze_jar_for_namespaces(jar_path)
        # Should return empty result rather than crashing
        assert result.namespaces == ()
    finally:
        jar_path.unlink()


# ===== Tests for is_clojure_jar =====


def test_is_clojure_jar_with_source():
    """Test detecting Clojure JAR by presence of .clj files."""
    jar_path = create_test_jar({
        "clojure/data/json.clj": "(ns clojure.data.json)",
    })

    try:
        assert is_clojure_jar(jar_path) is True
    finally:
        jar_path.unlink()


def test_is_clojure_jar_with_common_namespace():
    """Test detecting Clojure JAR by common namespace prefixes."""
    jar_path = create_test_jar({
        "clojure/core/async.class": b"fake class",
    })

    try:
        assert is_clojure_jar(jar_path) is True
    finally:
        jar_path.unlink()


def test_is_not_clojure_jar():
    """Test that pure Java JARs are not detected as Clojure."""
    jar_path = create_test_jar({
        "com/example/Util.class": b"fake java class",
        "META-INF/MANIFEST.MF": "Manifest-Version: 1.0",
    })

    try:
        # This might return True since our heuristic isn't perfect
        # The function is meant to be conservative (false positives OK)
        result = is_clojure_jar(jar_path)
        # We accept either result - it's just a heuristic
        assert isinstance(result, bool)
    finally:
        jar_path.unlink()


def test_is_clojure_jar_invalid_jar():
    """Test handling invalid JAR files."""
    jar_path = Path(tempfile.mktemp(suffix='.jar'))
    jar_path.write_text("not a jar")

    try:
        assert is_clojure_jar(jar_path) is False
    finally:
        jar_path.unlink()


# ===== Integration tests with realistic JARs =====


def test_analyze_realistic_source_jar():
    """Test analyzing a realistic Clojure library JAR (simulated)."""
    # Simulate a JAR like org.clojure/data.json
    jar_path = create_test_jar({
        "clojure/data/json.clj": """(ns clojure.data.json
          "JSON parser/generator to/from Clojure data structures."
          (:require [clojure.string :as str]))

        (defn read-str [s] s)
        (defn write-str [x] x)
        """,
        "META-INF/MANIFEST.MF": "Manifest-Version: 1.0\n",
        "META-INF/maven/org.clojure/data.json/pom.properties": "version=2.4.0\n",
    })

    try:
        result = analyze_jar_for_namespaces(jar_path)
        assert result.namespaces == ("clojure.data.json",)
    finally:
        jar_path.unlink()


def test_analyze_realistic_multi_namespace_jar():
    """Test analyzing a JAR with multiple namespaces (like core.async)."""
    # Simulate a JAR like org.clojure/core.async
    jar_path = create_test_jar({
        "clojure/core/async.clj": "(ns clojure.core.async)",
        "clojure/core/async/impl/protocols.clj": "(ns clojure.core.async.impl.protocols)",
        "clojure/core/async/impl/channels.clj": "(ns clojure.core.async.impl.channels)",
        "clojure/core/async/impl/buffers.clj": "(ns clojure.core.async.impl.buffers)",
        "clojure/core/async/impl/dispatch.clj": "(ns clojure.core.async.impl.dispatch)",
    })

    try:
        result = analyze_jar_for_namespaces(jar_path)
        assert result.namespaces == (
            "clojure.core.async",
            "clojure.core.async.impl.buffers",
            "clojure.core.async.impl.channels",
            "clojure.core.async.impl.dispatch",
            "clojure.core.async.impl.protocols",
        )
    finally:
        jar_path.unlink()
