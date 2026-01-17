"""Tests for tools.build subsystem and classpath fetching."""

from __future__ import annotations

import pytest

from pants_backend_clojure.subsystems.tools_build import (
    ToolsBuildClasspathRequest,
    ToolsBuildSubsystem,
    rules as tools_build_rules,
)
from pants.engine.rules import QueryRule
from pants.jvm.resolve import coursier_fetch
from pants.jvm.resolve.coursier_fetch import ToolClasspath
from pants.testutil.rule_runner import RuleRunner


@pytest.fixture
def rule_runner() -> RuleRunner:
    rule_runner = RuleRunner(
        rules=[
            *tools_build_rules(),
            *coursier_fetch.rules(),
            QueryRule(ToolClasspath, [ToolsBuildClasspathRequest]),
        ],
    )
    return rule_runner


def test_fetch_tools_build_classpath(rule_runner: RuleRunner) -> None:
    """Test that tools.build can be fetched via Coursier."""
    request = ToolsBuildClasspathRequest()
    result = rule_runner.request(ToolClasspath, [request])

    # Check that we got a valid classpath with entries
    assert result.digest is not None
    classpath_entries = list(result.classpath_entries())
    assert len(classpath_entries) > 0

    # Verify tools.build is in the classpath
    tools_build_jars = [
        entry for entry in classpath_entries if "tools.build" in entry
    ]
    assert len(tools_build_jars) > 0, (
        f"Expected tools.build JAR in classpath, got: {classpath_entries}"
    )


def test_tools_build_version_option(rule_runner: RuleRunner) -> None:
    """Test that the version option is configurable."""
    # Set a custom version
    rule_runner.set_options(["--clojure-tools-build-version=0.10.5"])

    request = ToolsBuildClasspathRequest()
    result = rule_runner.request(ToolClasspath, [request])

    # Verify the specified version is fetched
    classpath_entries = list(result.classpath_entries())
    version_match = [
        entry for entry in classpath_entries if "0.10.5" in entry
    ]
    assert len(version_match) > 0, (
        f"Expected tools.build version 0.10.5 in classpath, got: {classpath_entries}"
    )
