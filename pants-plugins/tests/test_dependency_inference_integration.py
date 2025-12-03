"""Integration tests for Clojure dependency inference.

These tests verify that dependency inference works end-to-end by using RuleRunner,
following the same pattern as Pants' own JVM dependency inference tests.
"""

from __future__ import annotations

import ast
import os
from textwrap import dedent

import pytest

from clojure_backend.dependency_inference import (
    InferClojureSourceDependencies,
    InferClojureTestDependencies,
)
from clojure_backend.dependency_inference import rules as dependency_inference_rules
from clojure_backend.clojure_symbol_mapping import rules as clojure_symbol_mapping_rules
from clojure_backend.namespace_analysis import rules as namespace_analysis_rules
from clojure_backend.goals.test import ClojureTestFieldSet, ClojureTestRequest
from clojure_backend.goals.test import rules as test_runner_rules
from clojure_backend.target_types import (
    ClojureSourcesGeneratorTarget,
    ClojureSourceTarget,
    ClojureTestsGeneratorTarget,
    ClojureTestTarget,
)
from clojure_backend.target_types import rules as target_types_rules
from clojure_backend import compile_clj
from pants.core.goals.test import TestResult
from pants.core.util_rules import config_files, external_tool, source_files, stripped_source_files, system_binaries
from pants.jvm import classpath, jvm_common, non_jvm_dependencies
from pants.engine.addresses import Address, Addresses
from pants.engine.rules import QueryRule
from pants.engine.target import (
    DependenciesRequest,
    ExplicitlyProvidedDependencies,
    InferredDependencies,
)
from pants.jvm import jdk_rules
from pants.jvm.dependency_inference import artifact_mapper
from pants.jvm.dependency_inference import symbol_mapper as jvm_symbol_mapper
from pants.jvm.goals import lockfile
from pants.jvm.resolve import jvm_tool
from pants.jvm.resolve.coursier_fetch import rules as coursier_fetch_rules
from pants.jvm.resolve.coursier_setup import rules as coursier_setup_rules
from pants.jvm.target_types import JvmArtifactTarget
from pants.jvm.util_rules import rules as jvm_util_rules
from pants.testutil.rule_runner import PYTHON_BOOTSTRAP_ENV, RuleRunner


def maybe_skip_jdk_test(func):
    """Skip JDK tests based on environment variable."""
    run_jdk_tests = bool(ast.literal_eval(os.environ.get("PANTS_RUN_JDK_TESTS", "True")))
    return pytest.mark.skipif(not run_jdk_tests, reason="Skip JDK tests")(func)


@pytest.fixture
def rule_runner() -> RuleRunner:
    """Set up a RuleRunner for Clojure dependency inference tests."""
    rule_runner = RuleRunner(
        rules=[
            *classpath.rules(),
            *compile_clj.rules(),
            *config_files.rules(),
            *coursier_fetch_rules(),
            *coursier_setup_rules(),
            *external_tool.rules(),
            *jvm_tool.rules(),
            *jvm_common.rules(),
            *non_jvm_dependencies.rules(),
            *dependency_inference_rules(),
            *clojure_symbol_mapping_rules(),
            *namespace_analysis_rules(),
            *target_types_rules(),
            *test_runner_rules(),
            *source_files.rules(),
            *stripped_source_files.rules(),
            *system_binaries.rules(),
            *jvm_util_rules(),
            *jdk_rules.rules(),
            *artifact_mapper.rules(),
            *jvm_symbol_mapper.rules(),
            *lockfile.rules(),
            QueryRule(Addresses, [DependenciesRequest]),
            QueryRule(ExplicitlyProvidedDependencies, [DependenciesRequest]),
            QueryRule(InferredDependencies, [InferClojureSourceDependencies]),
            QueryRule(InferredDependencies, [InferClojureTestDependencies]),
            QueryRule(TestResult, [ClojureTestRequest.Batch]),
        ],
        target_types=[
            ClojureSourceTarget,
            ClojureSourcesGeneratorTarget,
            ClojureTestTarget,
            ClojureTestsGeneratorTarget,
            JvmArtifactTarget,
        ],
    )
    rule_runner.set_options(args=[], env_inherit=PYTHON_BOOTSTRAP_ENV)
    return rule_runner


