"""Utilities for determining Clojure source roots.

This module provides functions for determining the source root directory
for Clojure source files based on their namespace declarations.
"""

from __future__ import annotations


def determine_source_root(file_path: str, namespace: str) -> str | None:
    """Determine the source root directory for a Clojure file.

    For a file like projects/foo/src/example/core.clj with namespace example.core,
    the source root is projects/foo/src.

    Args:
        file_path: The path to the Clojure source file.
        namespace: The namespace declared in the file.

    Returns:
        The source root directory, or None if the namespace can't be matched
        to the file path.

    Examples:
        >>> determine_source_root("projects/foo/src/example/core.clj", "example.core")
        "projects/foo/src"

        >>> determine_source_root("src/example/project_a/core.clj", "example.project-a.core")
        "src"
    """
    # Convert namespace to expected path (example.project-a.core -> example/project_a/core)
    expected_path_parts = namespace.replace(".", "/").replace("-", "_").split("/")

    # Remove .clj/.cljc extension from file path
    clean_path = file_path
    if clean_path.endswith(".clj"):
        clean_path = clean_path[:-4]
    elif clean_path.endswith(".cljc"):
        clean_path = clean_path[:-5]

    # Split the file path into parts
    path_parts = clean_path.split("/")

    # Find where the namespace path starts in the file path
    # e.g., projects/foo/src/example/core matches example/core at the end
    # Walk backwards from the file path, matching namespace components
    for i in range(len(path_parts) - len(expected_path_parts), -1, -1):
        if path_parts[i:] == expected_path_parts:
            # Source root is everything before the namespace path
            return "/".join(path_parts[:i]) if i > 0 else "."

    # Fallback: use the directory containing the source file
    return "/".join(file_path.split("/")[:-1]) if "/" in file_path else "."
