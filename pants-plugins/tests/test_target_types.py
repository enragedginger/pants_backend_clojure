from __future__ import annotations

from textwrap import dedent

import pytest

from pants_backend_clojure.target_types import (
    ClojureDeployJarTarget,
    ClojureMainNamespaceField,
    ClojureSourceField,
    ClojureSourcesGeneratorSourcesField,
    ClojureSourcesGeneratorTarget,
    ClojureSourceTarget,
    ClojureTestExtraEnvVarsField,
    ClojureTestSourceField,
    ClojureTestsGeneratorSourcesField,
    ClojureTestsGeneratorTarget,
    ClojureTestTarget,
    ClojureTestTimeoutField,
)
from pants_backend_clojure.target_types import rules as target_types_rules
from pants.build_graph.address import Address
from pants.engine.internals.graph import _TargetParametrizations, _TargetParametrizationsRequest
from pants.engine.rules import QueryRule
from pants.engine.target import Target
from pants.jvm import jvm_common
from pants.jvm.target_types import (
    JvmDependenciesField,
    JvmJdkField,
    JvmResolveField,
)
from pants.testutil.rule_runner import RuleRunner


@pytest.fixture
def rule_runner() -> RuleRunner:
    rule_runner = RuleRunner(
        target_types=[
            ClojureSourceTarget,
            ClojureSourcesGeneratorTarget,
            ClojureTestTarget,
            ClojureTestsGeneratorTarget,
            ClojureDeployJarTarget,
        ],
        rules=[
            *target_types_rules(),
            *jvm_common.rules(),
            QueryRule(_TargetParametrizations, [_TargetParametrizationsRequest]),
        ],
    )
    return rule_runner


_JVM_RESOLVES = {
    "jvm-default": "3rdparty/jvm/default.lock",
    "java17": "locks/jvm/java17.lock.jsonc",
    "java21": "locks/jvm/java21.lock.jsonc",
}


def assert_generated(
    rule_runner: RuleRunner,
    address: Address,
    *,
    build_content: str,
    expected_targets: set[Target],
) -> None:
    rule_runner.write_files({"BUILD": build_content})
    rule_runner.set_options(
        [
            f"--jvm-resolves={repr(_JVM_RESOLVES)}",
            "--jvm-default-resolve=jvm-default",
        ]
    )

    parametrizations = rule_runner.request(
        _TargetParametrizations,
        [
            _TargetParametrizationsRequest(
                address,
                description_of_origin="tests",
            ),
        ],
    )
    assert expected_targets == {
        t for parametrization in parametrizations for t in parametrization.parametrization.values()
    }


def test_clojure_source_field_extensions() -> None:
    """Test that ClojureSourceField accepts .clj and .cljc files."""
    assert ClojureSourceField.expected_file_extensions == (".clj", ".cljc")


def test_clojure_sources_field_extensions() -> None:
    """Test that ClojureGeneratorSourcesField accepts .clj and .cljc files."""
    assert ClojureSourcesGeneratorSourcesField.expected_file_extensions == (".clj", ".cljc")


def test_clojure_sources_default_globs() -> None:
    """Test that clojure_sources has correct default glob patterns."""
    assert ClojureSourcesGeneratorSourcesField.default == (
        "*.clj",
        "*.cljc",
        "!*_test.clj",
        "!*_test.cljc",
        "!test_*.clj",
        "!test_*.cljc",
    )


def test_generate_clojure_source_targets(rule_runner: RuleRunner) -> None:
    """Test that clojure_sources generates individual clojure_source targets."""
    rule_runner.write_files(
        {
            "src/clj/BUILD": "clojure_sources(name='lib')\n",
            "src/clj/example.clj": "(ns example.core)",
            "src/clj/util.clj": "(ns example.util)",
            "src/clj/shared.cljc": "(ns example.shared)",
        }
    )
    rule_runner.set_options(
        [
            f"--jvm-resolves={repr(_JVM_RESOLVES)}",
            "--jvm-default-resolve=jvm-default",
        ]
    )

    # Request the generator target
    parametrizations = rule_runner.request(
        _TargetParametrizations,
        [
            _TargetParametrizationsRequest(
                Address("src/clj", target_name="lib"),
                description_of_origin="tests",
            ),
        ],
    )

    # The generator should create individual targets for each .clj and .cljc file
    generated_targets = {
        t for parametrization in parametrizations for t in parametrization.parametrization.values()
    }

    # Check that we got three targets (one for each file)
    assert len(generated_targets) == 3

    # Check that all targets are ClojureSourceTarget instances
    assert all(isinstance(t, ClojureSourceTarget) for t in generated_targets)

    # Check that the targets have the expected source files
    source_files = {t[ClojureSourceField].value for t in generated_targets}
    assert source_files == {"example.clj", "util.clj", "shared.cljc"}


