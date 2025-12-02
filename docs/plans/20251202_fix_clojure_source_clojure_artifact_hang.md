# Plan: Fix `clojure_source` -> `jvm_artifact(clojure)` Dependency Hang

## Status: BLOCKED - Original Plan Does Not Work

**Update (2024-12-02):** Implementation of the original plan (Option B - isolated tool classpath via `extra_immutable_input_digests`) was attempted but **does not solve the hang**. The issue is deeper than digest merging - it occurs during the Pants scheduler's Coursier resolution phase. See "Investigation Findings" section below for details.

---

## Problem Summary

When a `clojure_source` target directly depends on a `jvm_artifact` for `org.clojure:clojure`, the Pants scheduler hangs indefinitely. This happens because:

1. **AOT compilation** (`aot_compile.py`) and **check goal** (`check.py`) fetch Clojure via `ToolClasspathRequest` with a hardcoded `DEFAULT_CLOJURE_VERSION`
2. The user's dependency graph also includes `jvm_artifact(clojure)`
3. When Pants resolves the classpath, it encounters conflicting resolution paths for the same artifact
4. This creates a deadlock in the Pants scheduler

This is a real limitation because in production Clojure projects, it's common and reasonable for `clojure_source` targets to depend on `org.clojure:clojure` (e.g., to access specific Clojure features or ensure version compatibility).

---

## Investigation Findings (2024-12-02)

### What Was Attempted

The original plan proposed using `extra_immutable_input_digests` to isolate tool and user classpaths, following the Scala backend pattern. Changes were made to:

1. `aot_compile.py` - Use `extra_immutable_input_digests` for tool classpath, use `classpath.immutable_inputs()` for user classpath
2. `check.py` - Same pattern as above
3. `test_package_clojure_deploy_jar.py` - Update test to have `clojure_source` depend on `jvm_artifact(clojure)`

**Result:** The test still hangs indefinitely.

### Why the Original Plan Fails

The `extra_immutable_input_digests` pattern only prevents **digest merging conflicts** at the process execution level. However, the hang occurs **earlier** during the Pants scheduler's **Coursier resolution phase**.

#### The Actual Root Cause

When both paths try to fetch `org.clojure:clojure`:

**Path 1: User Classpath Resolution**
- `classpath_get()` → `select_coursier_resolve_for_targets()` → loads user's lockfile
- Calls `coursier_fetch_one_coord()` with a `CoursierLockfileEntry` that has `pants_address` set to the `jvm_artifact` target

**Path 2: Tool Classpath Resolution**
- `ToolClasspathRequest(artifact_requirements=...)` → `coursier_resolve_lockfile()` → creates fresh lockfile entries
- Calls `coursier_fetch_one_coord()` with a `CoursierLockfileEntry` that has NO `pants_address`

The **cache key** for `coursier_fetch_one_coord()` is based on the entire `CoursierLockfileEntry` object, which includes `pants_address`. This means:

1. Two **different** cache keys are created for the **same** coordinate
2. Both requests trigger separate `coursier_fetch_one_coord()` calls
3. The scheduler encounters a conflict or cycle when trying to resolve both

#### Evidence from Pants Codebase

From `/Users/hopper/workspace/python/pants/src/python/pants/jvm/resolve/coursier_fetch.py`:

- Lines 551-642: `coursier_fetch_one_coord()` takes `CoursierLockfileEntry` as input
- Lines 575-583: If `request.pants_address` is set, it tries to `resolve_targets()`
- Lines 785-841: `materialize_classpath_for_tool()` creates entries without `pants_address`

This creates a situation where:
- User classpath fetch (with `pants_address`) may trigger target resolution
- Tool classpath fetch (without `pants_address`) takes a different code path
- Both are trying to fetch the same artifact, creating scheduler confusion

### Why Scala Doesn't Have This Problem

Looking at the Scala backend (`scalac.py`):

1. Scala **does** use `ToolClasspathRequest(artifact_requirements=...)` for the compiler (line 159)
2. However, Scala users typically **don't** have their `scala_source` targets explicitly depend on `jvm_artifact(scala-library)`
3. Instead, Scala has **built-in dependency inference** that automatically injects `scala-library` into the dependency graph
4. The injected dependency goes through a different code path that doesn't conflict

