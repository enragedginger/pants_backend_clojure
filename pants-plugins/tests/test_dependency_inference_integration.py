"""Integration tests for Clojure dependency inference.

These tests verify that dependency inference works end-to-end by using RuleRunner,
following the same pattern as Pants' own JVM dependency inference tests.
"""

from __future__ import annotations

import ast
import os
from textwrap import dedent

import pytest
from pants.core.goals.package import rules as package_rules
from pants.core.goals.test import TestResult
from pants.core.goals.test import rules as test_goal_rules
from pants.core.target_types import ResourcesGeneratorTarget, ResourceTarget
from pants.core.util_rules import config_files, external_tool, source_files, stripped_source_files, system_binaries
from pants.engine.addresses import Address, Addresses
from pants.engine.rules import QueryRule
from pants.engine.target import (
    DependenciesRequest,
    ExplicitlyProvidedDependencies,
    InferredDependencies,
)
from pants.jvm import classpath, jdk_rules, jvm_common, non_jvm_dependencies
from pants.jvm.dependency_inference import artifact_mapper
from pants.jvm.dependency_inference import symbol_mapper as jvm_symbol_mapper
from pants.jvm.goals import lockfile
from pants.jvm.resolve import jvm_tool
from pants.jvm.resolve.coursier_fetch import rules as coursier_fetch_rules
from pants.jvm.resolve.coursier_setup import rules as coursier_setup_rules
from pants.jvm.target_types import JvmArtifactTarget
from pants.jvm.util_rules import rules as jvm_util_rules
from pants.testutil.rule_runner import PYTHON_BOOTSTRAP_ENV, RuleRunner
from pants_backend_clojure import compile_clj
from pants_backend_clojure.clojure_symbol_mapping import rules as clojure_symbol_mapping_rules
from pants_backend_clojure.dependency_inference import (
    InferClojureResourceDependencyRequest,
    InferClojureSourceDependencies,
    InferClojureTestDependencies,
)
from pants_backend_clojure.dependency_inference import rules as dependency_inference_rules
from pants_backend_clojure.goals.test import ClojureTestRequest
from pants_backend_clojure.goals.test import rules as test_runner_rules
from pants_backend_clojure.namespace_analysis import rules as namespace_analysis_rules
from pants_backend_clojure.target_types import (
    ClojureSourcesGeneratorTarget,
    ClojureSourceTarget,
    ClojureTestsGeneratorTarget,
    ClojureTestTarget,
)
from pants_backend_clojure.target_types import rules as target_types_rules
from tests.clojure_test_fixtures import CLOJURE_3RDPARTY_BUILD, CLOJURE_LOCKFILE


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
            *test_goal_rules(),
            *package_rules(),
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
            QueryRule(InferredDependencies, [InferClojureResourceDependencyRequest]),
            QueryRule(TestResult, [ClojureTestRequest.Batch]),
        ],
        target_types=[
            ClojureSourceTarget,
            ClojureSourcesGeneratorTarget,
            ClojureTestTarget,
            ClojureTestsGeneratorTarget,
            JvmArtifactTarget,
            ResourcesGeneratorTarget,
            ResourceTarget,
        ],
    )
    rule_runner.set_options(args=[], env_inherit=PYTHON_BOOTSTRAP_ENV)
    return rule_runner


_JVM_RESOLVES = {
    "jvm-default": "3rdparty/jvm/default.lock",
}


