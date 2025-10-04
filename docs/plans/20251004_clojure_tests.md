# Plan: Implementing `clojure_test` and `clojure_tests` Targets

**Date:** 2025-10-04
**Status:** Planning
**Based on:** `junit_test`/`junit_tests` from Pants Java backend

## Overview

Implement test target types for Clojure similar to JUnit targets, but using clojure.test framework instead of JUnit.

## Key Differences from JUnit

| Aspect | JUnit (Java) | clojure.test (Clojure) |
|--------|--------------|------------------------|
| **File naming** | `*Test.java` | `*_test.clj` or `test_*.clj` |
| **Test framework** | JUnit JAR dependency | clojure.test (built-in to Clojure) |
| **Test discovery** | Scan for `@Test` annotations | Find namespaces, look for `deftest` |
| **Test execution** | JUnit runner | `clojure.main -m clojure.test` |
| **Dependencies** | Needs junit artifact | Just needs Clojure itself |

## Implementation Steps

### 1. Add Target Types

**File:** `pants-plugins/clojure_backend/target_types.py`

Add imports:
```python
from pants.core.goals.test import (
    TestTimeoutField,
    TestExtraEnvVarsField,
)
```

Define test-specific fields:
```python
class ClojureTestSourceField(ClojureSourceField):
    """A Clojure test file using clojure.test."""
    pass

class ClojureTestTimeoutField(TestTimeoutField):
    """Timeout for Clojure tests."""
    pass

class ClojureTestExtraEnvVarsField(TestExtraEnvVarsField):
    """Extra environment variables for Clojure tests."""
    pass
```

Define single test target:
```python
class ClojureTestTarget(Target):
    alias = "clojure_test"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ClojureTestSourceField,
        ClojureTestTimeoutField,
        ClojureTestExtraEnvVarsField,
        JvmDependenciesField,
        JvmResolveField,
        JvmProvidesTypesField,
        JvmJdkField,
    )
    help = "A single Clojure test file using clojure.test."
```

Define test generator:
```python
class ClojureTestsGeneratorSourcesField(ClojureGeneratorSourcesField):
    default = ("*_test.clj", "*_test.cljc", "test_*.clj", "test_*.cljc")
    help = generate_multiple_sources_field_help_message(
        "Example: `sources=['*_test.clj', '!skip_test.clj']`"
    )

class ClojureTestsGeneratorTarget(TargetFilesGenerator):
    alias = "clojure_tests"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ClojureTestsGeneratorSourcesField,
    )
    generated_target_cls = ClojureTestTarget
    copied_fields = COMMON_TARGET_FIELDS
    moved_fields = (
        ClojureTestTimeoutField,
        ClojureTestExtraEnvVarsField,
        JvmDependenciesField,
        JvmJdkField,
        JvmProvidesTypesField,
        JvmResolveField,
    )
    help = "Generate a `clojure_test` target for each file in the `sources` field."
```

### 2. Update `clojure_sources` to Exclude Tests

**File:** `pants-plugins/clojure_backend/target_types.py`

Update the default pattern to exclude test files:
```python
class ClojureSourcesGeneratorSourcesField(ClojureGeneratorSourcesField):
    default = (
        "*.clj",
        "*.cljc",
        # Exclude test files
        "!*_test.clj",
        "!*_test.cljc",
        "!test_*.clj",
        "!test_*.cljc",
    )
    help = generate_multiple_sources_field_help_message(
        "Example: `sources=['Example.clj', 'New*.clj', '!OldExample.clj']`"
    )
```

### 3. Register New Target Types

**File:** `pants-plugins/clojure_backend/register.py`

```python
from clojure_backend.target_types import (
    ClojureSourceTarget,
    ClojureSourcesGeneratorTarget,
    ClojureTestTarget,           # Add
    ClojureTestsGeneratorTarget, # Add
    rules as target_type_rules,
)

def target_types():
    return [
        ClojureSourceTarget,
        ClojureSourcesGeneratorTarget,
        ClojureTestTarget,           # Add
        ClojureTestsGeneratorTarget, # Add
    ]
```

