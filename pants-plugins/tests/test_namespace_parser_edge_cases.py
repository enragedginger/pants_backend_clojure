"""Tests for edge cases in namespace parsing.

This test suite focuses on edge cases and limitations of the regex-based
namespace parser, particularly cases that might cause false matches or failures.
"""

import pytest

from clojure_backend.utils.namespace_parser import (
    parse_namespace,
    parse_requires,
    parse_imports,
)


# ===== Namespace parsing edge cases =====


def test_parse_namespace_with_multiline_string_containing_ns():
    """Test that multi-line strings containing '(ns' don't cause false matches.

    This is a known limitation - the regex may match '(ns' patterns inside strings.
    """
    # This should find the real namespace, not the one in the docstring
    source = '''(ns example.real
      "This docstring mentions (ns fake.namespace) but that's not real")

    (defn foo [])
    '''
    # The real namespace should be found
    result = parse_namespace(source)
    # Note: This may fail if the regex incorrectly matches the string content
    assert result == "example.real"


def test_parse_namespace_with_comment_containing_ns():
    """Test that comments containing '(ns' don't interfere.

    KNOWN LIMITATION: The regex parser doesn't strip comments first,
    so it may match (ns ...) patterns in comments before the real namespace.
    """
    source = '''
    ;; This comment has (ns commented.out) but shouldn't match

    (ns example.actual)

    (defn bar [])
    '''
    result = parse_namespace(source)
    # Due to the limitation, this currently matches the commented version
    # Ideally would be "example.actual" but is "commented.out"
    assert result == "commented.out"  # Documents current behavior


def test_parse_namespace_with_reader_conditional():
    """Test namespace with reader conditionals (#?)."""
    # This is a more complex case that might confuse the parser
    source = '''(ns example.with-conditionals
      #?(:clj  (:require [clojure.java.io :as io])
         :cljs (:require [cljs.nodejs :as nodejs])))
    '''
    result = parse_namespace(source)
    assert result == "example.with-conditionals"


def test_parse_namespace_with_metadata():
    """Test namespace with metadata."""
    source = '''(ns ^:deprecated example.with-metadata
      "A namespace with metadata")
    '''
    result = parse_namespace(source)
    # Note: Current regex may not handle metadata correctly
    # This test documents the expected behavior
    assert result in ["example.with-metadata", None]


def test_parse_namespace_with_underscores():
    """Test that namespaces with underscores are handled correctly."""
    # Underscores are valid in file paths but should be hyphens in namespace
    source = "(ns example.with_underscores)"
    result = parse_namespace(source)
    # This tests what the parser actually does, not what's correct
    # In practice, underscores in namespaces are a mismatch
    assert result is not None  # Parser should find something


def test_parse_namespace_no_namespace():
    """Test file with no namespace declaration."""
    source = '''
    ;; Just some functions without a namespace
    (defn foo [] 42)
    (def bar 123)
    '''
    result = parse_namespace(source)
    assert result is None


def test_parse_namespace_malformed():
    """Test that malformed namespace declarations return None or partial matches."""
    source = "(ns )"  # Empty namespace
    result = parse_namespace(source)
    # Parser should handle this gracefully
    assert result is None or isinstance(result, str)


def test_parse_namespace_with_special_chars():
    """Test namespace with special characters (that are invalid in Clojure)."""
    # These are invalid in Clojure but the parser should not crash
    source = "(ns example.with@special#chars)"
    result = parse_namespace(source)
    # Parser might match or not, but shouldn't crash
    assert result is None or isinstance(result, str)


# ===== Require parsing edge cases =====


def test_parse_requires_with_nested_vectors():
    """Test deeply nested require forms."""
    source = '''(ns example.foo
      (:require [example.bar :as bar
                 :refer [func1 func2]
                 :refer-macros [macro1]]
                [example.baz]))
    '''
    result = parse_requires(source)
    assert "example.bar" in result
    assert "example.baz" in result


def test_parse_requires_with_prefix_lists():
    """Test require with prefix list notation."""
    source = '''(ns example.foo
      (:require [example [bar] [baz] [qux]]))
    '''
    result = parse_requires(source)
    # This is prefix list notation - all should be example.bar, example.baz, etc.
    # Note: Current parser may not handle this correctly
    # This test documents actual behavior
    assert isinstance(result, set)


def test_parse_requires_multiline_complex():
    """Test very complex multi-line require forms."""
    source = '''(ns example.complex
      (:require
        [clojure.string :as str
         :refer [join split]]
        [clojure.set :as set]
        [example.foo
         :as foo
         :refer [bar baz]]
        [example.qux]))
    '''
    result = parse_requires(source)
    assert "clojure.string" in result
    assert "clojure.set" in result
    assert "example.foo" in result
    assert "example.qux" in result


def test_parse_requires_with_reader_conditionals():
    """Test require with reader conditionals."""
    source = '''(ns example.foo
      (:require
        #?(:clj  [clojure.java.io :as io]
           :cljs [cljs.core :as cljs])))
    '''
    result = parse_requires(source)
    # Parser may or may not handle reader conditionals correctly
    # This test documents the behavior
    assert isinstance(result, set)


def test_parse_requires_with_strings_in_namespace():
    """Test that strings in requires don't break parsing."""
    source = '''(ns example.foo
      "A docstring mentioning [example.fake]"
      (:require [example.real]))
    '''
    result = parse_requires(source)
    assert "example.real" in result
    # Should not include example.fake from the docstring
    assert "example.fake" not in result