def test_clojure_source_with_resolve(rule_runner: RuleRunner) -> None:
    """Test that clojure_source respects the resolve field."""
    rule_runner.write_files({"src/clj/BUILD": 'clojure_sources(name="lib", resolve="java17")\n', "src/clj/example.clj": ""})
    assert_generated(
        rule_runner,
        Address("src/clj", target_name="lib"),
        build_content='clojure_sources(name="lib", resolve="java17")\n',
        expected_targets={
            ClojureSourceTarget(
                {
                    ClojureSourceField.alias: "example.clj",
                    JvmResolveField.alias: "java17",
                },
                Address("src/clj", target_name="lib", relative_file_path="example.clj"),
            ),
        },
    )


def test_clojure_source_with_jdk(rule_runner: RuleRunner) -> None:
    """Test that clojure_source respects the jdk field."""
    rule_runner.write_files({"src/clj/BUILD": 'clojure_sources(name="lib", jdk="17")\n', "src/clj/example.clj": ""})
    assert_generated(
        rule_runner,
        Address("src/clj", target_name="lib"),
        build_content='clojure_sources(name="lib", jdk="17")\n',
        expected_targets={
            ClojureSourceTarget(
                {
                    ClojureSourceField.alias: "example.clj",
                    JvmJdkField.alias: "17",
                },
                Address("src/clj", target_name="lib", relative_file_path="example.clj"),
            ),
        },
    )


def test_clojure_source_with_dependencies(rule_runner: RuleRunner) -> None:
    """Test that clojure_source respects the dependencies field."""
    rule_runner.write_files({"src/clj/BUILD": 'clojure_sources(name="lib", dependencies=["//3rdparty/jvm:clojure"])\n', "src/clj/example.clj": ""})
    assert_generated(
        rule_runner,
        Address("src/clj", target_name="lib"),
        build_content='clojure_sources(name="lib", dependencies=["//3rdparty/jvm:clojure"])\n',
        expected_targets={
            ClojureSourceTarget(
                {
                    ClojureSourceField.alias: "example.clj",
                    JvmDependenciesField.alias: ["//3rdparty/jvm:clojure"],
                },
                Address("src/clj", target_name="lib", relative_file_path="example.clj"),
            ),
        },
    )


def test_clojure_sources_excludes_test_files(rule_runner: RuleRunner) -> None:
    """Test that clojure_sources can exclude test files using glob patterns."""
    rule_runner.write_files(
        {
            "src/clj/BUILD": "clojure_sources(name='lib', sources=['*.clj', '!*_test.clj'])\n",
            "src/clj/example.clj": "(ns example.core)",
            "src/clj/example_test.clj": "(ns example.core-test)",
        }
    )
    rule_runner.set_options(
        [
            f"--jvm-resolves={repr(_JVM_RESOLVES)}",
            "--jvm-default-resolve=jvm-default",
        ]
    )

    parametrizations = rule_runner.request(
        _TargetParametrizations,
        [
            _TargetParametrizationsRequest(
                Address("src/clj", target_name="lib"),
                description_of_origin="tests",
            ),
        ],
    )

    generated_targets = {
        t for parametrization in parametrizations for t in parametrization.parametrization.values()
    }

    # Should only have one target (example.clj), not the test file
    assert len(generated_targets) == 1
    source_files = {t[ClojureSourceField].value for t in generated_targets}
    assert source_files == {"example.clj"}