### 4. Add Unit Tests

**File:** `pants-plugins/clojure_backend/tests/target_types_test.py`

```python
def test_clojure_test_field_extensions() -> None:
    """Test that ClojureTestSourceField accepts .clj and .cljc files."""
    assert ClojureTestSourceField.expected_file_extensions == (".clj", ".cljc")

def test_clojure_tests_default_globs() -> None:
    """Test that clojure_tests has correct default glob patterns for test files."""
    assert ClojureTestsGeneratorSourcesField.default == (
        "*_test.clj", "*_test.cljc", "test_*.clj", "test_*.cljc"
    )

def test_clojure_sources_excludes_tests_by_default() -> None:
    """Test that clojure_sources excludes test files by default."""
    assert "!*_test.clj" in ClojureSourcesGeneratorSourcesField.default
    assert "!test_*.clj" in ClojureSourcesGeneratorSourcesField.default

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
        t for parametrization in parametrizations
        for t in parametrization.parametrization.values()
    }

    assert len(generated_targets) == 2
    assert all(isinstance(t, ClojureTestTarget) for t in generated_targets)

    source_files = {t[ClojureTestSourceField].value for t in generated_targets}
    assert source_files == {"example_test.clj", "util_test.clj"}

def test_clojure_test_with_timeout(rule_runner: RuleRunner) -> None:
    """Test that clojure_test respects the timeout field."""
    rule_runner.write_files({
        "test/clj/BUILD": 'clojure_tests(name="tests", timeout=120)\n',
        "test/clj/example_test.clj": ""
    })
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
```

## Test File Naming Conventions

Clojure community standard patterns:
- `*_test.clj` - Most common (e.g., `core_test.clj`)
- `test_*.clj` - Alternative (e.g., `test_core.clj`)
- `*_test.cljc` - Cross-platform tests
- `test_*.cljc` - Cross-platform alternative

Note: Unlike Java's `*Test.java`, Clojure uses snake_case with `_test` suffix/prefix.

## Expected Usage

Once implemented, users can write:

```python
# project/BUILD
clojure_sources(
    name="lib",
    sources=["src/**/*.clj"],
)

clojure_tests(
    name="tests",
    sources=["test/**/*.clj"],
    dependencies=[
        ":lib",
        "//3rdparty/jvm:clojure",
    ],
    timeout=60,
    resolve="jvm-default",
)
```

Run commands:
```bash
# List all targets (will show generated test targets)
pants list project::

# View test target details (after test runner implemented)
pants peek project/test/example_test.clj:tests

# Run tests (Phase 6 - requires test runner implementation)
pants test project::
```

## Validation Checklist

- [ ] Target types defined with all required fields
- [ ] Test file patterns match Clojure conventions
- [ ] `clojure_sources` excludes test files by default
- [ ] Targets registered in `register.py`
- [ ] Unit tests pass: `pants test pants-plugins::`
- [ ] Can run `pants help clojure_test`
- [ ] Can run `pants help clojure_tests`
- [ ] Test targets generate correctly from BUILD files

## Future: Test Runner Implementation (Phase 6)

After target types work, implement test execution:

**File:** `pants-plugins/clojure_backend/test_runner.py`

Will handle:
1. Building classpath with test dependencies (reuse JVM classpath building)
2. Discovering test namespaces from test files (parse `ns` forms)
3. Running `java -cp <classpath> clojure.main -m clojure.test <namespaces>`
4. Parsing clojure.test output for pass/fail results
5. Integrating with Pants test goal

See Phase 6 in main plan document for details.

## References

- Main plan: `pants-clojure-plugin-plan.md`
- JUnit implementation: `/Users/hopper/workspace/python/pants/src/python/pants/backend/java/target_types.py`
- JVM test fields: `/Users/hopper/workspace/python/pants/src/python/pants/jvm/target_types.py`
- Session notes: `sessions/20251004_initial_setup.md`
