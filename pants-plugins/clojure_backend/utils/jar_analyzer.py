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

from clojure_backend.utils.namespace_parser import parse_namespace


@dataclass(frozen=True)
class JarNamespaceAnalysis:
    """Result of analyzing a JAR for Clojure namespaces.

    Attributes:
        namespaces: Tuple of Clojure namespace names found in the JAR.
    """
    namespaces: tuple[str, ...]


def namespace_from_class_path(class_path: str) -> str | None:
    """Infer Clojure namespace from .class file path.

    Some Clojure JARs are AOT-compiled and only contain .class files.
    This function attempts to infer the namespace from class file paths.

    Args:
        class_path: Path to a .class file within the JAR.

    Returns:
        The inferred namespace name, or None if the class file is not
        a Clojure namespace class.

    Examples:
        "clojure/data/json.class" -> "clojure.data.json"
        "clojure/data/json__init.class" -> None (internal class)
        "clojure/data/json$fn.class" -> None (internal class)
        "com/example/Utils.class" -> "com.example.Utils"

    Note:
        Clojure generates several classes per namespace:
        - foo/bar.class - Main namespace class
        - foo/bar__init.class - Initialization class
        - foo/bar$fn_name.class - Function classes

        We only want the main namespace class (no __ or $ in the name).
    """
    if not class_path.endswith('.class'):
        return None

    # Remove .class extension
    path = class_path[:-6]

    # Ignore internal Clojure implementation classes
    # __init, $fn, etc. are not namespace declarations
    if '__' in path or '$' in path:
        return None

    # Convert path to namespace: foo/bar/baz -> foo.bar.baz
    # Note: We keep underscores as-is here since Java class names use underscores
    # where Clojure namespaces use hyphens. The actual namespace will be
    # determined from source if available.
    namespace = path.replace('/', '.')

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
                        ns = parse_namespace(content)
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
