"""Clojure namespace parser using clj-kondo analysis output.

This module provides parsing functions for Clojure source files using clj-kondo's
static analysis capabilities. Unlike the regex-based approach, this properly handles:
- Multi-line strings and comments
- Reader conditionals (#?(:clj ...))
- Complex nested forms
- Prefix list notation
- Metadata on namespace forms
- All other edge cases that confuse regex parsers

clj-kondo is a battle-tested Clojure linter that uses a proper parser
(based on rewrite-clj and edamame) to analyze Clojure code without execution.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


def _run_clj_kondo_analysis(source_content: str, clj_kondo_path: str = "clj-kondo") -> dict:
    """Run clj-kondo analysis on source content and return parsed JSON.

    Args:
        source_content: The Clojure source code to analyze
        clj_kondo_path: Path to clj-kondo binary (default: "clj-kondo" from PATH)

    Returns:
        Dictionary containing clj-kondo's analysis output with keys:
        - "namespace-definitions": List of namespace declarations
        - "namespace-usages": List of required namespaces
        - "java-class-usages": List of imported Java classes

    Raises:
        FileNotFoundError: If clj-kondo is not found
        subprocess.CalledProcessError: If clj-kondo execution fails
        json.JSONDecodeError: If clj-kondo output is not valid JSON
    """
    # Write source to a temporary file
    # clj-kondo requires a file path, not stdin
    with tempfile.NamedTemporaryFile(mode='w', suffix='.clj', delete=False) as f:
        temp_file = Path(f.name)
        f.write(source_content)

    try:
        # Run clj-kondo with analysis output enabled
        # --lint: analyze the file
        # --config: enable analysis output with java-class-usages in JSON format
        result = subprocess.run(
            [
                clj_kondo_path,
                "--lint", str(temp_file),
                "--config", "{:output {:analysis {:java-class-usages true} :format :json}}",
            ],
            capture_output=True,
            text=True,
            check=False,  # Don't raise on non-zero exit (linting warnings are ok)
        )

        # clj-kondo writes analysis to stdout as JSON
        # Parse the JSON output
        if result.stdout:
            analysis = json.loads(result.stdout)
            return analysis.get("analysis", {})
        else:
            # No output means no analysis data (possibly empty file or error)
            return {}

    finally:
        # Clean up temp file
        temp_file.unlink(missing_ok=True)


def parse_namespace_with_kondo(source_content: str, clj_kondo_path: str = "clj-kondo") -> str | None:
    """Extract namespace using clj-kondo analysis.

    Args:
        source_content: The content of the Clojure source file
        clj_kondo_path: Path to clj-kondo binary (default: "clj-kondo" from PATH)

    Returns:
        The namespace name if found, None otherwise

    Example:
        >>> parse_namespace_with_kondo("(ns example.core)")
        'example.core'

    This function handles all edge cases that confuse regex parsers:
    - Multi-line strings containing "(ns fake.namespace)"
    - Comments with namespace declarations
    - Reader conditionals
    - Metadata on namespace forms
    - Complex nested forms
    """
    try:
        analysis = _run_clj_kondo_analysis(source_content, clj_kondo_path)
        ns_defs = analysis.get("namespace-definitions", [])

        # Return the first namespace definition found
        # In valid Clojure files, there should be exactly one namespace declaration
        if ns_defs:
            return ns_defs[0]["name"]
        return None

    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
        # If clj-kondo fails for any reason, return None
        # This matches the behavior of the regex parser
        return None


def parse_requires_with_kondo(source_content: str, clj_kondo_path: str = "clj-kondo") -> set[str]:
    """Extract required namespaces using clj-kondo analysis.

    Args:
        source_content: The content of the Clojure source file
        clj_kondo_path: Path to clj-kondo binary (default: "clj-kondo" from PATH)

    Returns:
        A set of required namespace names

    Example:
        >>> source = '''(ns example.core
        ...   (:require [clojure.string :as str]
        ...             [example.utils :as utils]))'''
        >>> parse_requires_with_kondo(source)
        {'clojure.string', 'example.utils'}

    This function properly handles:
    - Standard :require forms
    - :use forms (deprecated but still valid)
    - Prefix list notation: (:require [example [foo] [bar]])
    - Reader conditionals: #?(:clj [...] :cljs [...])
    - Multi-line requires
    - Requires with :as, :refer, :refer-macros, etc.
    """
    try:
        analysis = _run_clj_kondo_analysis(source_content, clj_kondo_path)
        ns_usages = analysis.get("namespace-usages", [])

        # Extract unique "to" namespaces
        # namespace-usages contains entries like:
        # {"from": "example.core", "to": "clojure.string", "alias": "str"}
        required = {usage["to"] for usage in ns_usages if "to" in usage}
        return required

    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
        # If clj-kondo fails for any reason, return empty set
        # This matches the behavior of the regex parser
        return set()


def parse_imports_with_kondo(source_content: str, clj_kondo_path: str = "clj-kondo") -> set[str]:
    """Extract Java imports using clj-kondo analysis.

    Args:
        source_content: The content of the Clojure source file
        clj_kondo_path: Path to clj-kondo binary (default: "clj-kondo" from PATH)

    Returns:
        A set of fully-qualified Java class names

    Example:
        >>> source = '''(ns example.core
        ...   (:import [java.util Date ArrayList]
        ...            java.io.File))'''
        >>> parse_imports_with_kondo(source)
        {'java.util.Date', 'java.util.ArrayList', 'java.io.File'}

    This function handles both import syntax styles:
    - Vector syntax: (:import [java.util Date ArrayList])
    - Single-class syntax: (:import java.util.Date java.io.File)
    - Mixed syntax in the same :import form
    - Inner classes: java.util.Map$Entry
    - Reader conditionals
    """
    try:
        analysis = _run_clj_kondo_analysis(source_content, clj_kondo_path)
        java_usages = analysis.get("java-class-usages", [])

        # Extract classes that are imports (not just usages)
        # Filter by the "import" flag to distinguish imports from usages
        # java-class-usages contains entries like:
        # {"class": "java.util.Date", "import": true}
        imported = {
            usage["class"]
            for usage in java_usages
            if usage.get("import") is True
        }
        return imported

    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
        # If clj-kondo fails for any reason, return empty set
        # This matches the behavior of the regex parser
        return set()
