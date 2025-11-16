# Writing Integration Tests for Pants Plugins

This guide documents how to write end-to-end integration tests for Pants plugins, based on patterns used in Pants' own JVM backend tests.

## Overview

Integration tests for Pants plugins use `RuleRunner` to test the complete rule execution graph without running actual Pants commands. These tests verify that all rules work together correctly end-to-end.

## Key Differences: Unit Tests vs Integration Tests

**Unit Tests:**
- Test individual rules in isolation
- Minimal rule dependencies
- Fast execution
- Good for testing rule logic

**Integration Tests:**
- Test complete workflows (e.g., dependency inference, compilation, testing)
- Require many interconnected rules
- Slower execution (but still fast enough for CI)
- Verify that rules work together correctly
- Catch integration bugs that unit tests miss

## RuleRunner Setup Pattern

The `RuleRunner` is the test harness for integration tests. It requires careful setup to include all necessary rules.

### Basic Structure

```python
from pants.testutil.rule_runner import RuleRunner, PYTHON_BOOTSTRAP_ENV
import pytest

@pytest.fixture
def rule_runner() -> RuleRunner:
    """Set up a RuleRunner for integration tests."""
    rule_runner = RuleRunner(
        rules=[
            # ... all required rules
        ],
        target_types=[
            # ... all target types used in tests
        ],
    )
    rule_runner.set_options(args=[], env_inherit=PYTHON_BOOTSTRAP_ENV)
    return rule_runner
```

### Critical Rules for JVM Dependency Inference Tests

Based on analysis of Pants' Kotlin, Scala, and Java tests, here are the essential rules:

```python
rules=[
    # Core Pants utilities
    *config_files.rules(),
    *source_files.rules(),

    # JVM infrastructure
    *jvm_tool.rules(),
    *jvm_util_rules(),
    *jdk_rules.rules(),

    # JVM dependency inference (CRITICAL!)
    *artifact_mapper.rules(),       # Maps JVM artifacts to symbols
    *jvm_symbol_mapper.rules(),     # Generic JVM symbol mapping

    # Language-specific rules
    *dependency_inference_rules(),  # Your language's dep inference
    *language_symbol_mapping_rules(),  # Your language's symbol mapping
    *target_types_rules(),          # Your language's target types

    # Query rules (what you want to test)
    QueryRule(Addresses, [DependenciesRequest]),
    QueryRule(ExplicitlyProvidedDependencies, [DependenciesRequest]),
    QueryRule(InferredDependencies, [InferYourLanguageSourceDependencies]),
    QueryRule(InferredDependencies, [InferYourLanguageTestDependencies]),
],
target_types=[
    YourSourceTarget,
    YourSourcesGeneratorTarget,
    YourTestTarget,
    YourTestsGeneratorTarget,
    JvmArtifactTarget,  # IMPORTANT: Needed if tests use jvm_artifact()
]
```

## Common Pitfalls and Gotchas

### 1. Missing `artifact_mapper.rules()` and `jvm_symbol_mapper.rules()`

**Symptom:** Errors like:
```
No source of dependency SymbolMapping for @rule(...)
No installed rules return the type AllJvmTypeProvidingTargets
No installed rules return the type ThirdPartySymbolMapping
```

**Solution:** Include both:
- `*artifact_mapper.rules()` - Required for JVM artifact mapping
- `*jvm_symbol_mapper.rules()` - Required for generic JVM symbol mapping

These are SEPARATE from your language-specific symbol mapping rules!

### 2. Missing `JvmArtifactTarget`

**Symptom:** Error when parsing BUILD files in tests:
```
NameError: name 'jvm_artifact' is not defined

All registered symbols: [...clojure_source, clojure_test...]
```

**Solution:** Add `JvmArtifactTarget` to `target_types`:
```python
from pants.jvm.target_types import JvmArtifactTarget

target_types=[
    # ... your target types ...
    JvmArtifactTarget,  # Enables jvm_artifact() in test BUILD files
]
```

### 3. Missing Core Rule Sources

**Symptom:** Errors like:
```
No source of dependency Get(DigestContents, [Digest])
No source of dependency Get(Owners, [OwnersRequest])
No source of dependency Get(SourceFiles, [SourceFilesRequest])
No source of dependency JvmSubsystem
```

**Solution:** These come from the core rule sets. Make sure you include:
- `*config_files.rules()`
- `*source_files.rules()`
- `*jvm_util_rules()`
- `*jdk_rules.rules()`

### 4. Wrong Import Paths

**Gotcha:** The import path differs between `jdk_rules`:

**CORRECT:**
```python
from pants.jvm import jdk_rules
# Then use:
*jdk_rules.rules()
```

