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
from pants.core.util_rules import config_files, source_files, stripped_source_files, system_binaries
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
            *jvm_tool.rules(),
            *jvm_common.rules(),
            *non_jvm_dependencies.rules(),
            *dependency_inference_rules(),
            *clojure_symbol_mapping_rules(),
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
                    version="1.12.0",
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
                    version="1.12.0",
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


@maybe_skip_jdk_test
def test_infer_transitive_clojure_dependencies(rule_runner: RuleRunner) -> None:
    """Test that transitive Clojure dependencies are inferred correctly.

    This test verifies the dependency chain:
    clojure_test -> (inferred) -> clojure_source -> (inferred) -> clojure_source

    This catches issues where transitive first-party dependencies fail to be inferred.
    """
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="org.clojure_clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="1.12.0",
                )

                jvm_artifact(
                    name="com.taoensso_timbre",
                    group="com.taoensso",
                    artifact="timbre",
                    version="6.3.1",
                )

                jvm_artifact(
                    name="com.taoensso_encore",
                    group="com.taoensso",
                    artifact="encore",
                    version="3.132.0",
                )
                """
            ),
            "3rdparty/jvm/default.lock": dedent(
                """\
                # This lockfile was autogenerated by Pants. To regenerate, run:
                #
                #    pants generate-lockfiles
                #
                # --- BEGIN PANTS LOCKFILE METADATA: DO NOT EDIT OR REMOVE ---
                # {
                #   "version": 1,
                #   "generated_with_requirements": [
                #     "com.taoensso:encore:3.132.0,url=not_provided,jar=not_provided",
                #     "com.taoensso:timbre:6.3.1,url=not_provided,jar=not_provided",
                #     "org.clojure:clojure:1.12.0,url=not_provided,jar=not_provided"
                #   ]
                # }
                # --- END PANTS LOCKFILE METADATA ---

                [[entries]]
                file_name = "com.taoensso_encore_3.132.0.jar"
                [[entries.directDependencies]]
                group = "com.taoensso"
                artifact = "truss"
                version = "1.12.0"
                packaging = "jar"

                [[entries.directDependencies]]
                group = "org.clojure"
                artifact = "tools.reader"
                version = "1.5.0"
                packaging = "jar"

                [[entries.dependencies]]
                group = "com.taoensso"
                artifact = "truss"
                version = "1.12.0"
                packaging = "jar"

                [[entries.dependencies]]
                group = "org.clojure"
                artifact = "clojure"
                version = "1.12.0"
                packaging = "jar"

                [[entries.dependencies]]
                group = "org.clojure"
                artifact = "tools.reader"
                version = "1.5.0"
                packaging = "jar"


                [entries.coord]
                group = "com.taoensso"
                artifact = "encore"
                version = "3.132.0"
                packaging = "jar"
                [entries.file_digest]
                fingerprint = "0fbedbc65316d5af67d108fd2d76a352185efb530f9eb410bf8357b9c79cee7e"
                serialized_bytes_length = 131401
                [[entries]]
                file_name = "com.taoensso_timbre_6.3.1.jar"
                [[entries.directDependencies]]
                group = "com.taoensso"
                artifact = "encore"
                version = "3.132.0"
                packaging = "jar"

                [[entries.directDependencies]]
                group = "io.aviso"
                artifact = "pretty"
                version = "1.4.4"
                packaging = "jar"

                [[entries.dependencies]]
                group = "com.taoensso"
                artifact = "encore"
                version = "3.132.0"
                packaging = "jar"

                [[entries.dependencies]]
                group = "io.aviso"
                artifact = "pretty"
                version = "1.4.4"
                packaging = "jar"

                [[entries.dependencies]]
                group = "org.clojure"
                artifact = "clojure"
                version = "1.12.0"
                packaging = "jar"


                [entries.coord]
                group = "com.taoensso"
                artifact = "timbre"
                version = "6.3.1"
                packaging = "jar"
                [entries.file_digest]
                fingerprint = "877d0dd59cd4512dde125caeae361253adc637f6348d731bc565a9fb8ae6d95c"
                serialized_bytes_length = 50700
                [[entries]]
                directDependencies = []
                dependencies = []
                file_name = "com.taoensso_truss_1.12.0.jar"

                [entries.coord]
                group = "com.taoensso"
                artifact = "truss"
                version = "1.12.0"
                packaging = "jar"
                [entries.file_digest]
                fingerprint = "010b82784ef592d1d103bcf335d151d6721b195b40bbc52d38bd1c3edd857252"
                serialized_bytes_length = 17124
                [[entries]]
                file_name = "io.aviso_pretty_1.4.4.jar"
                [[entries.directDependencies]]
                group = "org.clojure"
                artifact = "clojure"
                version = "1.12.0"
                packaging = "jar"

                [[entries.dependencies]]
                group = "org.clojure"
                artifact = "clojure"
                version = "1.12.0"
                packaging = "jar"


                [entries.coord]
                group = "io.aviso"
                artifact = "pretty"
                version = "1.4.4"
                packaging = "jar"
                [entries.file_digest]
                fingerprint = "acf84fb56fa33602737bf3cdbd054cc4177baa186a3ea2936606d8065c22bf92"
                serialized_bytes_length = 26218
                [[entries]]
                file_name = "org.clojure_clojure_1.12.0.jar"
                [[entries.directDependencies]]
                group = "org.clojure"
                artifact = "core.specs.alpha"
                version = "0.4.74"
                packaging = "jar"

                [[entries.directDependencies]]
                group = "org.clojure"
                artifact = "spec.alpha"
                version = "0.5.238"
                packaging = "jar"

                [[entries.dependencies]]
                group = "org.clojure"
                artifact = "core.specs.alpha"
                version = "0.4.74"
                packaging = "jar"

                [[entries.dependencies]]
                group = "org.clojure"
                artifact = "spec.alpha"
                version = "0.5.238"
                packaging = "jar"


                [entries.coord]
                group = "org.clojure"
                artifact = "clojure"
                version = "1.12.0"
                packaging = "jar"
                [entries.file_digest]
                fingerprint = "c45333006441a059ea9fdb1341fc6c1f40b921a10dccd82665311e48a0384763"
                serialized_bytes_length = 4227052
                [[entries]]
                file_name = "org.clojure_core.specs.alpha_0.4.74.jar"
                [[entries.directDependencies]]
                group = "org.clojure"
                artifact = "clojure"
                version = "1.12.0"
                packaging = "jar"

                [[entries.dependencies]]
                group = "org.clojure"
                artifact = "clojure"
                version = "1.12.0"
                packaging = "jar"


                [entries.coord]
                group = "org.clojure"
                artifact = "core.specs.alpha"
                version = "0.4.74"
                packaging = "jar"
                [entries.file_digest]
                fingerprint = "eb73ac08cf49ba840c88ba67beef11336ca554333d9408808d78946e0feb9ddb"
                serialized_bytes_length = 4306
                [[entries]]
                file_name = "org.clojure_spec.alpha_0.5.238.jar"
                [[entries.directDependencies]]
                group = "org.clojure"
                artifact = "clojure"
                version = "1.12.0"
                packaging = "jar"

                [[entries.dependencies]]
                group = "org.clojure"
                artifact = "clojure"
                version = "1.12.0"
                packaging = "jar"


                [entries.coord]
                group = "org.clojure"
                artifact = "spec.alpha"
                version = "0.5.238"
                packaging = "jar"
                [entries.file_digest]
                fingerprint = "94cd99b6ea639641f37af4860a643b6ed399ee5a8be5d717cff0b663c8d75077"
                serialized_bytes_length = 636643
                [[entries]]
                file_name = "org.clojure_tools.reader_1.5.0.jar"
                [[entries.directDependencies]]
                group = "org.clojure"
                artifact = "clojure"
                version = "1.12.0"
                packaging = "jar"

                [[entries.dependencies]]
                group = "org.clojure"
                artifact = "clojure"
                version = "1.12.0"
                packaging = "jar"


                [entries.coord]
                group = "org.clojure"
                artifact = "tools.reader"
                version = "1.5.0"
                packaging = "jar"
                [entries.file_digest]
                fingerprint = "bfc8f709efb843f2ccc4daa93e2842ceb86e7b8d11d5544dc0ee68b6a0f4db3c"
                serialized_bytes_length = 52618
                """
            ),
            "BUILD": dedent(
                """\
                # Level 2: Base utility
                clojure_source(
                    name='logger',
                    source='my/logger.clj',
                    dependencies=[
                        '3rdparty/jvm:org.clojure_clojure',
                        '3rdparty/jvm:com.taoensso_timbre',
                    ],
                )

                # Level 1: Service that uses logger (inferred dependency)
                clojure_source(
                    name='service',
                    source='my/service.clj',
                    dependencies=['3rdparty/jvm:org.clojure_clojure'],
                    # Dependency on :logger should be inferred
                )

                # Level 0: Test that uses service (inferred dependency)
                clojure_test(
                    name='test',
                    source='my/service_test.clj',
                    dependencies=['3rdparty/jvm:org.clojure_clojure'],
                    # Dependency on :service should be inferred
                )
                """
            ),
            "my/logger.clj": dedent(
                """\
                (ns my.logger
                  (:require [taoensso.timbre :as timbre]))

                (defn log-info [msg]
                  (timbre/info msg))
                """
            ),
            "my/service.clj": dedent(
                """\
                (ns my.service
                  (:require [my.logger :as logger]))

                (defn process-request [req]
                  (logger/log-info (str "Processing: " req))
                  {:status "ok"})
                """
            ),
            "my/service_test.clj": dedent(
                """\
                (ns my.service-test
                  (:require [clojure.test :refer [deftest is]]
                            [my.service :as svc]))

                (deftest test-service
                  (is (= {:status "ok"} (svc/process-request "test"))))
                """
            ),
        }
    )

    # Get targets
    test_target = rule_runner.get_target(
        Address("", target_name="test", relative_file_path="my/service_test.clj")
    )
    service_target = rule_runner.get_target(
        Address("", target_name="service", relative_file_path="my/service.clj")
    )
    logger_target = rule_runner.get_target(
        Address("", target_name="logger", relative_file_path="my/logger.clj")
    )

    # Request inference for the test - should infer service
    from clojure_backend.dependency_inference import (
        ClojureSourceDependenciesInferenceFieldSet,
        ClojureTestDependenciesInferenceFieldSet,
    )

    test_inferred = rule_runner.request(
        InferredDependencies,
        [
            InferClojureTestDependencies(
                ClojureTestDependenciesInferenceFieldSet.create(test_target)
            )
        ],
    )
    assert test_inferred == InferredDependencies([service_target.address]), (
        f"Expected test to infer {service_target.address}, " f"but got {test_inferred}"
    )

    # Request inference for service - should infer logger
    service_inferred = rule_runner.request(
        InferredDependencies,
        [
            InferClojureSourceDependencies(
                ClojureSourceDependenciesInferenceFieldSet.create(service_target)
            )
        ],
    )
    assert service_inferred == InferredDependencies([logger_target.address]), (
        f"Expected service to infer {logger_target.address}, "
        f"but got {service_inferred}"
    )

    # Now actually run the test to verify it works end-to-end
    rule_runner.set_options(
        [
            "--jvm-resolves={'jvm-default': '3rdparty/jvm/default.lock'}",
            "--jvm-default-resolve=jvm-default",
            '--coursier-repos=["https://repo1.maven.org/maven2", "https://repo.clojars.org/"]',
        ],
        env_inherit=PYTHON_BOOTSTRAP_ENV,
    )

    test_result = rule_runner.request(
        TestResult,
        [ClojureTestRequest.Batch("", (ClojureTestFieldSet.create(test_target),), None)],
    )

    assert test_result.exit_code == 0, (
        f"Expected test to pass, but it failed with exit code {test_result.exit_code}. "
        f"Output:\n{(test_result.stdout_bytes + test_result.stderr_bytes).decode()}"
    )