# -----------------------------------------------------------------------------------------------
# Test target type tests
# -----------------------------------------------------------------------------------------------


def test_clojure_test_field_extensions() -> None:
    """Test that ClojureTestSourceField accepts .clj and .cljc files."""
    assert ClojureTestSourceField.expected_file_extensions == (".clj", ".cljc")


def test_clojure_tests_default_globs() -> None:
    """Test that clojure_tests has correct default glob patterns for test files."""
    assert ClojureTestsGeneratorSourcesField.default == (
        "*_test.clj",
        "*_test.cljc",
        "test_*.clj",
        "test_*.cljc",
    )


def test_clojure_sources_excludes_tests_by_default() -> None:
    """Test that clojure_sources excludes test files by default."""
    assert "!*_test.clj" in ClojureSourcesGeneratorSourcesField.default
    assert "!*_test.cljc" in ClojureSourcesGeneratorSourcesField.default
    assert "!test_*.clj" in ClojureSourcesGeneratorSourcesField.default
    assert "!test_*.cljc" in ClojureSourcesGeneratorSourcesField.default


def test_generate_clojure_test_targets(rule_runner: RuleRunner) -> None:
    """Test that clojure_tests generates individual clojure_test targets."""
    rule_runner.write_files(
        {
            "test/clj/BUILD": "clojure_tests(name='tests')\n",
            "test/clj/example_test.clj": "(ns example.core-test)",
            "test/clj/util_test.clj": "(ns example.util-test)",
        }
    )
    rule_runner.set_options(
        [
            f"--jvm-resolves={repr(_JVM_RESOLVES)}",
            "--jvm-default-resolve=jvm-default",
        ]
    )

    parametrizations = rule_runner.request(
        _TargetParametrizations,
        [
            _TargetParametrizationsRequest(
                Address("test/clj", target_name="tests"),
                description_of_origin="tests",
            ),
        ],
    )

    generated_targets = {
        t for parametrization in parametrizations for t in parametrization.parametrization.values()
    }

    assert len(generated_targets) == 2
    assert all(isinstance(t, ClojureTestTarget) for t in generated_targets)

    source_files = {t[ClojureTestSourceField].value for t in generated_targets}
    assert source_files == {"example_test.clj", "util_test.clj"}


def test_clojure_test_with_timeout(rule_runner: RuleRunner) -> None:
    """Test that clojure_test respects the timeout field."""
    rule_runner.write_files(
        {"test/clj/BUILD": 'clojure_tests(name="tests", timeout=120)\n', "test/clj/example_test.clj": ""}
    )
    assert_generated(
        rule_runner,
        Address("test/clj", target_name="tests"),
        build_content='clojure_tests(name="tests", timeout=120)\n',
        expected_targets={
            ClojureTestTarget(
                {
                    ClojureTestSourceField.alias: "example_test.clj",
                    ClojureTestTimeoutField.alias: 120,
                },
                Address("test/clj", target_name="tests", relative_file_path="example_test.clj"),
            ),
        },
    )


def test_clojure_test_with_resolve(rule_runner: RuleRunner) -> None:
    """Test that clojure_test respects the resolve field."""
    rule_runner.write_files(
        {"test/clj/BUILD": 'clojure_tests(name="tests", resolve="java17")\n', "test/clj/example_test.clj": ""}
    )
    assert_generated(
        rule_runner,
        Address("test/clj", target_name="tests"),
        build_content='clojure_tests(name="tests", resolve="java17")\n',
        expected_targets={
            ClojureTestTarget(
                {
                    ClojureTestSourceField.alias: "example_test.clj",
                    JvmResolveField.alias: "java17",
                },
                Address("test/clj", target_name="tests", relative_file_path="example_test.clj"),
            ),
        },
    )


def test_clojure_tests_with_dependencies(rule_runner: RuleRunner) -> None:
    """Test that clojure_test respects the dependencies field."""
    rule_runner.write_files(
        {
            "test/clj/BUILD": 'clojure_tests(name="tests", dependencies=["//src:lib"])\n',
            "test/clj/example_test.clj": "",
        }
    )
    assert_generated(
        rule_runner,
        Address("test/clj", target_name="tests"),
        build_content='clojure_tests(name="tests", dependencies=["//src:lib"])\n',
        expected_targets={
            ClojureTestTarget(
                {
                    ClojureTestSourceField.alias: "example_test.clj",
                    JvmDependenciesField.alias: ["//src:lib"],
                },
                Address("test/clj", target_name="tests", relative_file_path="example_test.clj"),
            ),
        },
    )


