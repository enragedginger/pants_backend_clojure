# Plan: Fix `clojure_source` -> `jvm_artifact(clojure)` Dependency Hang

## Problem Summary

When a `clojure_source` target directly depends on a `jvm_artifact` for `org.clojure:clojure`, the Pants scheduler hangs indefinitely. This happens because:

1. **AOT compilation** (`aot_compile.py`) and **check goal** (`check.py`) fetch Clojure via `ToolClasspathRequest` with a hardcoded `DEFAULT_CLOJURE_VERSION`
2. The user's dependency graph also includes `jvm_artifact(clojure)`
3. When Pants resolves the classpath, it encounters conflicting resolution paths for the same artifact
4. This creates a deadlock in the Pants scheduler

This is a real limitation because in production Clojure projects, it's common and reasonable for `clojure_source` targets to depend on `org.clojure:clojure` (e.g., to access specific Clojure features or ensure version compatibility).

## Root Cause Analysis

The conflict occurs because Clojure is being requested through two different Pants mechanisms simultaneously:
- Via user dependency graph → `classpath_get()` → Coursier resolution with lockfile
- Via tool request → `ToolClasspathRequest` → Coursier resolution without lockfile context

The Pants engine cannot determine which Clojure version/resolution to use, leading to a scheduler deadlock.

## Solution Options Considered

### Option A: Use User's Clojure Only (Like Test Runner)

The test runner (`test.py`) doesn't fetch Clojure via `ToolClasspathRequest` - it relies on Clojure being in the user's classpath. Could we do the same for AOT compilation?

**Why this won't work:**
- AOT compilation and check goal need Clojure to be available to run `clojure.main`
- If a `clojure_source` doesn't have Clojure in its dependencies, compilation would fail
- This would require all `clojure_source` targets to explicitly depend on `jvm_artifact(clojure)`, which is poor UX
- The test runner works because tests typically depend on code that eventually depends on Clojure

### Option B: Use Isolated Tool Classpath (Scala Pattern) ✓ Selected

The Scala backend handles this exact problem by using `extra_immutable_input_digests` with directory prefixes to keep tool and user classpaths separate. `JvmProcess` in Pants supports this via the `extra_immutable_input_digests` parameter.

**Why this works:**
- Tool Clojure is kept in a separate directory (`__toolcp/`)
- User classpath uses the default path or a different prefix
- No digest merging conflict occurs because they're in separate filesystem locations
- The Pants scheduler sees them as unrelated operations

## Implementation Plan

### Phase 1: Update AOT Compilation to Use Isolated Tool Classpath

**Files to modify:**
- `pants-plugins/clojure_backend/aot_compile.py`

**Changes:**

1. Use `extra_immutable_input_digests` to keep tool classpath separate from `input_digest`
2. Add a directory prefix for the tool classpath entries
3. Remove `clojure_classpath.digest` from the `MergeDigests` call

**Current problematic code:**
```python
# Line 137-146 - Merges tool digest directly with user classpath digest
input_digest = await Get(
    Digest,
    MergeDigests([
        stripped_sources.snapshot.digest,
        *classpath.digests(),
        clojure_classpath.digest,  # <-- This causes conflict
        compile_script_digest,
    ]),
)
```

**Fixed code pattern:**
```python
# Define tool classpath prefix
toolcp_relpath = "__toolcp"

# DON'T merge clojure_classpath.digest into input_digest
input_digest = await Get(
    Digest,
    MergeDigests([
        stripped_sources.snapshot.digest,
        *classpath.digests(),
        compile_script_digest,
    ]),
)

# Build classpath with prefixed tool entries
classpath_entries = [
    ".",
    classes_dir,
    *classpath.args(),
    *[f"{toolcp_relpath}/{entry}" for entry in clojure_classpath.classpath_entries()],
]

# Pass tool classpath via extra_immutable_input_digests
process = JvmProcess(
    jdk=jdk,
    classpath_entries=classpath_entries,
    argv=["clojure.main", "__compile_script.clj"],
    input_digest=input_digest,
    extra_immutable_input_digests={toolcp_relpath: clojure_classpath.digest},  # <-- Key fix
    # ... rest of parameters
)
```

### Phase 2: Update Check Goal to Use Isolated Tool Classpath

**Files to modify:**
- `pants-plugins/clojure_backend/goals/check.py`

**Changes:**

Apply the same pattern as Phase 1:
1. Use `extra_immutable_input_digests` for tool classpath isolation
2. Add directory prefix for tool classpath entries
3. Remove `clojure_classpath.digest` from `MergeDigests`

**Current problematic code (lines 174-183):**
```python
input_digest = await Get(
    Digest,
    MergeDigests([
        loader_digest,
        stripped_sources.snapshot.digest,
        *clspath.digests(),
        clojure_classpath.digest,  # <-- This causes conflict
    ])
)
```

