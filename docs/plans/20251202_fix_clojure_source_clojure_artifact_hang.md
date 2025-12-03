# Plan: Fix `clojure_source` -> `jvm_artifact(clojure)` Dependency Hang

## Status: BLOCKED - Tests Still Hanging After Implementation

**Selected Solution:** Remove `ToolClasspathRequest` and rely on user's classpath containing Clojure.

**Current Issue:** Tests are still hanging even after implementation. Investigation ongoing.

---

## Implementation Progress Log

### What Has Been Done

1. **Phase 1 & 2 Complete:** Removed `ToolClasspathRequest` from both `aot_compile.py` and `check.py`
   - These changes were made in a previous session and are complete

2. **Phase 3 Partial:** Updated tests to provide Clojure via lockfile
   - Updated `test_check.py` with proper lockfile containing Clojure 1.11.0
   - Updated `test_aot_compile.py` with proper lockfile (fixed fake fingerprints that were causing issues)
   - Updated `test_package_clojure_deploy_jar.py` with proper lockfile and dependencies
   - Updated `test_error_scenarios.py` with proper lockfile and dependencies

3. **Test File Changes Made:**
   - Added `LOCKFILE_WITH_CLOJURE` constant with correct Clojure 1.11.0 fingerprints
   - Added `CLOJURE_3RDPARTY_BUILD` constant for jvm_artifact definition
   - Updated all tests to use proper lockfile instead of empty/fake lockfiles
   - Added `dependencies=["3rdparty/jvm:org.clojure_clojure"]` to clojure_source targets

### What's Still Hanging

Even with proper lockfiles, tests are still hanging for 60+ seconds before timing out or being interrupted. Tests affected:
- `test_check_valid_clojure_code` in test_check.py
- `test_check_with_unicode_characters` in test_error_scenarios.py
- Other tests that invoke Clojure compilation

### Possible Remaining Issues

1. **Lockfile fingerprint mismatch:** Initial lockfile had wrong fingerprint for `spec.alpha` (fixed from `6a35c9027c...` to `67ec898eb5...`)

2. **Still using cached bad state:** Pants may be caching something from previous runs that's causing the hang

3. **Different root cause:** The hang may not be solely about ToolClasspathRequest vs user classpath conflict - there may be another scheduler issue

4. **Test infrastructure issue:** The rule_runner tests may have their own issues with JVM artifact resolution

### Things Tried That Didn't Work

1. **Removing ToolClasspathRequest** - Implemented but tests still hang
2. **Adding proper lockfiles with correct fingerprints** - Implemented but tests still hang
3. **Adding explicit Clojure dependency to clojure_source targets** - Implemented but tests still hang
4. **Running individual tests** - Still hang
5. **Running test suite** - Hangs on JVM-related tests

### Next Steps to Try

1. **Clear Pants cache completely** and re-run tests
2. **Check if non-check/non-AOT tests pass** - e.g., dependency inference tests that don't invoke Clojure
3. **Add debug logging** to understand where exactly the hang occurs
4. **Compare with working tests** - The `test_runner.py` tests work, compare their setup
5. **Check if this is a rule_runner issue** - Maybe rule_runner has specific requirements for JVM tests

---

## Problem Summary

When a `clojure_source` target directly depends on a `jvm_artifact` for `org.clojure:clojure`, the Pants scheduler hangs indefinitely. This happens because:

1. **AOT compilation** (`aot_compile.py`) and **check goal** (`check.py`) fetch Clojure via `ToolClasspathRequest` with a hardcoded `DEFAULT_CLOJURE_VERSION`
2. The user's dependency graph also includes `jvm_artifact(clojure)`
3. When Pants resolves the classpath, it encounters conflicting resolution paths for the same artifact
4. This creates a deadlock in the Pants scheduler

---

## Investigation Findings

### Root Cause

The hang occurs during the Pants scheduler's **Coursier resolution phase**, not during digest merging.

When both paths try to fetch `org.clojure:clojure`:

**Path 1: User Classpath Resolution**
- `classpath_get()` → `select_coursier_resolve_for_targets()` → loads user's lockfile
- Calls `coursier_fetch_one_coord()` with a `CoursierLockfileEntry` that has `pants_address` set

**Path 2: Tool Classpath Resolution**
- `ToolClasspathRequest(artifact_requirements=...)` → `coursier_resolve_lockfile()` → creates fresh entries
- Calls `coursier_fetch_one_coord()` with a `CoursierLockfileEntry` that has NO `pants_address`

The **cache key** for `coursier_fetch_one_coord()` includes `pants_address`, so two different cache keys are created for the same coordinate, causing scheduler confusion.

### Why the `extra_immutable_input_digests` Approach Failed

The original plan proposed using `extra_immutable_input_digests` to isolate tool and user classpaths (following the Scala pattern). This was implemented but **does not work** because:

- `extra_immutable_input_digests` only affects the **process execution phase**
- The hang occurs **earlier** during **Coursier fetch**
- By the time we get to digest merging, the scheduler has already hung

---

## Selected Solution: Option B - Rely on User's Classpath

Remove `ToolClasspathRequest` entirely and rely on Clojure being present in the user's classpath.

### Rationale

1. **The test runner already works this way** - `test.py` doesn't fetch Clojure via `ToolClasspathRequest`, it just uses whatever is in the user's classpath
2. **Users building deploy JARs will have Clojure** - If someone is building a `clojure_deploy_jar`, they almost certainly have Clojure somewhere in their dependency graph
3. **Simplest solution** - No scheduler conflicts possible, user controls the Clojure version entirely
4. **Consistent behavior** - AOT compilation and checking will use the same Clojure version as test running

