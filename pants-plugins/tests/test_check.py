from __future__ import annotations

from textwrap import dedent

import pytest

from clojure_backend.goals.check import ClojureCheckFieldSet, ClojureCheckRequest
from clojure_backend.goals import check as check_goal
from clojure_backend.target_types import (
    ClojureSourcesGeneratorTarget,
    ClojureSourceTarget,
)
from clojure_backend.target_types import rules as target_types_rules
from clojure_backend import compile_clj
from pants.core.goals.check import CheckResults
from pants.core.util_rules import config_files, source_files, stripped_source_files, system_binaries
from pants.engine.addresses import Address
from pants.jvm import classpath, jvm_common, non_jvm_dependencies
from pants.jvm.goals import lockfile
from pants.jvm.resolve.coursier_fetch import rules as coursier_fetch_rules
from pants.jvm.resolve.coursier_setup import rules as coursier_setup_rules
from pants.jvm.target_types import JvmArtifactTarget
from pants.jvm.util_rules import rules as jdk_util_rules
from pants.testutil.rule_runner import PYTHON_BOOTSTRAP_ENV, QueryRule, RuleRunner


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
            *jdk_util_rules(),
            *jvm_common.rules(),
            *non_jvm_dependencies.rules(),
            *source_files.rules(),
            *stripped_source_files.rules(),
            *system_binaries.rules(),
            *target_types_rules(),
            *check_goal.rules(),
            *lockfile.rules(),
            QueryRule(CheckResults, [ClojureCheckRequest]),
        ],
        target_types=[
            ClojureSourceTarget,
            ClojureSourcesGeneratorTarget,
            JvmArtifactTarget,
        ],
    )
    return rule_runner


_JVM_RESOLVES = {
    "jvm-default": "3rdparty/jvm/default.lock",
}


# Lockfile with Clojure for check tests
# Now that we rely on the user's classpath to provide Clojure (instead of fetching
# it via ToolClasspathRequest), the tests need Clojure in the lockfile.
# Using version 1.11.0 with correct fingerprints.
LOCKFILE_WITH_CLOJURE = """\
# --- BEGIN PANTS LOCKFILE METADATA: DO NOT EDIT OR REMOVE ---
# {
#   "version": 1,
#   "generated_with_requirements": [
#     "org.clojure:clojure:1.11.0,url=not_provided,jar=not_provided"
#   ]
# }
# --- END PANTS LOCKFILE METADATA ---

[[entries]]
file_name = "org.clojure_clojure_1.11.0.jar"
[[entries.directDependencies]]
group = "org.clojure"
artifact = "core.specs.alpha"
version = "0.2.62"
packaging = "jar"

[[entries.directDependencies]]
group = "org.clojure"
artifact = "spec.alpha"
version = "0.3.218"
packaging = "jar"

[[entries.dependencies]]
group = "org.clojure"
artifact = "core.specs.alpha"
version = "0.2.62"
packaging = "jar"

[[entries.dependencies]]
group = "org.clojure"
artifact = "spec.alpha"
version = "0.3.218"
packaging = "jar"


[entries.coord]
group = "org.clojure"
artifact = "clojure"
version = "1.11.0"
packaging = "jar"
[entries.file_digest]
fingerprint = "3e21fa75a07ec9ddbbf1b2b50356cf180710d0398deaa4f44e91cd6304555947"
serialized_bytes_length = 4105010

[[entries]]
file_name = "org.clojure_core.specs.alpha_0.2.62.jar"
[[entries.directDependencies]]
group = "org.clojure"
artifact = "clojure"
version = "1.11.0"
packaging = "jar"

[[entries.dependencies]]
group = "org.clojure"
artifact = "clojure"
version = "1.11.0"
packaging = "jar"


[entries.coord]
group = "org.clojure"
artifact = "core.specs.alpha"
version = "0.2.62"
packaging = "jar"
[entries.file_digest]
fingerprint = "06eea8c070bbe45c158567e443439681bc8c46e9123414f81bfa32ba42d6cbc8"
serialized_bytes_length = 4325

[[entries]]
file_name = "org.clojure_spec.alpha_0.3.218.jar"
[[entries.directDependencies]]
group = "org.clojure"
artifact = "clojure"
version = "1.11.0"
packaging = "jar"

[[entries.dependencies]]
group = "org.clojure"
artifact = "clojure"
version = "1.11.0"
packaging = "jar"


[entries.coord]
group = "org.clojure"
artifact = "spec.alpha"
version = "0.3.218"
packaging = "jar"
[entries.file_digest]
fingerprint = "67ec898eb55c66a957a55279dd85d1376bb994bd87668b2b0de1eb3b97e8aae0"
serialized_bytes_length = 635617
"""


def run_clojure_check(
    rule_runner: RuleRunner,
    target_name: str,
    relative_file_path: str,
    *,
    extra_args: list[str] | None = None,
) -> CheckResults:
    args = [
        f"--jvm-resolves={repr(_JVM_RESOLVES)}",
        "--jvm-default-resolve=jvm-default",
        *(extra_args or []),
    ]
    rule_runner.set_options(args, env_inherit=PYTHON_BOOTSTRAP_ENV)
    tgt = rule_runner.get_target(
        Address(spec_path="", target_name=target_name, relative_file_path=relative_file_path)
    )
    field_set = ClojureCheckFieldSet.create(tgt)
    return rule_runner.request(
        CheckResults,
        [ClojureCheckRequest([field_set])],
    )


