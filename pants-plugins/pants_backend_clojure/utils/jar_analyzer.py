"""Utilities for analyzing JAR files to extract Clojure namespaces.

This module provides functions for inspecting JAR files to discover which
Clojure namespaces they provide. This is used during lock file generation
to build a mapping from namespaces to artifacts, enabling automatic
dependency inference for third-party Clojure libraries.

The analysis handles:
- Source JARs containing .clj, .cljc, .clje files
- AOT-compiled JARs containing only .class files
- Mixed JARs with both source and compiled code
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

import re


# Simple regex pattern to extract namespace from Clojure source files.
# This pattern handles the common case of (ns namespace-name ...) at the start.
# For JAR analysis (third-party dependencies), this is sufficient since most
# libraries use standard namespace declarations. Complex edge cases are rare
# in published JAR files.
_NS_PATTERN = re.compile(
    r'^\s*\(ns\s+([a-zA-Z][a-zA-Z0-9_.\-]*)',
    re.MULTILINE
)


def _parse_namespace_simple(source_content: str) -> str | None:
    """Parse namespace from Clojure source using simple regex.

    This is a lightweight parser for JAR analysis. It handles the common case
    where namespace declarations appear at the start of the file in standard
    format. It doesn't handle edge cases like:
    - Namespaces declared inside strings or comments
    - Reader conditionals
    - Complex metadata before namespace

    For third-party JAR files, this is sufficient since published libraries
    typically use standard namespace declarations.
    """
    match = _NS_PATTERN.search(source_content)
    if match:
        return match.group(1)
    return None


@dataclass(frozen=True)
class JarNamespaceAnalysis:
    """Result of analyzing a JAR for Clojure namespaces.

    Attributes:
        namespaces: Tuple of Clojure namespace names found in the JAR.
    """
    namespaces: tuple[str, ...]


def namespace_from_class_path(class_path: str) -> str | None:
    """Extract Clojure namespace from __init.class files.

    Clojure AOT compilation generates these classes per namespace:
    - my/app/core__init.class    <- Namespace loader (WE WANT THIS)
    - my/app/core$main.class     <- Named function
    - my/app/core$fn__1234.class <- Anonymous function

    The __init.class suffix definitively identifies a Clojure namespace.

    Args:
        class_path: Path to a .class file within the JAR.

    Returns:
        The inferred namespace name, or None if the class file is not
        a Clojure namespace __init class.

    Examples:
        "clojure/data/json__init.class" -> "clojure.data.json"
        "my_app/core__init.class" -> "my-app.core" (demunge heuristic)
        "clojure/data/json.class" -> None (not __init.class)
        "clojure/data/json$read_str.class" -> None (function class)

    LIMITATION: Both `my-app.core` and `my_app.core` compile to `my_app/core__init.class`.
    We use the demunge heuristic (underscore → hyphen) which works for idiomatic code,
    but may be wrong for namespaces that intentionally use underscores.
    Users can override via the `packages` field if needed.
    """
    if not class_path.endswith('__init.class'):
        return None

    # Remove __init.class suffix (12 characters)
    path = class_path[:-12]

    # Convert path to namespace: my/app/core -> my.app.core
    # Apply demunge heuristic: underscores -> hyphens (convention, not guaranteed)
    namespace = path.replace('/', '.').replace('_', '-')

    return namespace


def analyze_jar_for_namespaces(jar_path: Path) -> JarNamespaceAnalysis:
    """Extract Clojure namespaces from a JAR file.

    This function inspects a JAR file to discover which Clojure namespaces
    it provides. It handles both source JARs (containing .clj files) and
    AOT-compiled JARs (containing only .class files).

    Strategy:
    1. First, look for Clojure source files (.clj, .cljc, .clje)
    2. For each source file, parse the namespace declaration
    3. If no source files found, fall back to analyzing .class files
    4. Return deduplicated, sorted list of namespaces

    Args:
        jar_path: Path to the JAR file to analyze.

    Returns:
        JarNamespaceAnalysis containing the discovered namespaces.

    Examples:
        A source JAR containing:
            clojure/data/json.clj with (ns clojure.data.json)
        Returns:
            JarNamespaceAnalysis(namespaces=("clojure.data.json",))

        An AOT-compiled JAR containing:
            clojure/data/json.class
            clojure/data/json__init.class
        Returns:
            JarNamespaceAnalysis(namespaces=("clojure.data.json",))
    """
    namespaces = set()

    try:
        with zipfile.ZipFile(jar_path, 'r') as jar:
            # First pass: Look for Clojure source files
            source_files = [
                name for name in jar.namelist()
                if name.endswith(('.clj', '.cljc', '.clje')) and not name.startswith('META-INF/')
            ]

            if source_files:
                # We have source files - parse them for namespace declarations
                for entry in source_files:
                    try:
                        content = jar.read(entry).decode('utf-8', errors='ignore')
                        ns = _parse_namespace_simple(content)
                        if ns:
                            namespaces.add(ns)
                    except Exception:
                        # If we can't parse this file, skip it
                        # Common reasons: corrupt files, non-UTF8 encoding, etc.
                        pass
            else:
                # No source files - fall back to analyzing class files
                class_files = [
                    name for name in jar.namelist()
                    if name.endswith('.class') and not name.startswith('META-INF/')
                ]

                for entry in class_files:
                    ns = namespace_from_class_path(entry)
                    if ns:
                        namespaces.add(ns)

    except zipfile.BadZipFile:
        # Not a valid ZIP/JAR file - return empty result
        pass
    except Exception:
        # Unexpected error - return empty result rather than failing
        pass

    return JarNamespaceAnalysis(namespaces=tuple(sorted(namespaces)))


def is_clojure_jar(jar_path: Path) -> bool:
    """Check if a JAR file contains Clojure code.

    This is a quick check to determine if a JAR should be analyzed
    for Clojure namespaces. It looks for the presence of either:
    - Clojure source files (.clj, .cljc, .clje)
    - Clojure class files (based on common patterns)

    Args:
        jar_path: Path to the JAR file to check.

    Returns:
        True if the JAR appears to contain Clojure code, False otherwise.

    Note:
        This is a heuristic check for optimization purposes. It may return
        false positives (non-Clojure JARs that happen to have .clj files)
        or false negatives (Clojure JARs with unusual structure).
    """
    try:
        with zipfile.ZipFile(jar_path, 'r') as jar:
            for name in jar.namelist():
                # Check for Clojure source files
                if name.endswith(('.clj', '.cljc', '.clje')):
                    return True
                # Check for Clojure class files (common namespace prefixes)
                if name.endswith('.class') and not '__' in name and not '$' in name:
                    # This is a heuristic - any .class file could be Clojure
                    # Common Clojure library prefixes
                    if any(name.startswith(prefix) for prefix in [
                        'clojure/', 'cljs/', 'cljc/',  # Core Clojure namespaces
                        'medley/', 'ring/', 'compojure/',  # Common libraries
                    ]):
                        return True
    except Exception:
        pass

    return False
