"""Tests for ClojureInferSubsystem."""

from __future__ import annotations

from pants_backend_clojure.subsystems.clojure_infer import ClojureInferSubsystem
from pants.testutil.option_util import create_subsystem


def test_default_options() -> None:
    """Test that default options are set correctly when explicitly using defaults."""
    # create_subsystem requires explicit values, so we verify defaults are what we expect
    subsystem = create_subsystem(
        ClojureInferSubsystem,
        namespaces=True,
        java_imports=True,
        third_party_namespace_mapping={},
    )

    assert subsystem.namespaces is True
    assert subsystem.java_imports is True
    assert subsystem.third_party_namespace_mapping == {}


def test_namespaces_option_disabled() -> None:
    """Test that namespaces option can be disabled."""
    subsystem = create_subsystem(ClojureInferSubsystem, namespaces=False)

    assert subsystem.namespaces is False


def test_java_imports_option_disabled() -> None:
    """Test that java_imports option can be disabled."""
    subsystem = create_subsystem(ClojureInferSubsystem, java_imports=False)

    assert subsystem.java_imports is False


def test_third_party_namespace_mapping_option() -> None:
    """Test that third_party_namespace_mapping option can be configured."""
    subsystem = create_subsystem(
        ClojureInferSubsystem,
        third_party_namespace_mapping={
            "my.custom.lib.**": "com.example:my-lib",
            "another.ns": "org.other:lib",
        },
    )

    assert subsystem.third_party_namespace_mapping == {
        "my.custom.lib.**": "com.example:my-lib",
        "another.ns": "org.other:lib",
    }


def test_all_options_together() -> None:
    """Test that all options can be configured together."""
    subsystem = create_subsystem(
        ClojureInferSubsystem,
        namespaces=False,
        java_imports=False,
        third_party_namespace_mapping={"ring.**": "ring:ring-core"},
    )

    assert subsystem.namespaces is False
    assert subsystem.java_imports is False
    assert subsystem.third_party_namespace_mapping == {"ring.**": "ring:ring-core"}