# -----------------------------------------------------------------------------------------------
# clojure_deploy_jar target type tests
# -----------------------------------------------------------------------------------------------


def test_clojure_deploy_jar_main_field_required() -> None:
    """Test that the main field is required for clojure_deploy_jar."""
    assert ClojureMainNamespaceField.required is True
    assert ClojureMainNamespaceField.alias == "main"


def test_clojure_deploy_jar_target_type_alias() -> None:
    """Test that clojure_deploy_jar has the correct alias."""
    assert ClojureDeployJarTarget.alias == "clojure_deploy_jar"


def test_clojure_deploy_jar_has_required_fields() -> None:
    """Test that clojure_deploy_jar target has all expected fields."""
    field_aliases = {field.alias for field in ClojureDeployJarTarget.core_fields}

    # Core fields that should be present
    assert "main" in field_aliases
    assert "dependencies" in field_aliases
    assert "resolve" in field_aliases
    assert "jdk" in field_aliases
    assert "output_path" in field_aliases
    assert "provided" in field_aliases
    # aot field should NOT be present (removed in simplification)
    assert "aot" not in field_aliases


def test_clojure_deploy_jar_with_minimal_config(rule_runner: RuleRunner) -> None:
    """Test creating a clojure_deploy_jar with minimal configuration."""
    rule_runner.write_files(
        {
            "src/BUILD": dedent(
                """\
                clojure_source(name="core", source="core.clj")

                clojure_deploy_jar(
                    name="app",
                    main="my.app.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/core.clj": "(ns my.app.core (:gen-class))",
        }
    )
    rule_runner.set_options(
        [
            f"--jvm-resolves={repr(_JVM_RESOLVES)}",
            "--jvm-default-resolve=jvm-default",
        ]
    )

    target = rule_runner.get_target(Address("src", target_name="app"))

    # Check that it's the right type
    assert isinstance(target, ClojureDeployJarTarget)

    # Check main field
    assert target[ClojureMainNamespaceField].value == "my.app.core"


def test_clojure_deploy_jar_with_resolve(rule_runner: RuleRunner) -> None:
    """Test creating a clojure_deploy_jar with a specific resolve."""
    rule_runner.write_files(
        {
            "src/BUILD": dedent(
                """\
                clojure_source(name="core", source="core.clj", resolve="java17")

                clojure_deploy_jar(
                    name="app",
                    main="my.app.core",
                    dependencies=[":core"],
                    resolve="java17",
                )
                """
            ),
            "src/core.clj": "(ns my.app.core (:gen-class))",
        }
    )
    rule_runner.set_options(
        [
            f"--jvm-resolves={repr(_JVM_RESOLVES)}",
            "--jvm-default-resolve=jvm-default",
        ]
    )

    target = rule_runner.get_target(Address("src", target_name="app"))

    # Check resolve field
    assert target[JvmResolveField].value == "java17"


def test_clojure_deploy_jar_with_jdk(rule_runner: RuleRunner) -> None:
    """Test creating a clojure_deploy_jar with a specific JDK."""
    rule_runner.write_files(
        {
            "src/BUILD": dedent(
                """\
                clojure_source(name="core", source="core.clj")

                clojure_deploy_jar(
                    name="app",
                    main="my.app.core",
                    dependencies=[":core"],
                    jdk="17",
                )
                """
            ),
            "src/core.clj": "(ns my.app.core (:gen-class))",
        }
    )
    rule_runner.set_options(
        [
            f"--jvm-resolves={repr(_JVM_RESOLVES)}",
            "--jvm-default-resolve=jvm-default",
        ]
    )

    target = rule_runner.get_target(Address("src", target_name="app"))

    # Check JDK field
    assert target[JvmJdkField].value == "17"
