"""Utilities for parsing Clojure namespace declarations.

This module provides functions for parsing Clojure source files to extract
namespace declarations, required namespaces, and Java imports. It also
provides conversion functions between namespace names and file paths.

The parsing is implemented using clj-kondo's static analysis capabilities,
which properly handles all edge cases including:
- Multi-line strings containing namespace-like patterns
- Comments with namespace declarations
- Reader conditionals (#?(:clj ...))
- Complex nested forms
- Prefix list notation
- Metadata on namespace forms
"""

from __future__ import annotations

from clojure_backend.utils.clj_kondo_parser import (
    parse_namespace_with_kondo,
    parse_requires_with_kondo,
    parse_imports_with_kondo,
)


def parse_namespace(source_content: str) -> str | None:
    """Extract the namespace from a Clojure source file.

    Args:
        source_content: The content of the Clojure source file.

    Returns:
        The namespace name if found, None otherwise.

    Example:
        (ns example.project-a.core) -> "example.project-a.core"

    This function uses clj-kondo's static analysis for accurate parsing,
    handling all edge cases including:
    - Multi-line strings containing "(ns fake.namespace)"
    - Comments with namespace declarations
    - Reader conditionals (#?(:clj ...))
    - Metadata (^:deprecated)
    - Complex nested forms
    """
    return parse_namespace_with_kondo(source_content)


def parse_requires(source_content: str) -> set[str]:
    """Extract required namespaces from :require and :use forms.

    Args:
        source_content: The content of the Clojure source file.

    Returns:
        A set of required namespace names.

    Examples:
        (ns example.foo
          (:require [example.bar :as bar]
                    [example.baz])
          (:use [example.qux]))

        Returns: {"example.bar", "example.baz", "example.qux"}

    This function uses clj-kondo's static analysis for accurate parsing,
    handling all edge cases including:
    - Prefix list notation: (:require [example [bar] [baz]])
    - Reader conditionals (#?(:clj ...))
    - Complex multi-line forms
    - Comments within require forms
    - All :refer, :as, :refer-macros options
    """
    return parse_requires_with_kondo(source_content)


def parse_imports(source_content: str) -> set[str]:
    """Extract Java class imports from :import forms.

    Handles both vector and single-class import syntax.

    Args:
        source_content: The content of the Clojure source file.

    Returns:
        A set of fully-qualified Java class names.

    Examples:
        Vector syntax:
            (ns example.foo
              (:import [java.util Date ArrayList]
                       [java.io File]))
            Returns: {"java.util.Date", "java.util.ArrayList", "java.io.File"}

        Single-class syntax:
            (ns example.bar
              (:import java.util.Date
                       java.io.File))
            Returns: {"java.util.Date", "java.io.File"}

    This function uses clj-kondo's static analysis for accurate parsing,
    handling all edge cases including:
    - Vector and single-class syntax
    - Inner classes (Map$Entry)
    - Reader conditionals
    - Comments within import forms
    - All whitespace variations
    """
    return parse_imports_with_kondo(source_content)


def namespace_to_path(namespace: str) -> str:
    """Convert a Clojure namespace to its expected file path.

    Args:
        namespace: The Clojure namespace name.

    Returns:
        The expected file path for the namespace.

    Example:
        "example.project-a.core" -> "example/project_a/core.clj"

    Note:
        Clojure uses underscores in file paths for hyphens in namespaces.
    """
    path = namespace.replace('.', '/').replace('-', '_')
    return f"{path}.clj"


def path_to_namespace(file_path: str) -> str:
    """Convert a file path to a Clojure namespace.

    Args:
        file_path: The file path (relative or absolute).

    Returns:
        The expected namespace name for the file.

    Example:
        "example/project_a/core.clj" -> "example.project-a.core"

    Note:
        Clojure uses hyphens in namespaces for underscores in file paths.
    """
    # Remove .clj or .cljc extension
    path = file_path
    if path.endswith('.clj'):
        path = path[:-4]
    elif path.endswith('.cljc'):
        path = path[:-5]

    # Convert path separators to dots and underscores to hyphens
    namespace = path.replace('/', '.').replace('_', '-')
    return namespace


def class_to_path(class_name: str) -> str:
    """Convert a Java class name to its expected file path.

    Args:
        class_name: The fully-qualified Java class name.

    Returns:
        The expected file path for the class.

    Examples:
        "com.example.Foo" -> "com/example/Foo.java"
        "java.util.HashMap" -> "java/util/HashMap.java"
        "java.util.Map$Entry" -> "java/util/Map.java" (inner class)

    Note:
        Inner classes (containing $) are mapped to their outer class file.
    """
    # Handle inner classes by taking only the outer class
    if '$' in class_name:
        class_name = class_name.split('$')[0]

    path = class_name.replace('.', '/')
    return f"{path}.java"


def is_jdk_class(class_name: str) -> bool:
    """Check if a class is part of the JDK (implicit dependency).

    Args:
        class_name: The fully-qualified Java class name.

    Returns:
        True if the class is part of the JDK, False otherwise.

    JDK packages include:
        - java.* (java.lang, java.util, java.io, etc.)
        - javax.* (javax.swing, javax.sql, etc.)
        - sun.* (internal, discouraged but sometimes used)
        - jdk.* (JDK 9+ modules)
    """
    from clojure_backend.config import JDK_PACKAGE_PREFIXES

    return any(class_name.startswith(prefix) for prefix in JDK_PACKAGE_PREFIXES)