@maybe_skip_jdk_test
def test_infer_clojure_source_dependency(rule_runner: RuleRunner) -> None:
    """Test that Clojure sources can infer dependencies on other Clojure sources."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="org.clojure_clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="1.12.3",
                )
                """
            ),
            "3rdparty/jvm/default.lock": "# Empty lockfile for testing\n",
            "BUILD": dedent(
                """\
                clojure_source(
                    name='utils',
                    source='my/utils.clj',
                )

                clojure_test(
                    name='test',
                    source='my/utils_test.clj',
                    # Dependency on :utils should be inferred
                )
                """
            ),
            "my/utils.clj": dedent(
                """\
                (ns my.utils)

                (defn add [a b]
                  (+ a b))
                """
            ),
            "my/utils_test.clj": dedent(
                """\
                (ns my.utils-test
                  (:require [clojure.test :refer [deftest is]]
                            [my.utils :as utils]))

                (deftest test-add
                  (is (= 5 (utils/add 2 3))))
                """
            ),
        }
    )

    # Get the test target
    test_target = rule_runner.get_target(
        Address("", target_name="test", relative_file_path="my/utils_test.clj")
    )
    utils_target = rule_runner.get_target(
        Address("", target_name="utils", relative_file_path="my/utils.clj")
    )

    # Request inference for the test
    from clojure_backend.dependency_inference import ClojureTestDependenciesInferenceFieldSet

    inferred = rule_runner.request(
        InferredDependencies,
        [
            InferClojureTestDependencies(
                ClojureTestDependenciesInferenceFieldSet.create(test_target)
            )
        ],
    )

    # Should infer dependency on utils
    assert inferred == InferredDependencies([utils_target.address]), (
        f"Expected {utils_target.address} to be inferred, "
        f"but got {inferred}"
    )


@maybe_skip_jdk_test
def test_infer_clojure_test_dependency(rule_runner: RuleRunner) -> None:
    """Test that Clojure tests can infer dependencies on Clojure sources."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="org.clojure_clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="1.12.3",
                )
                """
            ),
            "3rdparty/jvm/default.lock": "# Empty lockfile for testing\n",
            "BUILD": dedent(
                """\
                clojure_source(
                    name='calculator',
                    source='calculator.clj',
                )

                clojure_test(
                    name='test',
                    source='calculator_test.clj',
                    # Dependency on :calculator should be inferred
                )
                """
            ),
            "calculator.clj": dedent(
                """\
                (ns calculator)

                (defn add [a b]
                  (+ a b))
                """
            ),
            "calculator_test.clj": dedent(
                """\
                (ns calculator-test
                  (:require [clojure.test :refer [deftest is]]
                            [calculator :as calc]))

                (deftest test-add
                  (is (= 5 (calc/add 2 3))))
                """
            ),
        }
    )

    # Get the test target
    test_target = rule_runner.get_target(
        Address("", target_name="test", relative_file_path="calculator_test.clj")
    )
    calculator_target = rule_runner.get_target(
        Address("", target_name="calculator", relative_file_path="calculator.clj")
    )

    # Request inference for the test
    from clojure_backend.dependency_inference import ClojureTestDependenciesInferenceFieldSet

    inferred = rule_runner.request(
        InferredDependencies,
        [
            InferClojureTestDependencies(
                ClojureTestDependenciesInferenceFieldSet.create(test_target)
            )
        ],
    )

    # Should infer dependency on calculator
    assert inferred == InferredDependencies([calculator_target.address]), (
        f"Expected {calculator_target.address} to be inferred, "
        f"but got {inferred}"
    )