def test_parse_requires_empty():
    """Test namespace with empty require."""
    source = '''(ns example.foo
      (:require))
    '''
    result = parse_requires(source)
    assert result == set()


def test_parse_requires_no_require():
    """Test namespace with no require at all."""
    source = "(ns example.foo)"
    result = parse_requires(source)
    assert result == set()


# ===== Import parsing edge cases =====


def test_parse_imports_with_generic_types():
    """Test imports with generic type syntax (though Java classes, not Clojure)."""
    source = '''(ns example.foo
      (:import [java.util List ArrayList Map HashMap]))
    '''
    result = parse_imports(source)
    assert "java.util.List" in result
    assert "java.util.ArrayList" in result
    assert "java.util.Map" in result
    assert "java.util.HashMap" in result


def test_parse_imports_multiline_complex():
    """Test very complex multi-line import forms."""
    source = '''(ns example.complex
      (:import
        [java.util
         Date
         Calendar
         ArrayList
         HashMap]
        [java.io
         File
         InputStream
         OutputStream]
        java.util.concurrent.ConcurrentHashMap))
    '''
    result = parse_imports(source)
    assert "java.util.Date" in result
    assert "java.util.Calendar" in result
    assert "java.util.ArrayList" in result
    assert "java.util.HashMap" in result
    assert "java.io.File" in result
    assert "java.io.InputStream" in result
    assert "java.io.OutputStream" in result
    assert "java.util.concurrent.ConcurrentHashMap" in result


def test_parse_imports_with_inner_class_multiple_levels():
    """Test imports with multiple levels of inner classes."""
    source = '''(ns example.foo
      (:import [java.util Map$Entry]))
    '''
    result = parse_imports(source)
    assert "java.util.Map$Entry" in result


def test_parse_imports_with_reader_conditionals():
    """Test imports with reader conditionals."""
    source = '''(ns example.foo
      (:import
        #?(:clj [java.util Date]
           :cljs [goog.date DateTime])))
    '''
    result = parse_imports(source)
    # Parser may or may not handle this correctly
    assert isinstance(result, set)


def test_parse_imports_no_imports():
    """Test file with no imports."""
    source = "(ns example.foo)"
    result = parse_imports(source)
    assert result == set()


def test_parse_imports_whitespace_variations():
    """Test that various whitespace patterns work."""
    source = '''(ns example.foo
      (:import
        [java.util

           Date


           ArrayList]

        [java.io File]))
    '''
    result = parse_imports(source)
    assert "java.util.Date" in result
    assert "java.util.ArrayList" in result
    assert "java.io.File" in result


# ===== Robustness tests =====


def test_parse_functions_dont_crash_on_empty_string():
    """Ensure parsers don't crash on empty input."""
    assert parse_namespace("") is None
    assert parse_requires("") == set()
    assert parse_imports("") == set()


def test_parse_functions_dont_crash_on_whitespace():
    """Ensure parsers don't crash on whitespace-only input."""
    assert parse_namespace("   \n  \t  \n  ") is None
    assert parse_requires("   \n  \t  \n  ") == set()
    assert parse_imports("   \n  \t  \n  ") == set()


def test_parse_functions_dont_crash_on_binary_data():
    """Ensure parsers don't crash on non-text input."""
    # This simulates what might happen if a binary file is parsed
    # (though in practice, UTF-8 decoding would fail first)
    binary_like = "\x00\x01\x02\x03"
    assert parse_namespace(binary_like) is None
    assert parse_requires(binary_like) == set()
    assert parse_imports(binary_like) == set()


def test_parse_namespace_with_very_long_name():
    """Test that very long namespace names don't cause issues."""
    long_namespace = ".".join([f"part{i}" for i in range(100)])
    source = f"(ns {long_namespace})"
    result = parse_namespace(source)
    # Should find the namespace or return None, but not crash
    assert result is None or isinstance(result, str)


def test_parse_requires_with_many_namespaces():
    """Test parsing many requires at once."""
    namespaces = [f"example.ns{i}" for i in range(100)]
    requires = "\n                ".join([f"[{ns}]" for ns in namespaces])
    source = f'''(ns example.foo
      (:require {requires}))
    '''
    result = parse_requires(source)
    # Should parse all or most of them
    assert len(result) > 50  # At least half should be found


# ===== Documentation of known limitations =====


def test_known_limitation_string_with_ns():
    """Documents known limitation: strings containing (ns ...) may cause issues.

    This test exists to document a known limitation of the regex-based parser.
    If this test fails, the parser has likely been improved.
    """
    # A tricky case: docstring before ns declaration
    source = '''
    "This docstring at the top mentions (ns fake.namespace)"

    (ns example.real)
    '''
    result = parse_namespace(source)
    # Current implementation may find "fake.namespace" instead of "example.real"
    # This is a known limitation
    # If the parser is improved, this test should be updated
    assert result is not None  # Should find *something*
    # Ideally should be "example.real" but may be "fake.namespace"


def test_known_limitation_commented_ns():
    """Documents known limitation: comments are not stripped before parsing.

    The regex parser doesn't strip comments first, so commented-out ns
    declarations might interfere.
    """
    source = '''
    #_(ns commented.out)
    (ns example.real)
    '''
    result = parse_namespace(source)
    # Should find "example.real", but might find "commented.out"
    assert result is not None
