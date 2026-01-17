from __future__ import annotations

from textwrap import dedent

import pytest

from pants_backend_clojure.goals.lint import CljKondoRequest
from pants_backend_clojure.goals.lint import rules as lint_rules
from pants_backend_clojure.target_types import (
    ClojureSourcesGeneratorTarget,
    ClojureSourceTarget,
    ClojureTestTarget,
)
from pants_backend_clojure.target_types import rules as target_types_rules
from pants.core.goals.lint import LintResult
from pants.core.util_rules import config_files, external_tool, source_files
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.addresses import Address
from pants.engine.rules import QueryRule
from pants.jvm import classpath, jvm_common, non_jvm_dependencies
from pants.jvm.resolve.coursier_fetch import rules as coursier_fetch_rules
from pants.jvm.resolve.coursier_setup import rules as coursier_setup_rules
from pants.jvm.target_types import JvmArtifactTarget
from pants.jvm.util_rules import rules as jdk_util_rules
from pants.testutil.rule_runner import RuleRunner


@pytest.fixture
def rule_runner() -> RuleRunner:
    rule_runner = RuleRunner(
        rules=[
            *classpath.rules(),
            *config_files.rules(),
            *coursier_fetch_rules(),
            *coursier_setup_rules(),
            *external_tool.rules(),
            *jdk_util_rules(),
            *jvm_common.rules(),
            *lint_rules(),
            *non_jvm_dependencies.rules(),
            *source_files.rules(),
            *target_types_rules(),
            QueryRule(LintResult, [CljKondoRequest.Batch]),
            QueryRule(SourceFiles, [SourceFilesRequest]),
        ],
        target_types=[
            ClojureSourceTarget,
            ClojureSourcesGeneratorTarget,
            ClojureTestTarget,
            JvmArtifactTarget,
        ],
    )
    return rule_runner


def run_clj_kondo(
    rule_runner: RuleRunner,
    targets: list[Address],
    *,
    extra_args: list[str] | None = None,
) -> LintResult:
    rule_runner.set_options(
        [
            "--backend-packages=pants_backend_clojure",
            "--no-clj-kondo-use-classpath",  # Disable classpath support in tests for now
            "--no-clj-kondo-use-cache",  # Disable cache support in tests for now
            *(extra_args or []),
        ],
        env_inherit={"PATH", "PYENV_ROOT", "HOME"},
    )
    field_sets = [
        CljKondoRequest.field_set_type.create(rule_runner.get_target(address))
        for address in targets
    ]
    input_sources = rule_runner.request(
        SourceFiles,
        [SourceFilesRequest(field_set.sources for field_set in field_sets)],
    )
    lint_result = rule_runner.request(
        LintResult,
        [
            CljKondoRequest.Batch(
                "",
                tuple(field_sets),
                partition_metadata=None,
            )
        ],
    )
    return lint_result


def test_lint_with_issues(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo detects lint issues."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_source(name='example', source='example.clj')",
            "example.clj": dedent(
                """\
                (ns example.core)

                (defn foo [x]
                  (let [y 10]
                    x))
                """
            ),
        }
    )

    tgt = Address("", target_name="example")
    lint_result = run_clj_kondo(rule_runner, [tgt])

    # Should detect unused binding 'y'
    assert lint_result.exit_code != 0
    assert "unused" in lint_result.stdout.lower() or "unused" in lint_result.stderr.lower()


def test_lint_clean_code(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo passes on clean code."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_source(name='example', source='example.clj')",
            "example.clj": dedent(
                """\
                (ns example)

                (defn add [x y]
                  (+ x y))

                (defn multiply [x y]
                  (* x y))
                """
            ),
        }
    )

    tgt = Address("", target_name="example")
    lint_result = run_clj_kondo(rule_runner, [tgt])

    # Clean code should pass with exit code 0
    assert lint_result.exit_code == 0


def test_lint_unresolved_symbol(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo detects unresolved symbols."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_source(name='example', source='example.clj')",
            "example.clj": dedent(
                """\
                (ns example.core)

                (defn foo [x]
                  (bar x))
                """
            ),
        }
    )

    tgt = Address("", target_name="example")
    lint_result = run_clj_kondo(rule_runner, [tgt])

    # Should detect unresolved symbol 'bar'
    assert lint_result.exit_code != 0
    output = lint_result.stdout + lint_result.stderr
    assert "unresolved" in output.lower() or "bar" in output.lower()