@maybe_skip_jdk_test
def test_missing_local_jar_raises_clear_error(rule_runner: RuleRunner) -> None:
    """A `jvm_artifact` with a missing local `jar=` file fails dependency inference
    with a clear error naming the missing path, rather than a cryptic IndexError
    from deep inside Coursier.

    The broken artifact lives in `jvm-default`, which has a valid, non-empty
    lockfile (CLOJURE_LOCKFILE), so `build_third_party_clojure_namespace_mapping`
    reaches the validation call rather than short-circuiting on a missing/empty
    lockfile. After scoping the validation to the resolve being built, it fires
    for any artifact in that resolve; the `clojure_source` need not depend on the
    broken artifact.
    """
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD
            + dedent(
                """\
                jvm_artifact(
                    name="brokenjar",
                    group="com.example",
                    artifact="broken",
                    version="1.0.0",
                    jar="missing.jar",
                    resolve="jvm-default",
                )
                """
            ),
            "3rdparty/jvm/default.lock": CLOJURE_LOCKFILE,
            "BUILD": dedent(
                """\
                clojure_source(
                    name='app',
                    source='app.clj',
                )
                """
            ),
            "app.clj": "(ns app)\n",
            # Note: 3rdparty/jvm/missing.jar is intentionally not created.
        }
    )

    # Point the default resolve at the non-empty lockfile so the namespace-mapping
    # build runs (the fixture default sets no resolves).
    rule_runner.set_options(
        [
            f"--jvm-resolves={repr(_JVM_RESOLVES)}",
            "--jvm-default-resolve=jvm-default",
        ],
        env_inherit=PYTHON_BOOTSTRAP_ENV,
    )

    from pants_backend_clojure.dependency_inference import ClojureSourceDependenciesInferenceFieldSet

    app_target = rule_runner.get_target(Address("", target_name="app", relative_file_path="app.clj"))

    with pytest.raises(Exception) as exc_info:
        rule_runner.request(
            InferredDependencies,
            [InferClojureSourceDependencies(ClojureSourceDependenciesInferenceFieldSet.create(app_target))],
        )

    error_text = str(exc_info.value)
    # Primary: the error names the offending local jar path.
    assert "missing.jar" in error_text
    # Secondary: it comes from the `jar=` validation, not an incidental failure.
    assert "the `jar=` field of `jvm_artifact` targets" in error_text


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
    test_target = rule_runner.get_target(Address("", target_name="test", relative_file_path="my/utils_test.clj"))
    utils_target = rule_runner.get_target(Address("", target_name="utils", relative_file_path="my/utils.clj"))

    # Request inference for the test
    from pants_backend_clojure.dependency_inference import ClojureTestDependenciesInferenceFieldSet

    inferred = rule_runner.request(
        InferredDependencies,
        [InferClojureTestDependencies(ClojureTestDependenciesInferenceFieldSet.create(test_target))],
    )

    # Should infer dependency on utils
    assert inferred == InferredDependencies([utils_target.address]), f"Expected {utils_target.address} to be inferred, but got {inferred}"


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
    test_target = rule_runner.get_target(Address("", target_name="test", relative_file_path="calculator_test.clj"))
    calculator_target = rule_runner.get_target(Address("", target_name="calculator", relative_file_path="calculator.clj"))

    # Request inference for the test
    from pants_backend_clojure.dependency_inference import ClojureTestDependenciesInferenceFieldSet

    inferred = rule_runner.request(
        InferredDependencies,
        [InferClojureTestDependencies(ClojureTestDependenciesInferenceFieldSet.create(test_target))],
    )

    # Should infer dependency on calculator
    assert inferred == InferredDependencies([calculator_target.address]), (
        f"Expected {calculator_target.address} to be inferred, but got {inferred}"
    )


# ===== Sibling resources inference tests =====


@maybe_skip_jdk_test
def test_infer_sibling_resources_from_src(rule_runner: RuleRunner) -> None:
    """Test that clojure_sources in src/ auto-depends on sibling resources/."""
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
            "mylib/src/BUILD": dedent(
                """\
                clojure_source(
                    name='lib',
                    source='my/core.clj',
                )
                """
            ),
            "mylib/src/my/core.clj": "(ns my.core)\n",
            "mylib/resources/BUILD": dedent(
                """\
                resources(
                    name='resources',
                    sources=['**/*'],
                )
                """
            ),
            "mylib/resources/config.edn": '{:key "value"}\n',
        }
    )

    source_target = rule_runner.get_target(Address("mylib/src", target_name="lib", relative_file_path="my/core.clj"))

    from pants_backend_clojure.dependency_inference import ClojureResourceDependencyInferenceFieldSet

    inferred = rule_runner.request(
        InferredDependencies,
        [InferClojureResourceDependencyRequest(ClojureResourceDependencyInferenceFieldSet.create(source_target))],
    )

    # Should find the resources target
    assert Address("mylib/resources", target_name="resources") in inferred.include