**WRONG:**
```python
from pants.jvm.jdk_rules import rules as jdk_rules
# Then use:
*jdk_rules()
```

The first pattern is used in Kotlin/newer tests and is the recommended approach.

### 5. `maybe_skip_jdk_test` Decorator Not Available

**Issue:** In Pants 2.28.0, `pants.jvm.testutil` module doesn't exist yet.

**Solution:** Define it yourself in your test file:
```python
import ast
import os
import pytest

def maybe_skip_jdk_test(func):
    """Skip JDK tests based on environment variable."""
    run_jdk_tests = bool(ast.literal_eval(os.environ.get("PANTS_RUN_JDK_TESTS", "True")))
    return pytest.mark.skipif(not run_jdk_tests, reason="Skip JDK tests")(func)
```

In newer Pants versions (2.29+), you can import it:
```python
from pants.jvm.testutil import maybe_skip_jdk_test
```

### 6. Assertion Pattern

**Less Precise:**
```python
assert utils_target.address in inferred
```

**More Precise (Recommended):**
```python
assert inferred == InferredDependencies([utils_target.address])
```

The second pattern is what Pants' own tests use. It:
- Checks exact equality (no extra dependencies)
- Provides better error messages
- Is more explicit about expectations

## Test Patterns from Pants Codebase

### Pattern 1: Basic Dependency Inference

```python
@maybe_skip_jdk_test
def test_infer_source_dependency(rule_runner: RuleRunner) -> None:
    """Test that sources can infer dependencies on other sources."""
    rule_runner.write_files({
        "BUILD": dedent("""\
            clojure_source(
                name='utils',
                source='utils.clj',
            )
            clojure_test(
                name='test',
                source='utils_test.clj',
                # Dependency should be inferred
            )
            """),
        "utils.clj": dedent("""\
            (ns my.utils)
            (defn add [a b] (+ a b))
            """),
        "utils_test.clj": dedent("""\
            (ns my.utils-test
              (:require [my.utils :as utils]))
            """),
    })

    test_target = rule_runner.get_target(
        Address("", target_name="test", relative_file_path="utils_test.clj")
    )
    utils_target = rule_runner.get_target(
        Address("", target_name="utils", relative_file_path="utils.clj")
    )

    from your_backend.dependency_inference import (
        InferYourLanguageTestDependencies,
        YourLanguageTestDependenciesInferenceFieldSet,
    )

    inferred = rule_runner.request(
        InferredDependencies,
        [InferYourLanguageTestDependencies(
            YourLanguageTestDependenciesInferenceFieldSet.create(test_target)
        )],
    )

    assert inferred == InferredDependencies([utils_target.address])
```

### Pattern 2: Same Target (No Inference)

Test that files in the same target don't infer dependencies on each other:

```python
@maybe_skip_jdk_test
def test_same_target_no_inference(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({
        "BUILD": "clojure_sources(name='t')",
        "A.clj": "(ns a)",
        "B.clj": "(ns b (:require [a]))",  # Same target as A
    })

    target_b = rule_runner.get_target(
        Address("", target_name="t", relative_file_path="B.clj")
    )

    inferred = rule_runner.request(
        InferredDependencies,
        [InferYourLanguageSourceDependencies(
            YourLanguageSourceDependenciesInferenceFieldSet.create(target_b)
        )],
    )

    # Should not infer dependency on A (same target)
    assert inferred == InferredDependencies([])
```

### Pattern 3: Transitive Dependencies

Test that transitive dependencies are correctly inferred:

```python
@maybe_skip_jdk_test
def test_transitive_dependencies(rule_runner: RuleRunner) -> None:
    """Test: test -> service -> logger (transitive chain)"""
    rule_runner.write_files({
        "BUILD": dedent("""\
            clojure_source(name='logger', source='logger.clj')
            clojure_source(name='service', source='service.clj')
            clojure_test(name='test', source='service_test.clj')
            """),
        "logger.clj": "(ns logger) (defn log [msg] (println msg))",
        "service.clj": "(ns service (:require [logger]))",
        "service_test.clj": "(ns service-test (:require [service]))",
    })

    # Test should infer service
    test_inferred = rule_runner.request(...)
    assert test_inferred == InferredDependencies([service_address])

    # Service should infer logger
    service_inferred = rule_runner.request(...)
    assert service_inferred == InferredDependencies([logger_address])
```

## Comparing with Pants' Own Tests

Your integration tests should follow the same patterns as Pants' JVM backend tests:

**Reference Files:**
- Java: `/src/python/pants/backend/java/dependency_inference/rules_test.py`
- Scala: `/src/python/pants/backend/scala/dependency_inference/rules_test.py`
- Kotlin: `/src/python/pants/backend/kotlin/dependency_inference/rules_test.py`

