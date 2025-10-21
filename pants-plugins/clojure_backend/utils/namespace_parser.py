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

Falls back to regex-based parsing if clj-kondo is not available.
"""

from __future__ import annotations

import re

from clojure_backend.utils.clj_kondo_parser import (
    parse_namespace_with_kondo,
    parse_requires_with_kondo,
    parse_imports_with_kondo,
)

# Namespace declaration pattern
# Pattern explanation:
#   \(ns      - Literal "(ns" to match namespace declaration
#   \s+       - One or more whitespace characters
#   ([\w\.\-]+) - Capture group: word chars, dots, and hyphens (namespace name)
# Note: This simple pattern may match (ns ...) in comments or strings
NS_PATTERN = re.compile(r'\(ns\s+([\w\.\-]+)', re.MULTILINE)


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

    Falls back to regex parsing if clj-kondo is not available.
    """
    # Try clj-kondo first for accurate parsing
    result = parse_namespace_with_kondo(source_content)
    if result is not None:
        return result

    # Fallback to regex if clj-kondo is not available or fails
    match = NS_PATTERN.search(source_content)
    return match.group(1) if match else None


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

    Falls back to regex parsing if clj-kondo is not available.
    """
    # Try clj-kondo first for accurate parsing
    result = parse_requires_with_kondo(source_content)
    if result:
        return result

    # Fallback to regex if clj-kondo is not available or fails
    required_namespaces = set()

    # Find the ns form - it starts with (ns and ends with a matching paren
    ns_match = re.search(r'\(ns\s+[\w\.\-]+\s*(.*?)(?=\n\(|\Z)', source_content, re.DOTALL)
    if not ns_match:
        return required_namespaces

    ns_body = ns_match.group(1)

    # Find :require and :use sections
    for directive in [':require', ':use']:
        directive_match = re.search(rf'\({directive}\s+(.*?)(?=\(:|$)', ns_body, re.DOTALL)
        if not directive_match:
            continue

        directive_body = directive_match.group(1)

        # Extract namespaces
        for match in re.finditer(r'\[([a-zA-Z][\w\.\-]*)', directive_body):
            namespace = match.group(1)
            # Only include if it looks like a namespace (has a dot)
            if '.' in namespace:
                required_namespaces.add(namespace)

    return required_namespaces


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

    Falls back to regex parsing if clj-kondo is not available.
    """
    # Try clj-kondo first for accurate parsing
    result = parse_imports_with_kondo(source_content)
    if result:
        return result

    # Fallback to regex if clj-kondo is not available or fails
    imported_classes = set()

    # Find the ns form
    ns_match = re.search(r'\(ns\s+[\w\.\-]+\s*(.*?)(?=\n\(|\Z)', source_content, re.DOTALL)
    if not ns_match:
        return imported_classes

    ns_body = ns_match.group(1)

    # Find :import section
    import_match = re.search(r'\(:import\s+(.*?)(?=\(:|$)', ns_body, re.DOTALL)
    if not import_match:
        return imported_classes

    import_body = import_match.group(1)

    # Handle vector syntax: [java.util Date ArrayList]
    for match in re.finditer(r'\[([a-zA-Z][\w\.]*)\s+([^\]]+)\]', import_body):
        package = match.group(1)
        classes_str = match.group(2)
        class_names = classes_str.split()
        for class_name in class_names:
            if re.match(r'^[A-Z][\w\$]*$', class_name):
                imported_classes.add(f"{package}.{class_name}")

    # Handle single-class syntax: java.util.Date
    for match in re.finditer(r'\b([a-z][\w]*(?:\.[a-z][\w]*)+\.[A-Z][\w\$]*)\b', import_body):
        class_name = match.group(1)
        imported_classes.add(class_name)

    return imported_classes


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
