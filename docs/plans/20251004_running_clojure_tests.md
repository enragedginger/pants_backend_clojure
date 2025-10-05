# Plan: Running Clojure Tests with clojure.test

**Date:** 2025-10-04
**Status:** Planning
**Based on:** JUnit and Scalatest implementations in Pants, Clojure CLI and compilation research

## Overview

Implement test execution for `clojure_test` targets using the clojure.test framework. This requires:

1. Compiling Clojure source and test code to JVM bytecode
2. Building classpaths with dependencies
3. Running tests via Clojure's test runner
4. Parsing test results

## Research Findings

### Clojure Compilation

From https://clojure.org/reference/compilation:

- **Dynamic compilation**: Clojure compiles to JVM bytecode at runtime by default
- **AOT compilation**: Optional ahead-of-time compilation via `(compile 'namespace-name)`
- **Compilation output**: Each namespace produces:
  - Loader class with `__init` suffix
  - Classfiles for each function
  - Stub classfiles for gen-class definitions
- **Requirements**: Compilation needs a `classes` directory and proper classpath

**Key insight**: We don't necessarily need AOT compilation for tests. We can use Clojure's built-in runtime compilation.

### Clojure CLI Tool

From https://clojure.org/reference/clojure_cli:

- **Purpose**: Command-line tool for running Clojure programs on the JVM
- **Requirements**: Java 8 or higher (we already manage JDKs via Coursier)
- **Classpath management**: Uses `deps.edn` files for dependency configuration
- **Execution modes**:
  - `-M`: Run a main namespace (what we'll use for tests)
  - `-X`: Execute a function
  - `-T`: Run a tool

**Important**: The Clojure CLI is a native binary (not a JAR), similar to how `go` or `terraform` work.

### Test Execution Approach

After analyzing JUnit and Scalatest implementations, we have two main options:

#### Option A: Use Clojure CLI (Recommended)
- Install Clojure CLI as an external tool (like protoc or terraform)
- Run tests via: `clojure -M -e "(require 'clojure.test) (clojure.test/run-tests ...)"`
- Classpath managed by Pants (not deps.edn)

**Pros**:
- More "Clojure-native" approach
- Simpler - leverages existing tooling
- Better error messages and stack traces
- No AOT compilation needed

**Cons**:
- Requires Clojure CLI to be installed
- Need to manage platform-specific binaries

#### Option B: Direct JVM Execution (Like JUnit/Scalatest)
- Use `clojure.main` as entry point directly with `java`
- Run via: `java -cp <classpath> clojure.main -e "(require 'clojure.test) ..."`
- Only requires Clojure JAR in resolve

**Pros**:
- No external tool installation
- Consistent with other JVM test frameworks in Pants
- User provides Clojure version in their resolve

**Cons**:
- Less idiomatic for Clojure users
- Requires proper classpath construction

**Decision**: Use **Option B (Direct JVM Execution)** because:
1. Consistent with JUnit/Scalatest patterns in Pants
2. No external tool installation required
3. User controls Clojure version via their resolve
4. Simpler implementation - reuses JVM infrastructure
5. The Clojure JAR includes `clojure.main` and `clojure.test` built-in

### Tool Installation Pattern

Based on research of Pants tool management:

**For Clojure JAR (our approach - Option B)**:
- **Pattern**: User-provided via resolve (like Scala compiler)
- **Installation**: User adds `org.clojure:clojure` to their resolve
- **No subsystem needed**: Clojure JAR comes from the test target's resolve
- **Version**: User controls via their lockfile

**Alternative (Option A - if we used Clojure CLI)**:
- **Pattern**: TemplatedExternalTool (like protoc, terraform, coursier)
- **Installation**: Automatic download from GitHub releases
- **Platform support**: linux_x86_64, linux_arm64, macos_x86_64, macos_arm64
- **Example URLs**: `https://github.com/clojure/brew-install/releases/download/1.11.1.1429/clojure-tools-1.11.1.1429.tar.gz`

## Implementation Plan

### Phase 1: Test Runner Implementation

**File**: `pants-plugins/clojure_backend/test_runner.py`

#### Step 1.1: Define Field Set and Test Request

```python
from __future__ import annotations

from dataclasses import dataclass

from clojure_backend.target_types import (
    ClojureTestSourceField,
    ClojureTestTimeoutField,
    ClojureTestExtraEnvVarsField,
)
from pants.core.goals.test import TestFieldSet, TestRequest
from pants.jvm.target_types import JvmDependenciesField, JvmJdkField, JvmResolveField


@dataclass(frozen=True)
class ClojureTestFieldSet(TestFieldSet):
    required_fields = (
        ClojureTestSourceField,
        JvmJdkField,
    )

    sources: ClojureTestSourceField
    timeout: ClojureTestTimeoutField
    jdk_version: JvmJdkField
    dependencies: JvmDependenciesField
    resolve: JvmResolveField
    extra_env_vars: ClojureTestExtraEnvVarsField


class ClojureTestRequest(TestRequest):
    field_set_type = ClojureTestFieldSet
    supports_debug = True  # We can support debug mode later
```

**Key aspects**:
- Matches JUnit/Scalatest field set pattern
- No tool_subsystem needed (Clojure comes from resolve)
- Debug support declared for future implementation

#### Step 1.2: Create Test Setup Rule

```python
from pants.core.goals.test import TestSubsystem
from pants.core.util_rules.environments import EnvironmentVarsRequest
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import Digest, MergeDigests
from pants.engine.process import ProcessCacheScope
from pants.engine.rules import Get, MultiGet, rule
from pants.engine.target import TransitiveTargets, TransitiveTargetsRequest
from pants.jvm.classpath import Classpath
from pants.jvm.jdk_rules import JdkEnvironment, JdkRequest, JvmProcess
from pants.jvm.subsystems.jvm import JvmSubsystem
from pants.util.logging import LogLevel


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

    # Build classpath for test and dependencies
    classpath = await Get(Classpath, CoarsenedTargets([request.field_set.address]))

    # Get source files for resource access
    source_files = await Get(
        SourceFiles,
        SourceFilesRequest(
            (dep.get(SourcesField) for dep in transitive_targets.dependencies),
            for_sources_types=(FileSourceField,),
            enable_codegen=True,
        ),
    )

    # Merge all input digests
    input_digest = await Get(
        Digest,
        MergeDigests([*classpath.digests(), source_files.snapshot.digest]),
    )

    # Get environment variables
    field_set_extra_env = await Get(
        EnvironmentVars,
        EnvironmentVarsRequest(request.field_set.extra_env_vars.value or ()),
    )

    # Extract test namespace from source file
    # We need to parse the (ns ...) form from the .clj file
    test_namespace = await get_test_namespace(request.field_set.sources.value)

    # Output directory for test results
    reports_dir = f"__reports/{request.field_set.address.path_safe_spec}"

    # Cache test runs only if successful, or not at all if --test-force
    cache_scope = (
        ProcessCacheScope.PER_SESSION if test_subsystem.force
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
        classpath_entries=list(classpath.args()),
        argv=[
            *extra_jvm_args,
            "clojure.main",
            "-e",
            test_runner_code,
        ],
        input_digest=input_digest,
        extra_env=field_set_extra_env,
        output_directories=(reports_dir,),
        description=f"Run clojure.test for {request.field_set.address}",
        timeout_seconds=request.field_set.timeout.calculate_from_global_options(test_subsystem),
        level=LogLevel.DEBUG,
        cache_scope=cache_scope,
        use_nailgun=False,
    )

    return TestSetup(process=process, reports_dir=reports_dir)
```

**Key aspects**:
- Follows JUnit/Scalatest setup pattern
- Uses full transitive classpath (includes Clojure JAR from resolve)
- No separate tool classpath needed (clojure.main is in user classpath)
- Dynamically discovers test namespace from source file
- Uses `clojure.main -e` to evaluate test runner code
- No AOT compilation required (runtime compilation)

#### Step 1.3: Namespace Discovery Helper

```python
@rule
async def get_test_namespace(test_file: str) -> str:
    """Extract namespace from Clojure test file."""
    # Read the file content
    digest = await Get(Digest, PathGlobs([test_file]))
    file_content = await Get(DigestContents, Digest, digest)

    # Parse (ns ...) form
    # Simple regex: (ns namespace.name ...)
    content = file_content.sole_file.content.decode("utf-8")
    match = re.search(r'\(ns\s+([a-z0-9\-_.]+)', content)

    if not match:
        raise ValueError(f"Could not find namespace declaration in {test_file}")

    return match.group(1)
```

**Note**: We'll need to refine this to handle edge cases in namespace parsing.

#### Step 1.4: Test Execution Rule

```python
from pants.engine.process import Process, FallibleProcessResult, ProcessWithRetries
from pants.core.goals.test import TestResult


@rule(desc="Run Clojure tests", level=LogLevel.DEBUG)
async def run_clojure_test(
    test_subsystem: TestSubsystem,
    batch: ClojureTestRequest.Batch[ClojureTestFieldSet, Any],
) -> TestResult:
    field_set = batch.single_element

    # Setup test process
    test_setup = await Get(TestSetup, TestSetupRequest(field_set, is_debug=False))

    # Convert JvmProcess to Process
    process = await Get(Process, JvmProcess, test_setup.process)

    # Execute with retry support
    process_results = await Get(
        tuple[FallibleProcessResult, ...],
        ProcessWithRetries(process, test_subsystem.attempts_default),
    )

    # For now, we won't generate XML reports (Phase 2)
    # Just return the process result
    return TestResult.from_fallible_process_result(
        process_results=process_results,
        address=field_set.address,
        output_setting=test_subsystem.output,
    )
```

**Key aspects**:
- Matches JUnit/Scalatest execution pattern
- Supports test retries
- Returns test result with stdout/stderr

#### Step 1.5: Debug Support (Future)

```python
@rule(level=LogLevel.DEBUG)
async def setup_clojure_test_debug_request(
    batch: ClojureTestRequest.Batch[ClojureTestFieldSet, Any],
) -> TestDebugRequest:
    setup = await Get(TestSetup, TestSetupRequest(batch.single_element, is_debug=True))
    process = await Get(Process, JvmProcess, setup.process)

    return TestDebugRequest(
        InteractiveProcess.from_process(
            process,
            forward_signals_to_process=False,
            restartable=True,
        )
    )
```

#### Step 1.6: Rules Registration

```python
def rules():
    return [
        *collect_rules(),
        *ClojureTestRequest.rules(),
    ]
```

**File**: `pants-plugins/clojure_backend/register.py`

Update to include test runner rules:

```python
from clojure_backend import test_runner

def rules():
    return [
        *target_type_rules(),
        *test_runner.rules(),
    ]
```

### Phase 2: XML Test Report Generation

Currently, clojure.test outputs text to stdout. For integration with CI systems and Pants' test reporting, we should generate JUnit-compatible XML reports.

**Options**:

1. **Use test2junit library**: External library that wraps clojure.test
   - Maven: `test2junit:test2junit:1.4.2`
   - Usage: `(require '[test2junit.core]) (test2junit.core/run-tests ...)`

2. **Custom XML reporter**: Write our own clojure.test reporter
   - More control over output format
   - No extra dependencies

**Recommended**: Use test2junit for Phase 2 implementation. Add to test setup:

```python
# In test_runner.py setup_clojure_test_for_target
test_runner_code = (
    "(require 'test2junit.core) "
    f"(test2junit.core/run-tests-in-directory '{reports_dir} '{test_namespace})"
)
```

Then parse XML output:

```python
# In run_clojure_test rule
xml_result_subset = await Get(
    Digest,
    DigestSubset(process_results[-1].output_digest, PathGlobs([f"{reports_dir}/**"])),
)
xml_results = await Get(
    Snapshot,
    RemovePrefix(xml_result_subset, test_setup.reports_dir),
)

return TestResult.from_fallible_process_result(
    process_results=process_results,
    address=field_set.address,
    output_setting=test_subsystem.output,
    xml_results=xml_results,  # Add XML results
)
```

### Phase 3: Compilation Support (Future Enhancement)

While tests can run without AOT compilation, we may want to support it for:

1. **Faster startup**: Pre-compiled code loads faster
2. **Error checking**: Compilation catches errors early
3. **Deployment**: Some users want to ship compiled code

**Implementation approach** (when needed):

```python
@rule
async def compile_clojure_sources(request: CompileClojureSourcesRequest) -> CompiledClassfiles:
    # Create classes directory
    classes_dir = "__classes"

    # Build classpath
    classpath = await Get(Classpath, ...)

    # Generate compilation script
    compile_code = (
        f"(binding [*compile-path* \"{classes_dir}\"] "
        f"  (compile '{namespace}))"
    )

    # Run compilation
    result = await Get(
        ProcessResult,
        JvmProcess(
            argv=["clojure.main", "-e", compile_code],
            classpath_entries=classpath.args(),
            output_directories=(classes_dir,),
        ),
    )

    return CompiledClassfiles(digest=result.output_digest)
```

## User-Facing Changes

### 1. Resolve Configuration

Users must include Clojure in their resolve:

**File**: `3rdparty/jvm/BUILD`

```python
jvm_artifact(
    name="org.clojure_clojure",
    group="org.clojure",
    artifact="clojure",
    version="1.11.1",
)
```

**File**: `3rdparty/jvm/default.lock`

Generated via: `pants generate-lockfiles --resolve=jvm-default`

### 2. Test Target Usage

**File**: `test/clj/BUILD`

```python
clojure_tests(
    name="tests",
    dependencies=[
        "//src/clj:lib",
        "//3rdparty/jvm:org.clojure_clojure",
    ],
)
```

### 3. Running Tests

```bash
# Run all tests
pants test test::

# Run specific test
pants test test/clj/example_test.clj:tests

# Run with debug output
pants test --test-output=all test::

# Force re-run (no caching)
pants test --test-force test::
```

## Validation Checklist

- [ ] Test runner implementation complete
- [ ] Can run simple passing test
- [ ] Can run simple failing test
- [ ] Test timeout works
- [ ] Test retries work (--test-attempts-default)
- [ ] Extra env vars passed correctly
- [ ] Classpath includes all dependencies
- [ ] Works with different JDK versions
- [ ] Works with different Clojure versions
- [ ] Namespace discovery handles various formats
- [ ] Error messages are clear
- [ ] Unit tests for test runner
- [ ] Integration tests with real Clojure tests

## Future Enhancements

1. **XML Report Generation** (Phase 2)
   - Use test2junit library
   - Generate JUnit-compatible XML
   - Better CI integration

2. **Debug Support** (Phase 3)
   - Interactive debugging with REPL
   - Breakpoint support

3. **Coverage Support** (Phase 4)
   - Integrate with cloverage
   - Generate coverage reports

4. **Test Filtering** (Phase 5)
   - Filter by test name/metadata
   - Skip certain tests

5. **AOT Compilation** (Phase 6)
   - Optional compilation step
   - Faster test startup
   - Better error checking

6. **Watch Mode** (Phase 7)
   - Re-run tests on file changes
   - Interactive development

## Example Test Files

### Simple Test

**File**: `test/clj/example_test.clj`

```clojure
(ns example.core-test
  (:require [clojure.test :refer [deftest is testing]]
            [example.core :as core]))

(deftest test-addition
  (testing "Basic addition"
    (is (= 4 (+ 2 2)))
    (is (= 0 (+ -1 1)))))

(deftest test-hello
  (is (= "Hello, World!" (core/hello "World"))))
```

### Test with Dependencies

**File**: `test/clj/integration_test.clj`

```clojure
(ns example.integration-test
  (:require [clojure.test :refer [deftest is use-fixtures]]
            [example.db :as db]
            [example.api :as api]))

(use-fixtures :once db/setup-test-db)

(deftest test-api-integration
  (let [result (api/create-user {:name "Alice"})]
    (is (= "Alice" (:name result)))
    (is (some? (:id result)))))
```

## Testing Strategy

### Unit Tests

**File**: `pants-plugins/clojure_backend/tests/test_runner_test.py`

```python
def test_simple_passing_test(rule_runner: RuleRunner) -> None:
    """Test that a simple passing test succeeds."""
    rule_runner.write_files({
        "BUILD": "clojure_tests(name='tests', dependencies=['//3rdparty/jvm:clojure'])",
        "example_test.clj": dedent("""
            (ns example.core-test
              (:require [clojure.test :refer [deftest is]]))

            (deftest test-addition
              (is (= 4 (+ 2 2))))
        """),
    })

    result = run_clojure_test(rule_runner, "tests", "example_test.clj")

    assert result.exit_code == 0
    assert "1 tests successful" in result.stdout_bytes.decode()


def test_simple_failing_test(rule_runner: RuleRunner) -> None:
    """Test that a failing test returns non-zero exit code."""
    rule_runner.write_files({
        "BUILD": "clojure_tests(name='tests', dependencies=['//3rdparty/jvm:clojure'])",
        "example_test.clj": dedent("""
            (ns example.core-test
              (:require [clojure.test :refer [deftest is]]))

            (deftest test-failure
              (is (= 5 (+ 2 2))))
        """),
    })

    result = run_clojure_test(rule_runner, "tests", "example_test.clj")

    assert result.exit_code != 0
    assert "FAIL" in result.stdout_bytes.decode()
```

## References

- **Clojure compilation**: https://clojure.org/reference/compilation
- **Clojure CLI**: https://clojure.org/reference/clojure_cli
- **JUnit implementation**: `/Users/hopper/workspace/python/pants/src/python/pants/jvm/test/junit.py`
- **Scalatest implementation**: `/Users/hopper/workspace/python/pants/src/python/pants/backend/scala/test/scalatest.py`
- **JVM process execution**: `/Users/hopper/workspace/python/pants/src/python/pants/jvm/jdk_rules.py`
- **Tool management patterns**: `/Users/hopper/workspace/python/pants/src/python/pants/core/util_rules/external_tool.py`
- **Session notes**: `sessions/20251004_initial_setup.md`
- **Test targets plan**: `docs/plans/20251004_clojure_tests.md`

## Decision Log

1. **Execution approach**: Use direct JVM execution (Option B) instead of Clojure CLI
   - More consistent with Pants' JVM patterns
   - No external tool installation
   - User controls Clojure version

2. **Compilation strategy**: Start without AOT compilation
   - Simpler initial implementation
   - Runtime compilation is sufficient for tests
   - Can add AOT as future enhancement

3. **Tool management**: Clojure JAR from user's resolve
   - No subsystem needed
   - User brings their own Clojure version
   - Consistent with how Scala compiler works

4. **Test discovery**: Parse namespace from source file
   - Simple regex-based extraction
   - Can refine later for edge cases
   - Sufficient for initial implementation
