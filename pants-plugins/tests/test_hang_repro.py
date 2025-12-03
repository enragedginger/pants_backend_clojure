"""Minimal reproduction of test hang issue.

This test reproduces the hang that occurs when tests run for too long.
The hang is triggered by specific combinations of:
1. clojure_source depending on jvm_artifact(clojure)
2. Using check or AOT compile goals

FINDINGS:
=========
The hang is reproduced by this test. After investigation, a potential root cause
was identified in check.py and aot_compile.py:

PROBLEMATIC PATTERN (check.py:189, aot_compile.py:162):
    result = await Get(FallibleProcessResult, Process, await Get(Process, JvmProcess, jvm_process))

This nested `await Get()` violates the Pants Engine's async patterns. The scheduler
may deadlock waiting for the nested Get to complete while holding locks.

CORRECT PATTERN (used in test.py:199-202):
    process = await Get(Process, JvmProcess, test_setup.process)
    process_results = await Get(ProcessResultWithRetries, ProcessWithRetries(process, ...))

The test runner (test.py) works correctly because it uses sequential Get() calls
instead of nested ones.

NEXT STEP:
Fix the nested await Get() pattern in:
- pants-plugins/clojure_backend/aot_compile.py:162
- pants-plugins/clojure_backend/goals/check.py:189
"""

from __future__ import annotations

from textwrap import dedent

import pytest

from clojure_backend.goals.check import ClojureCheckFieldSet, ClojureCheckRequest
from clojure_backend.goals import check as check_goal
from clojure_backend.target_types import (
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


# Lockfile with Clojure 1.11.0 and correct fingerprints
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


@pytest.fixture
def rule_runner() -> RuleRunner:
    """Create a minimal rule runner for hang reproduction."""
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
            JvmArtifactTarget,
        ],
    )
    return rule_runner


_JVM_RESOLVES = {
    "jvm-default": "3rdparty/jvm/default.lock",
}


def test_check_with_clojure_dependency(rule_runner: RuleRunner) -> None:
    """Minimal test that reproduces the hang.

    This test:
    1. Creates a clojure_source that depends on jvm_artifact(clojure)
    2. Runs the check goal on that source

    This is the minimal reproduction of the hang issue.
    """
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
            "BUILD": 'clojure_source(name="example", source="example.clj", dependencies=["3rdparty/jvm:org.clojure_clojure"])',
            "example.clj": dedent(
                """\
                (ns example)
                (defn greet [name]
                  (str "Hello, " name))
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
        Address(spec_path="", target_name="example")
    )
    field_set = ClojureCheckFieldSet.create(tgt)

    # This is where the hang occurs
    results = rule_runner.request(
        CheckResults,
        [ClojureCheckRequest([field_set])],
    )

    assert len(results.results) == 1
    assert results.results[0].exit_code == 0