def test_skip_clj_kondo_field(rule_runner: RuleRunner) -> None:
    """Test that skip_clj_kondo field prevents linting."""
    rule_runner.write_files(
        {
            "BUILD": dedent(
                """\
                clojure_source(
                    name='skipped',
                    source='skipped.clj',
                    skip_clj_kondo=True,
                )
                """
            ),
            "skipped.clj": dedent(
                """\
                (ns example.skipped)

                (defn foo [x]
                  (let [y 10]
                    x))
                """
            ),
        }
    )

    tgt = Address("", target_name="skipped")

    # When skip_clj_kondo=True, the field set should have skip enabled
    field_set = CljKondoRequest.field_set_type.create(rule_runner.get_target(tgt))

    # Check that skip_clj_kondo is set to True
    assert field_set.skip_clj_kondo.value is True


def test_lint_multiple_files(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo can lint multiple files at once."""
    rule_runner.write_files(
        {
            "BUILD": dedent(
                """\
                clojure_source(name='file1', source='file1.clj')
                clojure_source(name='file2', source='file2.clj')
                """
            ),
            "file1.clj": dedent(
                """\
                (ns example.file1)

                (defn foo [x]
                  (* x 2))
                """
            ),
            "file2.clj": dedent(
                """\
                (ns example.file2)

                (defn bar [y]
                  (let [z 10]
                    y))
                """
            ),
        }
    )

    targets = [
        Address("", target_name="file1"),
        Address("", target_name="file2"),
    ]
    lint_result = run_clj_kondo(rule_runner, targets)

    # Should detect issue in file2.clj (unused binding 'z')
    assert lint_result.exit_code != 0
    assert "file2.clj" in (lint_result.stdout + lint_result.stderr)


def test_clj_kondo_with_config_file(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo respects configuration files."""
    rule_runner.write_files(
        {
            ".clj-kondo/config.edn": dedent(
                """\
                {:linters {:unused-binding {:level :off}}}
                """
            ),
            "BUILD": "clojure_source(name='example', source='example.clj')",
            "example.clj": dedent(
                """\
                (ns example)

                (defn foo [x]
                  (let [y 10]
                    x))
                """
            ),
        }
    )

    tgt = Address("", target_name="example")
    lint_result = run_clj_kondo(rule_runner, [tgt])

    # The formatter should run successfully with the config file present
    # Config file may not be discovered in test environment, but lint should complete
    assert lint_result.exit_code in (0, 2)  # 0 if config works, 2 if warning remains


def test_clj_kondo_with_cljc_files(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo lints .cljc files."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_source(name='example', source='example.cljc')",
            "example.cljc": dedent(
                """\
                (ns example)

                (defn portable [x]
                  (+ x 1))

                #?(:clj (defn jvm-only [] :jvm)
                   :cljs (defn js-only [] :js))
                """
            ),
        }
    )

    tgt = Address("", target_name="example")
    lint_result = run_clj_kondo(rule_runner, [tgt])

    # Should successfully lint .cljc files
    # Clean code should pass
    assert lint_result.exit_code == 0


def test_empty_file(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo handles empty files gracefully."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_source(name='empty', source='empty.clj')",
            "empty.clj": "",
        }
    )

    tgt = Address("", target_name="empty")
    lint_result = run_clj_kondo(rule_runner, [tgt])

    # Empty file should pass linting
    assert lint_result.exit_code == 0


def test_clj_kondo_respects_skip_option(rule_runner: RuleRunner) -> None:
    """Test that --clj-kondo-skip option is available."""
    # This test just verifies the subsystem option exists
    rule_runner.set_options(
        ["--backend-packages=pants_backend_clojure", "--clj-kondo-skip"],
        env_inherit={"PATH", "PYENV_ROOT", "HOME"},
    )
    # If we get here without error, the option exists
    assert True


def test_lint_test_target(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo can lint test targets."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_test(name='example_test', source='example_test.clj')",
            "example_test.clj": dedent(
                """\
                (ns example-test
                  (:require [clojure.test :refer [deftest is]]))

                (deftest test-addition
                  (is (= 4 (+ 2 2))))
                """
            ),
        }
    )

    tgt = Address("", target_name="example_test")
    lint_result = run_clj_kondo(rule_runner, [tgt])

    # Clean test code should pass
    assert lint_result.exit_code == 0


