from __future__ import annotations

from textwrap import dedent

import pytest

from clojure_backend.target_types import (
    ClojureSourceField,
    ClojureSourcesGeneratorSourcesField,
    ClojureSourcesGeneratorTarget,
    ClojureSourceTarget,
)
from clojure_backend.target_types import rules as target_types_rules
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
        target_types=[ClojureSourceTarget, ClojureSourcesGeneratorTarget],
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
    assert ClojureSourcesGeneratorSourcesField.default == ("*.clj", "*.cljc")


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
    assert_generated(
        rule_runner,
        Address("src/clj", target_name="lib", relative_file_path="example.clj"),
        build_content=dedent(
            """\
            clojure_sources(
                name="lib",
                resolve="java17",
            )
            """
        ),
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
    assert_generated(
        rule_runner,
        Address("src/clj", target_name="lib", relative_file_path="example.clj"),
        build_content=dedent(
            """\
            clojure_sources(
                name="lib",
                jdk="17",
            )
            """
        ),
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
    assert_generated(
        rule_runner,
        Address("src/clj", target_name="lib", relative_file_path="example.clj"),
        build_content=dedent(
            """\
            clojure_sources(
                name="lib",
                dependencies=["//3rdparty/jvm:clojure"],
            )
            """
        ),
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
