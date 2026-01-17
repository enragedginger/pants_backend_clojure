from __future__ import annotations

import pytest

from pants_backend_clojure.utils.source_roots import determine_source_root


def test_determine_source_root_basic_case() -> None:
    """Test basic source root determination."""
    result = determine_source_root("src/example/core.clj", "example.core")
    assert result == "src"


def test_determine_source_root_nested_namespace() -> None:
    """Test source root with deeply nested namespace."""
    result = determine_source_root(
        "projects/foo/src/example/project/utils/string.clj",
        "example.project.utils.string",
    )
    assert result == "projects/foo/src"


def test_determine_source_root_hyphen_to_underscore() -> None:
    """Test that hyphens in namespaces are converted to underscores in paths."""
    result = determine_source_root(
        "src/example/project_a/core.clj", "example.project-a.core"
    )
    assert result == "src"


def test_determine_source_root_cljc_files() -> None:
    """Test source root determination with .cljc files."""
    result = determine_source_root(
        "src/example/multiplatform.cljc", "example.multiplatform"
    )
    assert result == "src"


def test_determine_source_root_deep_nesting() -> None:
    """Test source root with very deep directory structure."""
    result = determine_source_root(
        "monorepo/services/backend/src/com/company/service/api/handlers.clj",
        "com.company.service.api.handlers",
    )
    assert result == "monorepo/services/backend/src"


def test_determine_source_root_single_segment_namespace() -> None:
    """Test source root with single-segment namespace."""
    result = determine_source_root("src/core.clj", "core")
    assert result == "src"


def test_determine_source_root_no_subdirectory() -> None:
    """Test source root when file is in current directory."""
    result = determine_source_root("core.clj", "core")
    assert result == "."


def test_determine_source_root_complex_hyphenated_namespace() -> None:
    """Test namespace with multiple hyphens."""
    result = determine_source_root(
        "src/my_app/db_utils/connection_pool.clj", "my-app.db-utils.connection-pool"
    )
    assert result == "src"


def test_determine_source_root_fallback_when_no_match() -> None:
    """Test fallback behavior when namespace doesn't match file structure."""
    # Namespace doesn't match the path structure
    result = determine_source_root("src/foo/bar.clj", "completely.different.namespace")
    # Should fall back to the directory containing the file
    assert result == "src/foo"


def test_determine_source_root_test_directory() -> None:
    """Test source root determination for test files."""
    result = determine_source_root("test/example/core_test.clj", "example.core-test")
    assert result == "test"


def test_determine_source_root_multiple_hyphens_consecutive() -> None:
    """Test namespace with consecutive hyphens."""
    result = determine_source_root("src/my__special/module.clj", "my--special.module")
    assert result == "src"


def test_determine_source_root_empty_components() -> None:
    """Test edge case with namespace that could produce empty path components."""
    result = determine_source_root("src/example/util.clj", "example.util")
    assert result == "src"


def test_determine_source_root_numeric_components() -> None:
    """Test namespace with numeric components."""
    result = determine_source_root("src/api/v2/endpoints.clj", "api.v2.endpoints")
    assert result == "src"


def test_determine_source_root_underscore_in_namespace() -> None:
    """Test that underscores in file names remain underscores."""
    result = determine_source_root("src/my_module/core.clj", "my_module.core")
    assert result == "src"
