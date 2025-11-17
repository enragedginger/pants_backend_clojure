from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from clojure_backend.target_types import (
    ClojureSourceField,
    ClojureTestExtraEnvVarsField,
    ClojureTestFieldSet,
    ClojureTestSourceField,
    ClojureTestTimeoutField,
)
from pants.option.option_types import SkipOption
from pants.option.subsystem import Subsystem
from pants.core.goals.test import (
    TestDebugRequest,
    TestFieldSet,
    TestRequest,
    TestResult,
    TestSubsystem,
)
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.addresses import Addresses
from pants.engine.env_vars import EnvironmentVars, EnvironmentVarsRequest
from pants.engine.fs import Digest, DigestContents, MergeDigests, PathGlobs
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.process import (
    InteractiveProcess,
    Process,
    ProcessCacheScope,
    ProcessResultWithRetries,
    ProcessWithRetries,
)
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import FieldSet, SourcesField, TransitiveTargets, TransitiveTargetsRequest
from pants.engine.unions import UnionRule
from pants.jvm.classpath import classpath as classpath_get
from pants.jvm.jdk_rules import JdkEnvironment, JdkRequest, JvmProcess
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.target_types import JvmDependenciesField, JvmJdkField
from pants.util.logging import LogLevel


class ClojureTestSubsystem(Subsystem):
    options_scope = "clojure-test-runner"
    name = "Clojure test"
    help = "Clojure test runner (clojure.test)"

    skip = SkipOption("test")


# ClojureTestFieldSet is now defined in target_types.py to avoid circular dependencies
# and allow both the test runner and compiler to use the same field set definition


class ClojureTestRequest(TestRequest):
    tool_subsystem = ClojureTestSubsystem
    field_set_type = ClojureTestFieldSet
    supports_debug = True


@dataclass(frozen=True)
class TestSetupRequest:
    field_set: ClojureTestFieldSet
    is_debug: bool


@dataclass(frozen=True)
class TestSetup:
    process: JvmProcess
    reports_dir: str


@rule(level=LogLevel.DEBUG)
async def setup_clojure_test_for_target(
    request: TestSetupRequest,
    jvm: JvmSubsystem,
    test_subsystem: TestSubsystem,
) -> TestSetup:
    # Prepare JDK and get transitive targets
    jdk_request = JdkRequest.from_field(request.field_set.jdk_version)
    transitive_targets_request = TransitiveTargetsRequest([request.field_set.address])

    jdk, transitive_targets = await MultiGet(
        Get(JdkEnvironment, JdkRequest, jdk_request),
        Get(TransitiveTargets, TransitiveTargetsRequest, transitive_targets_request),
    )

    # Get classpath for all transitive dependencies, not just the test target
    classpath = await classpath_get(
        **implicitly(Addresses(tgt.address for tgt in transitive_targets.closure))
    )

    # Get test source file to parse namespace
    test_source_files = await Get(
        SourceFiles,
        SourceFilesRequest([request.field_set.sources]),
    )

    # Extract test namespace from source file
    test_file_path = test_source_files.files[0]
    digest_contents = await Get(DigestContents, Digest, test_source_files.snapshot.digest)
    content = digest_contents[0].content.decode("utf-8")
    match = re.search(r"\(ns\s+([a-z0-9\-_.]+)", content, re.MULTILINE)
    if not match:
        raise ValueError(
            f"Could not find namespace declaration in {test_file_path}.\n\n"
            f"Common causes:\n"
            f"  - Missing or malformed (ns ...) declaration\n"
            f"  - Namespace declaration not at the top of the file\n"
            f"  - Invalid characters in namespace name\n\n"
            f"Expected format: (ns my-namespace-name)\n\n"
            f"Troubleshooting:\n"
            f"  1. Ensure the file starts with a valid (ns ...) form\n"
            f"  2. Check for syntax errors: pants check {field_set.address}\n"
            f"  3. Verify namespace follows Clojure naming conventions\n"
        )
    test_namespace = match.group(1)

    # Get all source files (both production and test code)
    all_source_files = await Get(
        SourceFiles,
        SourceFilesRequest(
            (tgt.get(SourcesField) for tgt in transitive_targets.closure),
            for_sources_types=(ClojureSourceField, ClojureTestSourceField),
            enable_codegen=False,
        ),
    )

    # Merge classpath JARs with all source files
    input_digest = await Get(
        Digest,
        MergeDigests([*classpath.digests(), all_source_files.snapshot.digest]),
    )

    # Get environment variables
    field_set_extra_env = await Get(
        EnvironmentVars,
        EnvironmentVarsRequest(request.field_set.extra_env_vars.value or ()),
    )

    # Output directory for test results (for future XML reports)
    reports_dir = f"__reports/{request.field_set.address.path_safe_spec}"

    # Cache test runs only if successful, or not at all if --test-force
    cache_scope = (
        ProcessCacheScope.PER_SESSION
        if test_subsystem.force
        else ProcessCacheScope.SUCCESSFUL
    )

    # Extra JVM args for debug mode
    extra_jvm_args: list[str] = []
    if request.is_debug:
        extra_jvm_args.extend(jvm.debug_args)

    # Clojure test runner command
    # We'll use clojure.main to load and run tests
    test_runner_code = (
        "(require 'clojure.test) "
        f"(require '{test_namespace}) "
        f"(let [result (clojure.test/run-tests '{test_namespace})] "
        "(System/exit (if (clojure.test/successful? result) 0 1)))"
    )

    process = JvmProcess(
        jdk=jdk,
        classpath_entries=[".", *classpath.args()],
        argv=[
            *extra_jvm_args,
            "clojure.main",
            "-e",
            test_runner_code,
        ],
        input_digest=input_digest,
        extra_env=field_set_extra_env,
        extra_jvm_options=(),
        extra_nailgun_keys=(),
        output_directories=(reports_dir,),
        output_files=(),
        description=f"Run clojure.test for {request.field_set.address}",
        timeout_seconds=request.field_set.timeout.calculate_from_global_options(test_subsystem),
        level=LogLevel.DEBUG,
        cache_scope=cache_scope,
        use_nailgun=False,
    )

    return TestSetup(process=process, reports_dir=reports_dir)


@rule(desc="Run Clojure tests", level=LogLevel.DEBUG)
async def run_clojure_test(
    test_subsystem: TestSubsystem,
    batch: ClojureTestRequest.Batch[ClojureTestFieldSet, Any],
) -> TestResult:
    field_set = batch.single_element

    # Setup test process
    test_setup = await Get(TestSetup, TestSetupRequest, TestSetupRequest(field_set, is_debug=False))

    # Convert JvmProcess to Process
    process = await Get(Process, JvmProcess, test_setup.process)

    # Execute with retry support
    process_results = await Get(
        ProcessResultWithRetries,
        ProcessWithRetries(process, test_subsystem.attempts_default),
    )

    return TestResult.from_fallible_process_result(
        process_results=process_results.results,
        address=field_set.address,
        output_setting=test_subsystem.output,
    )


@rule(level=LogLevel.DEBUG)
async def setup_clojure_test_debug_request(
    batch: ClojureTestRequest.Batch[ClojureTestFieldSet, Any],
) -> TestDebugRequest:
    setup = await Get(
        TestSetup,
        TestSetupRequest,
        TestSetupRequest(batch.single_element, is_debug=True),
    )
    process = await Get(Process, JvmProcess, setup.process)

    return TestDebugRequest(
        InteractiveProcess.from_process(
            process,
            forward_signals_to_process=False,
            restartable=True,
        )
    )


def rules():
    return [
        *collect_rules(),
        *ClojureTestRequest.rules(),
    ]