**Key Observations:**

1. **Kotlin pattern is most similar** to what newer JVM languages need (includes artifact_mapper)
2. **All use `@maybe_skip_jdk_test`** decorator
3. **All use precise equality assertions** with `InferredDependencies([...])`
4. **None include compilation rules** for pure dependency inference tests
5. **All use minimal, focused test cases** (not realistic full applications)

## When to Use Different Rule Sets

### Dependency Inference Only
```python
rules=[
    *config_files.rules(),
    *source_files.rules(),
    *jvm_tool.rules(),
    *jvm_util_rules(),
    *jdk_rules.rules(),
    *artifact_mapper.rules(),
    *jvm_symbol_mapper.rules(),
    *your_dep_inference_rules(),
    *your_symbol_mapping_rules(),
    *your_target_types_rules(),
]
```

### Compilation Tests
Add:
```python
*compile_your_language_rules(),
*classpath.rules(),
```

### Full Integration (Compilation + Testing)
Add all of the above plus:
```python
*your_test_runner_rules(),
*coursier_fetch_rules(),
*coursier_setup_rules(),
*lockfile.rules(),
```

## Debugging Tips

### 1. Use `--print-stacktrace` and `-ldebug`

When tests fail:
```bash
pants test path/to/test.py --print-stacktrace -ldebug
```

### 2. Check Rule Graph Errors Carefully

Errors like "No source of dependency X" mean:
- X is not provided by any installed rule
- OR X needs to be included in QueryRule or Get

Look at the rule name in the error to see what's requesting it.

### 3. Start with a Known-Good Example

Copy the RuleRunner setup from Kotlin tests first, then adapt:
1. Copy Kotlin test setup
2. Replace Kotlin-specific imports with yours
3. Add your language's rules
4. Test incrementally

### 4. Version Compatibility

Features vary by Pants version:
- **2.28.0**: Stable, but missing `pants.jvm.testutil`
- **2.29.0+**: Has more JVM utilities, but check compatibility with your backend code

## Running the Tests

```bash
# Run all integration tests
pants test pants-plugins/tests/test_dependency_inference_integration.py

# Run with verbose output
pants test pants-plugins/tests/test_dependency_inference_integration.py -v

# Run with debug logging
pants test pants-plugins/tests/test_dependency_inference_integration.py -ldebug

# Skip JDK tests (useful for quick checks)
PANTS_RUN_JDK_TESTS=False pants test pants-plugins/tests/
```

## Best Practices

1. **Use pytest fixtures** for RuleRunner setup (reused across all tests)
2. **Write focused test cases** - one aspect per test
3. **Use `dedent()`** for multi-line strings (keeps tests readable)
4. **Follow Pants naming conventions** - `test_infer_*`, `test_compile_*`, etc.
5. **Test edge cases** - same target, ambiguous symbols, cycles, transitive deps
6. **Keep test BUILD files minimal** - only what's needed to demonstrate the feature
7. **Use descriptive docstrings** - explain what's being tested and why

## Common Test Scenarios to Cover

For dependency inference:
-  Basic inference (test ’ source)
-  Same target (no inference expected)
-  Transitive dependencies
-  Ambiguous symbols (warn, don't infer)
-  Explicit `!` excludes override inference
-  Third-party dependencies
-  Cross-language dependencies (if applicable)

## Example: Complete Integration Test

See `pants-plugins/tests/test_dependency_inference_integration.py` for a complete example following all these patterns.

## Troubleshooting Checklist

When integration tests fail, check:

- [ ] All required rules are included (especially `artifact_mapper` and `jvm_symbol_mapper`)
- [ ] All target types are registered (including `JvmArtifactTarget`)
- [ ] Import paths are correct (`from pants.jvm import jdk_rules`)
- [ ] `PYTHON_BOOTSTRAP_ENV` is passed to `set_options()`
- [ ] Test uses `@maybe_skip_jdk_test` decorator
- [ ] Assertions use exact equality (`==`) not membership (`in`)
- [ ] BUILD files in tests use registered target types
- [ ] No typos in target addresses or file paths

## Summary

Integration tests are essential for Pants plugins but require careful setup:

1. **Include all necessary rules** - especially `artifact_mapper` and `jvm_symbol_mapper`
2. **Register all target types** - including `JvmArtifactTarget`
3. **Follow Pants patterns** - study Kotlin/Scala/Java tests
4. **Test incrementally** - start simple, add complexity
5. **Be precise** - use exact equality assertions

The complexity is worth it - integration tests catch bugs that unit tests miss and give confidence that your plugin works end-to-end.