For Clojure, we're explicitly allowing users to depend on `jvm_artifact(clojure)`, which creates the conflict.

---

## Revised Solution Options

### Option A: Use Pre-Generated Lockfile for Clojure Tool (Recommended)

Create a `ClojureSubsystem` that extends `JvmToolBase` and uses a pre-generated lockfile for the Clojure runtime, similar to how `scala_parser.lock` works.

**How it works:**
```python
class ClojureSubsystem(JvmToolBase):
    options_scope = "clojure"
    help = "The Clojure runtime used for AOT compilation and checking."

    default_version = "1.11.1"
    default_artifacts = ("org.clojure:clojure:{version}",)
    default_lockfile_resource = ("clojure_backend", "clojure.lock")
```

Then in `aot_compile.py`:
```python
clojure_classpath = await Get(
    ToolClasspath,
    ToolClasspathRequest(lockfile=GenerateJvmLockfileFromTool.create(clojure_subsystem)),
)
```

**Pros:**
- Uses Pants' established pattern for tool dependencies
- Lockfile is resolved independently, avoiding the cache key conflict
- Users can customize the Clojure version via `pants.toml`

**Cons:**
- Requires creating and maintaining a lockfile
- More complex implementation
- Lockfile needs to be regenerated when Clojure version changes

### Option B: Don't Use ToolClasspathRequest - Rely on User's Classpath

Remove `ToolClasspathRequest` entirely and require Clojure to be available in the user's classpath (similar to how `test.py` works).

**How it works:**
```python
# In aot_compile.py - don't fetch Clojure separately
# Just use what's in the user's classpath
classpath = await classpath_get(**implicitly(request.source_addresses))

# Assume Clojure is already in classpath
classpath_entries = [
    ".",
    classes_dir,
    *classpath.immutable_inputs_args(prefix=usercp_relpath),
]
```

**Pros:**
- Simplest solution
- No scheduler conflicts possible
- User controls the Clojure version entirely

**Cons:**
- AOT compilation fails if user hasn't included Clojure in dependencies
- Poor UX - users must explicitly add `jvm_artifact(clojure)` to their deps
- Error messages may be confusing ("clojure.main not found")

### Option C: Automatic Dependency Injection (Like Scala)

Add automatic dependency injection for Clojure, similar to how Scala handles `scala-library`.

**How it works:**
- Create an `InferClojureDependencyRequest` rule
- Automatically inject `org.clojure:clojure` into every `clojure_source` target's dependencies
- Use the injected dependency instead of `ToolClasspathRequest`

**Pros:**
- Matches how Scala works
- Consistent user experience
- No need for explicit Clojure dependency in BUILD files

**Cons:**
- Most complex implementation
- Need to handle version conflicts
- May inject unwanted dependencies

### Option D: File Pants Bug / Upstream Fix

The underlying issue is arguably a bug in Pants' scheduler - it shouldn't hang when the same artifact is requested through different paths.

**Actions:**
- File an issue on the Pants repository
- Propose that `coursier_fetch_one_coord()` cache key should be based on coordinate only, not on `pants_address`
- Wait for upstream fix

**Pros:**
- Fixes the root cause
- Benefits all JVM backends

**Cons:**
- Depends on Pants maintainers
- Timeline unknown
- May take significant time

---

## Recommendation

