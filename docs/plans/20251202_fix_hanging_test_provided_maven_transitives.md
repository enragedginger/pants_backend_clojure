# Plan: Fix Hanging Test `test_provided_maven_transitives_excluded_from_jar`

## Problem Summary

The test `test_provided_maven_transitives_excluded_from_jar` in `pants-plugins/tests/test_package_clojure_deploy_jar.py` hangs indefinitely at line 906 when calling `rule_runner.request(BuiltPackage, [field_set])`.

## Investigation So Far

### Phase 1: Lockfile Fix (COMPLETED - Did NOT fix hang)

We identified and fixed malformed lockfile entries in `LOCKFILE_WITH_CLOJURE`. The `core.specs.alpha` and `spec.alpha` entries had empty `directDependencies = []` and `dependencies = []` instead of proper back-references to `clojure`.

**Status:** Fix applied, but test still hangs. The lockfile fix is correct for data integrity but was not the root cause of the hang.

### Key Differences Between Passing and Failing Tests

| Aspect | Passing Test (`test_provided_jvm_artifact_excluded_from_jar`) | Failing Test (`test_provided_maven_transitives_excluded_from_jar`) |
|--------|--------------------------------------------------------------|-------------------------------------------------------------------|
| Provided artifact | `jsr305` (no Maven transitives) | `clojure` (has Maven transitives: spec.alpha, core.specs.alpha) |
| BUILD location | Single file `src/app/BUILD` | Split: `3rdparty/jvm/BUILD` + `src/app/BUILD` |
| `clojure_source` depends on | `:jsr305` (same directory) | `//3rdparty/jvm:clojure` (cross-directory) |
| Purpose | Test JAR filename exclusion | Test Maven transitive exclusion |

---

## Phase 2: Deep Investigation (IN PROGRESS)

### Task 2.1: Identify exact hang location

Add debug logging to narrow down where the hang occurs. The hang happens during `rule_runner.request(BuiltPackage, [field_set])`, but we need to know which specific rule or `Get` call is blocking.

**Potential hang points in `package_clojure_deploy_jar` rule:**
1. Line 90-93: `Get(TransitiveTargets, TransitiveTargetsRequest(...))`
2. Line 112-116: `Get(SourceFiles, SourceFilesRequest(...))`
3. Line 202-205: `Get(ProvidedDependencies, ResolveProvidedDependenciesRequest(...))`
4. Line 219-231: `MultiGet` for JDK, classpath, and AOT compilation
5. Line 282: `Get(Digest, MergeDigests(...))`

**Potential hang points in `resolve_provided_dependencies` rule:**
1. Line 113: `Get(Targets, UnparsedAddressInputs, ...)`
2. Line 116-119: `MultiGet` for `TransitiveTargets`
3. Line 146-148: Lockfile loading and parsing

**Potential hang points in `aot_compile_clojure` rule:**
1. Line 78-88: `MultiGet` for JDK, classpath, targets, clojure_classpath
2. Line 99-106: `Get(SourceFiles, ...)`
3. Line 180: `Get(FallibleProcessResult, Process, ...)` - actual compilation

### Task 2.2: Add debug print statements

Add temporary print statements to identify the hang location:

```python
# In package.py, before each Get/MultiGet:
print("DEBUG: About to get TransitiveTargets", flush=True)
# ... Get call ...
print("DEBUG: Got TransitiveTargets", flush=True)
```

### Task 2.3: Compare rule_runner configurations

Check if the failing test is missing required rules that the passing tests have. Compare the `rule_runner` fixture setup with other working tests like `test_test_runner.py`.

### Task 2.4: Test with simplified setup

Create a minimal reproduction case:
1. Try the failing test with a single BUILD file (like the passing test)
2. Try removing the `provided` field to see if basic packaging works
3. Try with `provided` but without the cross-directory dependency

---

## Phase 3: Hypotheses to Test

### Hypothesis A: Cross-directory dependency resolution issue

The failing test uses `//3rdparty/jvm:clojure` (cross-directory) while passing tests use same-directory references like `:jsr305`. Test by changing the failing test to use same-directory setup.

### Hypothesis B: Missing rules in RuleRunner

The `rule_runner` fixture may be missing rules needed for resolving `jvm_artifact` transitives. Compare with `test_test_runner.py` which successfully uses `jvm_artifact` with Clojure.

### Hypothesis C: Classpath resolution for jvm_artifact with transitives

When a `clojure_source` depends on a `jvm_artifact` that has Maven transitives, the classpath resolution may hang. The `aot_compile.py` calls `classpath_get(**implicitly(request.source_addresses))` which needs to resolve all dependencies.

### Hypothesis D: Circular dependency in Pants engine

There may be a circular dependency when:
1. `clojure_deploy_jar` needs to compile code
2. Compilation needs Clojure on classpath
3. Clojure is also marked as `provided`
4. This creates a conflict in dependency resolution

---

## Phase 4: Implementation of Fix

(To be determined after investigation identifies root cause)

---

## Phase 5: Verification

### Task 5.1: Run the previously hanging test
```bash
pants test pants-plugins/tests/test_package_clojure_deploy_jar.py -- -v -k "test_provided_maven_transitives_excluded_from_jar"
```

### Task 5.2: Run the full test file
```bash
pants test pants-plugins/tests/test_package_clojure_deploy_jar.py
```

### Task 5.3: Run the full test suite
```bash
pants test pants-plugins/::
```

---

## Notes

- The lockfile fix from Phase 1 should be kept regardless - it corrects the data format
- The test's purpose (verifying Maven transitive exclusion) is valuable and worth fixing
- If the root cause is a Pants engine limitation, we may need to restructure the test or skip it with a note