@maybe_skip_jdk_test
def test_infer_sibling_resources_from_test(rule_runner: RuleRunner) -> None:
    """Test that clojure_test in test/ auto-depends on sibling resources/."""
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
            "mylib/test/BUILD": dedent(
                """\
                clojure_test(
                    name='tests',
                    source='my/core_test.clj',
                )
                """
            ),
            "mylib/test/my/core_test.clj": dedent(
                """\
                (ns my.core-test
                  (:require [clojure.test :refer [deftest is]]))
                (deftest dummy (is true))
                """
            ),
            "mylib/resources/BUILD": dedent(
                """\
                resources(
                    name='resources',
                    sources=['**/*'],
                )
                """
            ),
            "mylib/resources/test-data.edn": "{}\n",
        }
    )

    test_target = rule_runner.get_target(Address("mylib/test", target_name="tests", relative_file_path="my/core_test.clj"))

    from pants_backend_clojure.dependency_inference import ClojureResourceDependencyInferenceFieldSet

    inferred = rule_runner.request(
        InferredDependencies,
        [InferClojureResourceDependencyRequest(ClojureResourceDependencyInferenceFieldSet.create(test_target))],
    )

    assert Address("mylib/resources", target_name="resources") in inferred.include


@maybe_skip_jdk_test
def test_no_sibling_resources_no_error(rule_runner: RuleRunner) -> None:
    """Test that missing sibling resources/ directory doesn't cause errors."""
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
            "mylib/src/BUILD": dedent(
                """\
                clojure_source(
                    name='lib',
                    source='my/core.clj',
                )
                """
            ),
            "mylib/src/my/core.clj": "(ns my.core)\n",
            # No resources/ directory at all
        }
    )

    source_target = rule_runner.get_target(Address("mylib/src", target_name="lib", relative_file_path="my/core.clj"))

    from pants_backend_clojure.dependency_inference import ClojureResourceDependencyInferenceFieldSet

    inferred = rule_runner.request(
        InferredDependencies,
        [InferClojureResourceDependencyRequest(ClojureResourceDependencyInferenceFieldSet.create(source_target))],
    )

    # Should return empty, not error
    assert inferred == InferredDependencies([])


@maybe_skip_jdk_test
def test_no_resources_inference_for_non_brick_paths(rule_runner: RuleRunner) -> None:
    """Test that resources inference only triggers for src/ and test/ directories."""
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
                    name='lib',
                    source='core.clj',
                )
                """
            ),
            "core.clj": "(ns core)\n",
        }
    )

    source_target = rule_runner.get_target(Address("", target_name="lib", relative_file_path="core.clj"))

    from pants_backend_clojure.dependency_inference import ClojureResourceDependencyInferenceFieldSet

    inferred = rule_runner.request(
        InferredDependencies,
        [InferClojureResourceDependencyRequest(ClojureResourceDependencyInferenceFieldSet.create(source_target))],
    )

    # Root-level target has no parent/src path, so no inference
    assert inferred == InferredDependencies([])


# ===== .cljc dependency inference tests =====


@maybe_skip_jdk_test
def test_infer_cljc_source_dependency(rule_runner: RuleRunner) -> None:
    """Test that dependency inference works for .cljc files."""
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
                    source='my/utils.cljc',
                )

                clojure_test(
                    name='test',
                    source='my/utils_test.cljc',
                )
                """
            ),
            "my/utils.cljc": dedent(
                """\
                (ns my.utils)

                (defn add [a b]
                  (+ a b))
                """
            ),
            "my/utils_test.cljc": dedent(
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

    test_target = rule_runner.get_target(Address("", target_name="test", relative_file_path="my/utils_test.cljc"))
    utils_target = rule_runner.get_target(Address("", target_name="utils", relative_file_path="my/utils.cljc"))

    from pants_backend_clojure.dependency_inference import ClojureTestDependenciesInferenceFieldSet

    inferred = rule_runner.request(
        InferredDependencies,
        [InferClojureTestDependencies(ClojureTestDependenciesInferenceFieldSet.create(test_target))],
    )

    assert inferred == InferredDependencies([utils_target.address])


@maybe_skip_jdk_test
def test_infer_clj_test_depending_on_cljc_source(rule_runner: RuleRunner) -> None:
    """Test that a .clj test can infer dependency on a .cljc source."""
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
                    source='my/utils.cljc',
                )

                clojure_test(
                    name='test',
                    source='my/utils_test.clj',
                )
                """
            ),
            "my/utils.cljc": dedent(
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

    test_target = rule_runner.get_target(Address("", target_name="test", relative_file_path="my/utils_test.clj"))
    utils_target = rule_runner.get_target(Address("", target_name="utils", relative_file_path="my/utils.cljc"))

    from pants_backend_clojure.dependency_inference import ClojureTestDependenciesInferenceFieldSet

    inferred = rule_runner.request(
        InferredDependencies,
        [InferClojureTestDependencies(ClojureTestDependenciesInferenceFieldSet.create(test_target))],
    )

    assert inferred == InferredDependencies([utils_target.address])
