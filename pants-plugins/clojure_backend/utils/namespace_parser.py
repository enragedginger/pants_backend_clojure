"""Utilities for parsing Clojure namespace declarations.

This module provides functions for parsing Clojure source files to extract
namespace declarations, required namespaces, and Java imports. It also
provides conversion functions between namespace names and file paths.

Note: The parsing is currently implemented using regex patterns. While this
works for most common cases, it has some limitations with edge cases like:
- Multi-line strings containing namespace-like patterns
- Comments with namespace declarations
- Reader conditionals (#?(:clj ...))
- Complex nested forms

For production use with complex codebases, consider using clojure.tools.reader
or a proper s-expression parser.
"""

from __future__ import annotations

import re

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

    Limitations:
        This function uses regex patterns for parsing, which works well for
        standard cases but has known limitations:

        - May match (ns ...) patterns in comments or strings before the real namespace
        - Does not strip comments before parsing (e.g., ;; (ns fake) will match)
        - Does not handle reader conditionals (#?(:clj ...)) specially
        - Does not validate s-expression structure
        - Assumes namespace follows standard naming (letters, numbers, dots, hyphens)
        - May not handle metadata (^:deprecated) correctly

        Known edge cases that may cause issues:
        - Multi-line docstrings containing "(ns fake.namespace)"
        - Commented-out namespace declarations
        - Complex reader conditional forms

        For production use with complex codebases requiring perfect accuracy,
        consider using clojure.tools.reader or a proper s-expression parser.

        See tests/test_namespace_parser_edge_cases.py for documented limitations.
    """
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

    Limitations:
        This function uses regex patterns for parsing :require and :use forms.

        Known limitations:
        - Only finds namespaces that contain at least one dot (filters single-word requires)
        - May miss prefix list notation: (:require [example [bar] [baz]])
        - Reader conditionals (#?(:clj ...)) may confuse the parser
        - Does not handle all complex multi-line forms correctly
        - Does not strip comments or strings before parsing
        - Assumes standard formatting with square brackets

        Unsupported edge cases:
        - Prefix list notation for grouped requires
        - Some complex reader conditional patterns
        - Requires within macros or nested forms

        For most typical Clojure code, this parser works correctly.
        See tests/test_namespace_parser_edge_cases.py for documented limitations.
    """
    required_namespaces = set()

    # Find the ns form - it starts with (ns and ends with a matching paren
    # Pattern explanation:
    #   \(ns      - Literal "(ns"
    #   \s+       - One or more whitespace
    #   [\w\.\-]+ - Namespace name (word chars, dots, hyphens)
    #   \s*       - Optional whitespace
    #   (.*?)     - Non-greedy capture of ns body
    #   (?=\n\(|\Z) - Lookahead for newline+paren or end of string
    ns_match = re.search(r'\(ns\s+[\w\.\-]+\s*(.*?)(?=\n\(|\Z)', source_content, re.DOTALL)
    if not ns_match:
        return required_namespaces

    ns_body = ns_match.group(1)

    # Find :require and :use sections - look for (:require ...) and (:use ...)
    # Pattern explanation:
    #   \({directive}  - Literal "(:require" or "(:use"
    #   \s+            - One or more whitespace
    #   (.*?)          - Non-greedy capture of directive body
    #   (?=\(:|$)      - Lookahead for next directive "(:..." or end
    for directive in [':require', ':use']:
        directive_match = re.search(rf'\({directive}\s+(.*?)(?=\(:|$)', ns_body, re.DOTALL)
        if not directive_match:
            continue

        directive_body = directive_match.group(1)

        # Extract namespaces - they appear at the start of [namespace ...] forms
        # Match patterns like [example.foo ...] or [example.bar]
        # Pattern explanation:
        #   \[            - Literal opening bracket
        #   ([a-zA-Z][\w\.\-]*) - Capture: starts with letter, then word chars/dots/hyphens
        for match in re.finditer(r'\[([a-zA-Z][\w\.\-]*)', directive_body):
            namespace = match.group(1)
            # Only include if it looks like a namespace (has a dot)
            # This filters out single-word requires like [clojure] which are rare
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

    Limitations:
        This function uses regex patterns for parsing :import forms.

        Known limitations:
        - Does not validate that class names are valid Java identifiers
        - Reader conditionals (#?(:clj ...)) may confuse the parser
        - Does not handle all whitespace variations perfectly
        - Does not strip comments before parsing
        - Assumes class names start with uppercase letters

        Supported features:
        - Vector syntax with package and class list
        - Single fully-qualified class names
        - Inner classes (Map$Entry)
        - Deeply nested packages (java.util.concurrent.atomic.*)
        - Mixed syntax in same :import form

        For typical Clojure/Java interop code, this parser works correctly.
        See tests/test_namespace_parser_edge_cases.py for edge case behavior.
    """
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
    # Match [package Class1 Class2 ...]
    for match in re.finditer(r'\[([a-zA-Z][\w\.]*)\s+([^\]]+)\]', import_body):
        package = match.group(1)
        classes_str = match.group(2)
        # Split on whitespace to get individual class names
        class_names = classes_str.split()
        for class_name in class_names:
            # Only include valid class names (start with letter, no special chars except _)
            if re.match(r'^[A-Z][\w\$]*$', class_name):
                imported_classes.add(f"{package}.{class_name}")

    # Handle single-class syntax: java.util.Date
    # Match fully-qualified class names (package.Class)
    # Must have at least one dot and end with uppercase letter (class name)
    for match in re.finditer(r'\b([a-z][\w]*(?:\.[a-z][\w]*)+\.[A-Z][\w\$]*)\b', import_body):
        class_name = match.group(1)
        # Avoid matching things inside vector forms (already handled above)
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