**Fixed code pattern:**
```python
toolcp_relpath = "__toolcp"

input_digest = await Get(
    Digest,
    MergeDigests([
        loader_digest,
        stripped_sources.snapshot.digest,
        *clspath.digests(),
        # clojure_classpath.digest removed from here
    ])
)

classpath_entries = [
    ".",
    *clspath.args(),
    *[f"{toolcp_relpath}/{entry}" for entry in clojure_classpath.classpath_entries()],
]

jvm_process = JvmProcess(
    jdk=jdk,
    classpath_entries=classpath_entries,
    argv=["clojure.main", "check_loader.clj"],
    input_digest=input_digest,
    extra_immutable_input_digests={toolcp_relpath: clojure_classpath.digest},  # <-- Key fix
    # ... rest of parameters
)
```

### Phase 3: Update Test to Verify the Fix

**Files to modify:**
- `pants-plugins/tests/test_package_clojure_deploy_jar.py`

**Changes:**

1. Update `test_provided_maven_transitives_excluded_from_jar` to have `clojure_source` depend on `jvm_artifact(clojure)` directly
2. This restores the original test structure that was failing before the workaround

**Current workaround structure (that avoids the hang):**
```python
clojure_source(
    name="core",
    source="core.clj",
    # NO dependencies on :clojure - workaround
)

clojure_deploy_jar(
    name="app",
    main="app.core",
    dependencies=[":core", ":clojure"],  # clojure_deploy_jar depends directly
    provided=[":clojure"],
)
```

**Restored test structure (that should now work):**
```python
clojure_source(
    name="core",
    source="core.clj",
    dependencies=[":clojure"],  # Now this should work without hanging
)

clojure_deploy_jar(
    name="app",
    main="app.core",
    dependencies=[":core"],
    provided=[":clojure"],
)
```

### Phase 4: Run Full Test Suite and Verify

**Commands:**
```bash
# Run the previously hanging test
pants test pants-plugins/tests/test_package_clojure_deploy_jar.py -- -v -k "test_provided_maven_transitives_excluded_from_jar"

# Run all package tests
pants test pants-plugins/tests/test_package_clojure_deploy_jar.py

# Run full test suite
pants test pants-plugins::
```

## Files Summary

| File | Action | Description |
|------|--------|-------------|
| `pants-plugins/clojure_backend/aot_compile.py` | Modify | Use `extra_immutable_input_digests` for tool classpath isolation |
| `pants-plugins/clojure_backend/goals/check.py` | Modify | Use `extra_immutable_input_digests` for tool classpath isolation |
| `pants-plugins/tests/test_package_clojure_deploy_jar.py` | Modify | Restore original test structure with `clojure_source` -> `clojure` dependency |

## Technical Details

### Why `extra_immutable_input_digests` Solves the Problem

The `JvmProcess` class (in `/Users/hopper/workspace/python/pants/src/python/pants/jvm/jdk_rules.py:321-371`) accepts an `extra_immutable_input_digests` parameter that allows placing digest contents in separate directory prefixes.

When we use:
```python
extra_immutable_input_digests={toolcp_relpath: clojure_classpath.digest}
```

The tool Clojure JAR files are materialized at `__toolcp/path/to/clojure.jar` rather than being merged into the main digest. This keeps them separate from any user-provided Clojure in the main classpath.

The classpath entries then reference these prefixed paths:
```python
*[f"{toolcp_relpath}/{entry}" for entry in clojure_classpath.classpath_entries()]
```

### Why This is Different from Scala

The Scala backend also uses the `extra_immutable_input_digests` pattern, but with additional complexity:
- It uses `-bootclasspath` for priority ordering
- It has separate compiler and library JARs
- It supports configurable versions per resolve via `ScalaSubsystem`

For Clojure, we only need the isolation aspect - Clojure has a single JAR that serves as both compiler and runtime. We don't need `-bootclasspath` or version configuration (though those could be added later as enhancements).

### Future Enhancement: Configurable Clojure Version

A potential future enhancement would be to add a `ClojureSubsystem` with `version_for_resolve()` method, similar to Scala. This would allow users to specify:

```toml
[clojure]
version_for_resolve = { "jvm-default" = "1.12.0", "legacy" = "1.10.3" }
```

This is NOT required for the bug fix and can be done separately.

## Risks and Mitigations

### Risk 1: Breaking existing tests
**Mitigation**: Run full test suite after each phase. The change is isolated to how digests are organized, not the actual compilation logic.

### Risk 2: Tool Clojure version mismatch with user code
**Mitigation**: The tool Clojure is only used for compilation/checking. The final JAR and runtime classpath will use the user's Clojure from their resolve. This matches how Clojure development typically works.

### Risk 3: Performance impact
**Mitigation**: The `extra_immutable_input_digests` pattern is well-tested in Scala and other backends. Digests are cached, so there's minimal overhead.

## Success Criteria

1. `test_provided_maven_transitives_excluded_from_jar` passes with `clojure_source` directly depending on `jvm_artifact(clojure)`
2. All existing tests continue to pass
3. Users can have `clojure_source` targets depend on `org.clojure:clojure` without scheduler hangs
4. AOT compilation and check goal work correctly with the isolated tool classpath