**Short term:** Implement **Option B** (rely on user's classpath) as a quick fix, but with good error messages that guide users to add Clojure to their dependencies.

**Medium term:** Implement **Option A** (pre-generated lockfile) as the proper solution, following the pattern established by other Pants JVM tools.

**Long term:** Consider **Option D** (upstream fix) to address the root cause.

---

## Implementation Plan for Option A (Pre-Generated Lockfile)

### Phase 1: Create ClojureSubsystem

**New file:** `pants-plugins/clojure_backend/subsystems/clojure.py`

```python
from pants.jvm.resolve.jvm_tool import JvmToolBase

class ClojureSubsystem(JvmToolBase):
    options_scope = "clojure"
    help = "The Clojure runtime used for AOT compilation and checking."

    default_version = "1.11.1"
    default_artifacts = ("org.clojure:clojure:{version}",)
    default_lockfile_resource = ("clojure_backend", "clojure.lock")
    default_lockfile_path = "pants-plugins/clojure_backend/clojure.lock"
    default_lockfile_url = None  # We'll generate it locally
```

### Phase 2: Generate Lockfile

Run:
```bash
pants generate-lockfiles --resolve=clojure
```

This creates `pants-plugins/clojure_backend/clojure.lock` containing the resolved Clojure artifact.

### Phase 3: Update AOT Compilation

**File:** `pants-plugins/clojure_backend/aot_compile.py`

```python
from clojure_backend.subsystems.clojure import ClojureSubsystem
from pants.jvm.resolve.jvm_tool import GenerateJvmLockfileFromTool

@rule
async def aot_compile_clojure(
    request: CompileClojureAOTRequest,
    clojure: ClojureSubsystem,  # Inject subsystem
) -> CompiledClojureClasses:
    # Use lockfile-based tool classpath
    clojure_classpath = await Get(
        ToolClasspath,
        ToolClasspathRequest(lockfile=GenerateJvmLockfileFromTool.create(clojure)),
    )
    # ... rest of implementation
```

### Phase 4: Update Check Goal

**File:** `pants-plugins/clojure_backend/goals/check.py`

Same pattern as Phase 3.

### Phase 5: Update Tests and Verify

1. Restore the test to have `clojure_source` depend on `jvm_artifact(clojure)`
2. Run full test suite
3. Verify the hang is resolved

---

## Implementation Plan for Option B (Quick Fix)

### Phase 1: Remove ToolClasspathRequest from AOT Compilation

**File:** `pants-plugins/clojure_backend/aot_compile.py`

Remove the `ToolClasspathRequest` and rely on user's classpath containing Clojure.

### Phase 2: Remove ToolClasspathRequest from Check Goal

**File:** `pants-plugins/clojure_backend/goals/check.py`

Same as Phase 1.

### Phase 3: Add Error Handling

Add clear error messages when `clojure.main` is not found:

```python
if "clojure.main" not in classpath_entries:
    raise ValueError(
        "Clojure runtime not found in classpath. "
        "Please ensure your clojure_source targets depend on a jvm_artifact "
        "for org.clojure:clojure."
    )
```

### Phase 4: Update Documentation

Document that users must include Clojure in their dependencies for AOT compilation to work.

---

## Files Summary

| File | Action | Description |
|------|--------|-------------|
| `pants-plugins/clojure_backend/subsystems/clojure.py` | Create (Option A) | ClojureSubsystem extending JvmToolBase |
| `pants-plugins/clojure_backend/clojure.lock` | Generate (Option A) | Pre-generated lockfile for Clojure |
| `pants-plugins/clojure_backend/aot_compile.py` | Modify | Use lockfile-based ToolClasspathRequest or remove it |
| `pants-plugins/clojure_backend/goals/check.py` | Modify | Same as aot_compile.py |
| `pants-plugins/clojure_backend/register.py` | Modify | Register ClojureSubsystem rules |

---

## Original Plan (Archived - Does Not Work)

<details>
<summary>Click to expand original plan</summary>

### Option B: Use Isolated Tool Classpath (Scala Pattern) ~~✓ Selected~~ **DOES NOT WORK**

The Scala backend handles this exact problem by using `extra_immutable_input_digests` with directory prefixes to keep tool and user classpaths separate. `JvmProcess` in Pants supports this via the `extra_immutable_input_digests` parameter.

**Why this was expected to work:**
- Tool Clojure is kept in a separate directory (`__toolcp/`)
- User classpath uses the default path or a different prefix
- No digest merging conflict occurs because they're in separate filesystem locations
- The Pants scheduler sees them as unrelated operations

**Why it doesn't actually work:**
- The hang occurs during **Coursier fetch**, not during **digest merging**
- `extra_immutable_input_digests` only affects the process execution phase
- By the time we get to digest merging, the scheduler has already hung

</details>

---

## Success Criteria

1. `test_provided_maven_transitives_excluded_from_jar` passes with `clojure_source` directly depending on `jvm_artifact(clojure)`
2. All existing tests continue to pass
3. Users can have `clojure_source` targets depend on `org.clojure:clojure` without scheduler hangs
4. AOT compilation and check goal work correctly