### Tradeoffs

**Pros:**
- Eliminates scheduler hang completely
- Simpler code - removes complexity around tool classpath management
- User controls the Clojure version
- Consistent with how `test.py` works

**Cons:**
- If a `clojure_source` somehow has no path to Clojure in its dependencies, AOT compilation would fail
- Error message would be "clojure.main not found" which may be confusing (mitigated by good error handling)

---

## Implementation Plan

### Phase 1: Update AOT Compilation

**File:** `pants-plugins/clojure_backend/aot_compile.py`

**Changes:**
1. Remove the `ToolClasspathRequest` for fetching Clojure
2. Remove `clojure_classpath` from `MultiGet`
3. Remove `clojure_classpath.digest` from `MergeDigests`
4. Remove `clojure_classpath.classpath_entries()` from classpath entries
5. Remove unused imports (`ToolClasspath`, `ToolClasspathRequest`, `ArtifactRequirement`, `ArtifactRequirements`, `Coordinate`)
6. Remove `DEFAULT_CLOJURE_VERSION` import if no longer needed

**Before:**
```python
jdk, classpath, targets, clojure_classpath = await MultiGet(
    Get(JdkEnvironment, JdkRequest, jdk_request),
    classpath_get(**implicitly(request.source_addresses)),
    Get(Targets, Addresses, request.source_addresses),
    Get(
        ToolClasspath,
        ToolClasspathRequest(
            artifact_requirements=ArtifactRequirements([clojure_artifact]),
        ),
    ),
)
```

**After:**
```python
jdk, classpath, targets = await MultiGet(
    Get(JdkEnvironment, JdkRequest, jdk_request),
    classpath_get(**implicitly(request.source_addresses)),
    Get(Targets, Addresses, request.source_addresses),
)
```

### Phase 2: Update Check Goal

**File:** `pants-plugins/clojure_backend/goals/check.py`

**Changes:**
1. Remove the `ToolClasspathRequest` for fetching Clojure
2. Remove `clojure_classpath` from `MultiGet`
3. Remove `clojure_classpath.digest` from `MergeDigests`
4. Remove `clojure_classpath.classpath_entries()` from classpath entries
5. Remove unused imports

### Phase 3: Update Test to Verify Fix

**File:** `pants-plugins/tests/test_package_clojure_deploy_jar.py`

**Changes:**
1. Update `test_provided_maven_transitives_excluded_from_jar` to have `clojure_source` depend on `jvm_artifact(clojure)` directly
2. This restores the original test structure that was causing the hang

**Test structure:**
```python
jvm_artifact(
    name="clojure",
    group="org.clojure",
    artifact="clojure",
    version="1.11.0",
)

clojure_source(
    name="core",
    source="core.clj",
    dependencies=[":clojure"],  # Direct dependency on clojure
)

clojure_deploy_jar(
    name="app",
    main="app.core",
    dependencies=[":core"],
    provided=[":clojure"],
)
```

### Phase 4: Run Full Test Suite

```bash
# Run the previously hanging test
pants test pants-plugins/tests/test_package_clojure_deploy_jar.py -- -v -k "test_provided_maven_transitives_excluded_from_jar"

# Run all package tests
pants test pants-plugins/tests/test_package_clojure_deploy_jar.py

# Run full test suite
pants test pants-plugins::
```

---

## Files Summary

| File | Action | Description |
|------|--------|-------------|
| `pants-plugins/clojure_backend/aot_compile.py` | Modify | Remove `ToolClasspathRequest`, rely on user classpath |
| `pants-plugins/clojure_backend/goals/check.py` | Modify | Remove `ToolClasspathRequest`, rely on user classpath |
| `pants-plugins/tests/test_package_clojure_deploy_jar.py` | Modify | Update test to have `clojure_source` depend on `jvm_artifact(clojure)` |

---

## Success Criteria

1. `test_provided_maven_transitives_excluded_from_jar` passes with `clojure_source` directly depending on `jvm_artifact(clojure)`
2. All existing tests continue to pass
3. Users can have `clojure_source` targets depend on `org.clojure:clojure` without scheduler hangs
4. AOT compilation and check goal work correctly using user's classpath

---

## Alternative Options (Not Selected)

<details>
<summary>Click to expand other considered options</summary>

### Option A: Use Pre-Generated Lockfile for Clojure Tool

Create a `ClojureSubsystem` that extends `JvmToolBase` with a pre-generated lockfile.

**Why not selected:** More complex, requires maintaining a lockfile, and Option B is simpler and sufficient.

### Option C: Automatic Dependency Injection (Like Scala)

Add automatic dependency injection for Clojure, similar to how Scala handles `scala-library`.

**Why not selected:** Most complex implementation, and Option B achieves the goal more simply.

### Option D: File Pants Bug / Upstream Fix

File an issue with Pants to fix the scheduler deadlock.

**Why not selected:** Timeline unknown, and we need a fix now. Could still file the bug for long-term resolution.

</details>

---

## Original Failed Approach (Archived)

<details>
<summary>Click to expand details on failed approach</summary>

### Isolated Tool Classpath via `extra_immutable_input_digests`

The original plan proposed using `extra_immutable_input_digests` to isolate tool and user classpaths, following the Scala backend pattern.

**Why it was expected to work:**
- Tool Clojure kept in separate directory (`__toolcp/`)
- User classpath uses different prefix (`__cp/`)
- No digest merging conflict

**Why it doesn't work:**
- The hang occurs during **Coursier fetch**, not during **digest merging**
- `extra_immutable_input_digests` only affects the process execution phase
- By the time we get to digest merging, the scheduler has already hung

</details>