def test_check_valid_clojure_code(rule_runner: RuleRunner) -> None:
    """Test that valid Clojure code passes check."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="org.clojure_clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="1.11.0",
                )
                """
            ),
            "3rdparty/jvm/default.lock": LOCKFILE_WITH_CLOJURE,
            "BUILD": 'clojure_sources(dependencies=["3rdparty/jvm:org.clojure_clojure"])',
            "example.clj": dedent(
                """\
                (ns example)
                (defn greet [name]
                  (str "Hello, " name))
                """
            ),
        }
    )

    results = run_clojure_check(rule_runner, "", "example.clj")
    assert len(results.results) == 1
    assert results.results[0].exit_code == 0


def test_check_syntax_error(rule_runner: RuleRunner) -> None:
    """Test that syntax errors cause check to fail."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="org.clojure_clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="1.11.0",
                )
                """
            ),
            "3rdparty/jvm/default.lock": LOCKFILE_WITH_CLOJURE,
            "BUILD": 'clojure_sources(dependencies=["3rdparty/jvm:org.clojure_clojure"])',
            "bad.clj": dedent(
                """\
                (ns bad)
                (defn broken [x y
                """
            ),
        }
    )

    results = run_clojure_check(rule_runner, "", "bad.clj")
    assert len(results.results) == 1
    assert results.results[0].exit_code != 0
    output = results.results[0].stdout + results.results[0].stderr
    assert "Failed to load" in output or "EOF" in output


def test_check_undefined_symbol(rule_runner: RuleRunner) -> None:
    """Test that undefined symbols cause check to fail."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="org.clojure_clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="1.11.0",
                )
                """
            ),
            "3rdparty/jvm/default.lock": LOCKFILE_WITH_CLOJURE,
            "BUILD": 'clojure_sources(dependencies=["3rdparty/jvm:org.clojure_clojure"])',
            "undef.clj": dedent(
                """\
                (ns undef)
                (defn foo [] (unknown-function 42))
                """
            ),
        }
    )

    results = run_clojure_check(rule_runner, "", "undef.clj")
    assert len(results.results) == 1
    assert results.results[0].exit_code != 0


def test_check_java_interop(rule_runner: RuleRunner) -> None:
    """Test that Java interop works in check."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="org.clojure_clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="1.11.0",
                )
                """
            ),
            "3rdparty/jvm/default.lock": LOCKFILE_WITH_CLOJURE,
            "BUILD": 'clojure_sources(dependencies=["3rdparty/jvm:org.clojure_clojure"])',
            "java_interop.clj": dedent(
                """\
                (ns java-interop
                  (:import [java.util ArrayList HashMap]))

                (defn make-list [] (ArrayList.))
                (defn make-map [] (HashMap.))
                """
            ),
        }
    )

    results = run_clojure_check(rule_runner, "", "java_interop.clj")
    assert len(results.results) == 1
    assert results.results[0].exit_code == 0


def test_check_skip_option(rule_runner: RuleRunner) -> None:
    """Test that --clojure-check-skip skips checking."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="org.clojure_clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="1.11.0",
                )
                """
            ),
            "3rdparty/jvm/default.lock": LOCKFILE_WITH_CLOJURE,
            "BUILD": 'clojure_sources(dependencies=["3rdparty/jvm:org.clojure_clojure"])',
            "bad.clj": "(ns bad) (defn broken",
        }
    )

    # With skip, even broken code should pass (no results)
    results = run_clojure_check(
        rule_runner,
        "",
        "bad.clj",
        extra_args=["--clojure-check-skip"],
    )
    # When skipped, there should be no results
    assert len(results.results) == 0


def test_check_detects_arity_mismatch(rule_runner: RuleRunner) -> None:
    """Test that check detects function calls with wrong arity."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="org.clojure_clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="1.11.0",
                )
                """
            ),
            "3rdparty/jvm/default.lock": LOCKFILE_WITH_CLOJURE,
            "BUILD": 'clojure_sources(dependencies=["3rdparty/jvm:org.clojure_clojure"])',
            "arity_error.clj": dedent(
                """\
                (ns arity-error)

                (defn takes-two-args [x y]
                  (+ x y))

                ; Call with wrong number of arguments
                (takes-two-args 1 2 3)
                """
            ),
        }
    )

    results = run_clojure_check(rule_runner, "", "arity_error.clj")

    # Should fail due to arity mismatch
    assert results.results[0].exit_code != 0


def test_check_with_macro_usage(rule_runner: RuleRunner) -> None:
    """Test checking code that uses macros."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="org.clojure_clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="1.11.0",
                )
                """
            ),
            "3rdparty/jvm/default.lock": LOCKFILE_WITH_CLOJURE,
            "BUILD": 'clojure_sources(dependencies=["3rdparty/jvm:org.clojure_clojure"])',
            "macros.clj": dedent(
                """\
                (ns macros)

                ; Use core macros
                (defn process-items [items]
                  (when (seq items)
                    (map inc items)))
                """
            ),
        }
    )

    results = run_clojure_check(rule_runner, "", "macros.clj")

    # Should pass - valid macro usage
    assert results.results[0].exit_code == 0
