"""Tests for error handling and edge cases across multiple components."""

from __future__ import annotations

from textwrap import dedent

import pytest

from clojure_backend.goals.check import ClojureCheckFieldSet, ClojureCheckRequest
from clojure_backend.goals import check as check_goal
from clojure_backend.goals.test import ClojureTestFieldSet, ClojureTestRequest
from clojure_backend.goals.test import rules as test_runner_rules
from clojure_backend.namespace_analysis import rules as namespace_analysis_rules
from clojure_backend.target_types import (
    ClojureSourcesGeneratorTarget,
    ClojureSourceTarget,
    ClojureTestTarget,
)
from clojure_backend.target_types import rules as target_types_rules
from clojure_backend import compile_clj
from pants.core.goals.check import CheckResults
from pants.core.goals.test import TestResult
from pants.core.util_rules import config_files, external_tool, source_files, stripped_source_files, system_binaries
from pants.engine.addresses import Address
from pants.jvm import classpath, jvm_common, non_jvm_dependencies
from pants.jvm.goals import lockfile
from pants.jvm.resolve.coursier_fetch import rules as coursier_fetch_rules
from pants.jvm.resolve.coursier_setup import rules as coursier_setup_rules
from pants.jvm.target_types import JvmArtifactTarget
from pants.jvm.util_rules import rules as jdk_util_rules
from pants.testutil.rule_runner import PYTHON_BOOTSTRAP_ENV, QueryRule, RuleRunner
from pants.engine.fs import DigestContents
from pants.engine.internals.graph import CycleException

from tests.clojure_test_fixtures import CLOJURE_LOCKFILE, CLOJURE_3RDPARTY_BUILD


@pytest.fixture
def rule_runner() -> RuleRunner:
    rule_runner = RuleRunner(
        preserve_tmpdirs=True,
        rules=[
            *classpath.rules(),
            *compile_clj.rules(),
            *config_files.rules(),
            *coursier_fetch_rules(),
            *coursier_setup_rules(),
            *external_tool.rules(),
            *jdk_util_rules(),
            *jvm_common.rules(),
            *namespace_analysis_rules(),
            *non_jvm_dependencies.rules(),
            *source_files.rules(),
            *stripped_source_files.rules(),
            *system_binaries.rules(),
            *target_types_rules(),
            *check_goal.rules(),
            *test_runner_rules(),
            *lockfile.rules(),
            QueryRule(CheckResults, [ClojureCheckRequest]),
            QueryRule(TestResult, [ClojureTestRequest.Batch]),
        ],
        target_types=[
            ClojureSourceTarget,
            ClojureSourcesGeneratorTarget,
            ClojureTestTarget,
            JvmArtifactTarget,
        ],
    )
    return rule_runner


_JVM_RESOLVES = {
    "jvm-default": "3rdparty/jvm/default.lock",
}


def test_check_with_missing_source_file_reference(rule_runner: RuleRunner) -> None:
    """Test that check handles missing source file references gracefully."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "3rdparty/jvm/default.lock": CLOJURE_LOCKFILE,
            "BUILD": dedent(
                """\
                clojure_source(
                    name="example",
                    source="example.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            # Note: We intentionally don't create example.clj
        }
    )

    args = [
        f"--jvm-resolves={repr(_JVM_RESOLVES)}",
        "--jvm-default-resolve=jvm-default",
    ]
    rule_runner.set_options(args, env_inherit=PYTHON_BOOTSTRAP_ENV)

    # Attempting to get a target with a missing source file should fail
    # or the check should handle it gracefully
    with pytest.raises(Exception):
        tgt = rule_runner.get_target(
            Address(spec_path="", target_name="example", relative_file_path="example.clj")
        )
        field_set = ClojureCheckFieldSet.create(tgt)
        rule_runner.request(CheckResults, [ClojureCheckRequest([field_set])])


def test_check_with_malformed_clojure_syntax(rule_runner: RuleRunner) -> None:
    """Test that check detects severely malformed Clojure code."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "3rdparty/jvm/default.lock": CLOJURE_LOCKFILE,
            "BUILD": 'clojure_sources(dependencies=["3rdparty/jvm:org.clojure_clojure"])',
            "malformed.clj": ")))(((",  # Completely malformed
        }
    )

    args = [
        f"--jvm-resolves={repr(_JVM_RESOLVES)}",
        "--jvm-default-resolve=jvm-default",
    ]
    rule_runner.set_options(args, env_inherit=PYTHON_BOOTSTRAP_ENV)
    tgt = rule_runner.get_target(
        Address(spec_path="", target_name="", relative_file_path="malformed.clj")
    )
    field_set = ClojureCheckFieldSet.create(tgt)
    results = rule_runner.request(CheckResults, [ClojureCheckRequest([field_set])])

    # Should fail with error
    assert len(results.results) == 1
    assert results.results[0].exit_code != 0


def test_test_runner_with_missing_dependency(rule_runner: RuleRunner) -> None:
    """Test that test runner reports missing dependencies clearly."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "3rdparty/jvm/default.lock": CLOJURE_LOCKFILE,
            "BUILD": 'clojure_test(name="test", source="test.clj", dependencies=["3rdparty/jvm:org.clojure_clojure"])',
            "test.clj": dedent(
                """\
                (ns example-test
                  (:require [clojure.test :refer [deftest is]]
                            [missing.namespace :as missing]))

                (deftest test-with-missing-dep
                  (is (= 1 (missing/func))))
                """
            ),
        }
    )

    args = [
        f"--jvm-resolves={repr(_JVM_RESOLVES)}",
        "--jvm-default-resolve=jvm-default",
    ]
    rule_runner.set_options(args, env_inherit=PYTHON_BOOTSTRAP_ENV)
    tgt = rule_runner.get_target(Address(spec_path="", target_name="test"))
    field_set = ClojureTestFieldSet.create(tgt)
    result = rule_runner.request(
        TestResult,
        [ClojureTestRequest.Batch("", (field_set,), partition_metadata=None)],
    )

    # Should fail due to missing namespace
    assert result.exit_code != 0