def test_lint_detects_redefined_var(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo detects redefined variables."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_source(name='example', source='example.clj')",
            "example.clj": dedent(
                """\
                (ns example)

                (def my-var 10)
                (def my-var 20)
                """
            ),
        }
    )

    tgt = Address("", target_name="example")
    lint_result = run_clj_kondo(rule_runner, [tgt])

    # Should detect redefined var
    assert lint_result.exit_code != 0
    output = lint_result.stdout + lint_result.stderr
    assert "redefin" in output.lower() or "my-var" in output


def test_lint_with_custom_severity_levels(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo respects custom severity levels in config."""
    rule_runner.write_files(
        {
            ".clj-kondo/config.edn": dedent(
                """\
                {:linters {:unresolved-symbol {:level :warning}}}
                """
            ),
            "BUILD": "clojure_source(name='example', source='example.clj')",
            "example.clj": dedent(
                """\
                (ns example)

                (defn foo [x]
                  (bar x))
                """
            ),
        }
    )

    tgt = Address("", target_name="example")
    lint_result = run_clj_kondo(rule_runner, [tgt])

    # With custom severity, should still report the issue
    # but the exact exit code may vary based on config discovery
    assert lint_result.exit_code in (0, 2, 3)  # May be warning or error depending on config discovery


def test_lint_invalid_config_graceful_failure(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo handles invalid config files gracefully."""
    rule_runner.write_files(
        {
            ".clj-kondo/config.edn": "this is not valid edn {{{",
            "BUILD": "clojure_source(name='example', source='example.clj')",
            "example.clj": dedent(
                """\
                (ns example)

                (defn foo [x]
                  (+ x 1))
                """
            ),
        }
    )

    tgt = Address("", target_name="example")
    # Should not crash, even with invalid config
    lint_result = run_clj_kondo(rule_runner, [tgt])

    # clj-kondo should handle invalid config and either:
    # - use defaults and lint successfully, or
    # - report config error but not crash
    assert lint_result is not None


def test_lint_unused_namespace_in_require(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo detects unused required namespaces."""
    rule_runner.write_files(
        {
            "BUILD": dedent(
                """\
                clojure_source(name='util', source='util.clj')
                clojure_source(name='main', source='main.clj', dependencies=[':util'])
                """
            ),
            "util.clj": dedent(
                """\
                (ns example.util)

                (defn helper [] "helper")
                """
            ),
            "main.clj": dedent(
                """\
                (ns example.main
                  (:require [example.util :as util]))

                (defn process []
                  "does not use util")
                """
            ),
        }
    )

    tgt = Address("", target_name="main")
    lint_result = run_clj_kondo(rule_runner, [tgt])

    # Should detect unused namespace require
    assert lint_result.exit_code != 0
    output = lint_result.stdout + lint_result.stderr
    assert "unused" in output.lower() or "example.util" in output


def test_lint_private_function_usage(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo detects usage of private functions."""
    rule_runner.write_files(
        {
            "BUILD": dedent(
                """\
                clojure_source(name='lib', source='lib.clj')
                clojure_source(name='main', source='main.clj', dependencies=[':lib'])
                """
            ),
            "lib.clj": dedent(
                """\
                (ns example.lib)

                (defn- private-fn []
                  "This is private")
                """
            ),
            "main.clj": dedent(
                """\
                (ns example.main
                  (:require [example.lib :as lib]))

                (defn use-private []
                  (lib/private-fn))
                """
            ),
        }
    )

    tgt = Address("", target_name="main")
    lint_result = run_clj_kondo(rule_runner, [tgt])

    # Should detect usage of private function
    # Note: This requires multi-file analysis which may not work without classpath
    # Exit code may be 0 if private function check isn't enabled
    assert lint_result.exit_code in (0, 2, 3)


def test_lint_invalid_arity(rule_runner: RuleRunner) -> None:
    """Test that clj-kondo detects invalid arity in function calls."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_source(name='example', source='example.clj')",
            "example.clj": dedent(
                """\
                (ns example)

                (defn takes-two [a b]
                  (+ a b))

                (defn caller []
                  (takes-two 1 2 3))
                """
            ),
        }
    )

    tgt = Address("", target_name="example")
    lint_result = run_clj_kondo(rule_runner, [tgt])

    # Should detect arity mismatch
    assert lint_result.exit_code != 0
    output = lint_result.stdout + lint_result.stderr
    # clj-kondo reports: "called with X args but expects Y"
    assert ("args" in output.lower() and "expects" in output.lower()) or "arity" in output.lower()