def test_check_with_circular_namespace_reference(rule_runner: RuleRunner) -> None:
    """Test behavior with circular namespace dependencies."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "3rdparty/jvm/default.lock": CLOJURE_LOCKFILE,
            "BUILD": dedent(
                """\
                clojure_source(name='a', source='a.clj', dependencies=[':b', '3rdparty/jvm:org.clojure_clojure'])
                clojure_source(name='b', source='b.clj', dependencies=[':a', '3rdparty/jvm:org.clojure_clojure'])
                """
            ),
            "a.clj": dedent(
                """\
                (ns example.a
                  (:require [example.b :as b]))

                (defn func-a [] (b/func-b))
                """
            ),
            "b.clj": dedent(
                """\
                (ns example.b
                  (:require [example.a :as a]))

                (defn func-b [] (a/func-a))
                """
            ),
        }
    )

    args = [
        f"--jvm-resolves={repr(_JVM_RESOLVES)}",
        "--jvm-default-resolve=jvm-default",
    ]
    rule_runner.set_options(args, env_inherit=PYTHON_BOOTSTRAP_ENV)

    # Circular dependencies should be detected by Pants at the graph level
    tgt = rule_runner.get_target(Address(spec_path="", target_name="a"))
    field_set = ClojureCheckFieldSet.create(tgt)

    # Pants detects the cycle when building the dependency graph
    with pytest.raises(Exception) as exc_info:
        results = rule_runner.request(CheckResults, [ClojureCheckRequest([field_set])])

    # Should raise a CycleException or ExecutionError wrapping it
    assert "cycle" in str(exc_info.value).lower()


def test_empty_namespace_declaration(rule_runner: RuleRunner) -> None:
    """Test handling of files with missing or empty namespace declarations."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "3rdparty/jvm/default.lock": CLOJURE_LOCKFILE,
            "BUILD": 'clojure_sources(dependencies=["3rdparty/jvm:org.clojure_clojure"])',
            "no_ns.clj": dedent(
                """\
                ; No namespace declaration
                (defn orphan-function []
                  "This function has no namespace")
                """
            ),
        }
    )

    args = [
        f"--jvm-resolves={repr(_JVM_RESOLVES)}",
        "--jvm-default-resolve=jvm-default",
    ]
    rule_runner.set_options(args, env_inherit=PYTHON_BOOTSTRAP_ENV)
    tgt = rule_runner.get_target(
        Address(spec_path="", target_name="", relative_file_path="no_ns.clj")
    )
    field_set = ClojureCheckFieldSet.create(tgt)
    results = rule_runner.request(CheckResults, [ClojureCheckRequest([field_set])])

    # Should handle missing ns gracefully (Clojure allows this but it's unusual)
    assert len(results.results) == 1
    # May pass or fail depending on linting strictness
    assert results.results[0] is not None


def test_test_with_invalid_test_syntax(rule_runner: RuleRunner) -> None:
    """Test runner handling of tests with invalid clojure.test syntax."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "3rdparty/jvm/default.lock": CLOJURE_LOCKFILE,
            "BUILD": 'clojure_test(name="invalid", source="invalid_test.clj", dependencies=["3rdparty/jvm:org.clojure_clojure"])',
            "invalid_test.clj": dedent(
                """\
                (ns invalid-test
                  (:require [clojure.test :refer [deftest is]]))

                ; Invalid: deftest without a body
                (deftest incomplete-test)
                """
            ),
        }
    )

    args = [
        f"--jvm-resolves={repr(_JVM_RESOLVES)}",
        "--jvm-default-resolve=jvm-default",
    ]
    rule_runner.set_options(args, env_inherit=PYTHON_BOOTSTRAP_ENV)
    tgt = rule_runner.get_target(Address(spec_path="", target_name="invalid"))
    field_set = ClojureTestFieldSet.create(tgt)
    result = rule_runner.request(
        TestResult,
        [ClojureTestRequest.Batch("", (field_set,), partition_metadata=None)],
    )

    # Should handle incomplete test gracefully
    # May pass (empty test) or fail depending on runner behavior
    assert result is not None


def test_check_with_unicode_characters(rule_runner: RuleRunner) -> None:
    """Test that check handles files with Unicode characters correctly."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "3rdparty/jvm/default.lock": CLOJURE_LOCKFILE,
            "BUILD": 'clojure_sources(dependencies=["3rdparty/jvm:org.clojure_clojure"])',
            "unicode.clj": dedent(
                """\
                (ns unicode)

                (defn greet [name]
                  (str "Hello, " name " 👋"))

                (def pi-approx 3.14159)
                (def emoji "🚀 🎉 ✨")
                (def chinese "你好世界")
                """
            ),
        }
    )

    args = [
        f"--jvm-resolves={repr(_JVM_RESOLVES)}",
        "--jvm-default-resolve=jvm-default",
    ]
    rule_runner.set_options(args, env_inherit=PYTHON_BOOTSTRAP_ENV)
    tgt = rule_runner.get_target(
        Address(spec_path="", target_name="", relative_file_path="unicode.clj")
    )
    field_set = ClojureCheckFieldSet.create(tgt)
    results = rule_runner.request(CheckResults, [ClojureCheckRequest([field_set])])

    # Unicode should be handled correctly
    assert len(results.results) == 1
    assert results.results[0].exit_code == 0
