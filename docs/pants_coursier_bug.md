# Pants Coursier Transitive Dependency Bug

## Summary

Pants' `fetch_with_coursier()` function fails to recursively resolve transitive dependencies from lockfiles when multiple top-level `jvm_artifact` targets share dependencies. This causes missing JARs on the classpath at runtime.

## The Problem

### Lockfile Optimization by Coursier

When a Coursier lockfile contains multiple top-level artifacts, Coursier optimizes the lockfile structure by omitting transitive dependencies that are already defined elsewhere as top-level artifacts.

**Example 1: Linear Chain (A → B → C)**

Dependency chain: `spring-webmvc → spring-web → spring-core`

```toml
# Entry for spring-webmvc
[[entries]]
[entries.coord]
group = "org.springframework"
artifact = "spring-webmvc"
version = "5.3.23"

[[entries.dependencies]]
group = "org.springframework"
artifact = "spring-web"
version = "5.3.23"
# NOTE: spring-core is NOT listed here, even though spring-webmvc transitively needs it

# Entry for spring-web
[[entries]]
[entries.coord]
group = "org.springframework"
artifact = "spring-web"
version = "5.3.23"

[[entries.dependencies]]
group = "org.springframework"
artifact = "spring-core"
version = "5.3.23"
# spring-core is only listed in spring-web's dependencies

# Entry for spring-core
[[entries]]
[entries.coord]
group = "org.springframework"
artifact = "spring-core"
version = "5.3.23"

[[entries.dependencies]]
# (no relevant transitive deps for this example)
```

**Example 2: Diamond Dependencies (A → C, B → C)**

Both `spring-context` and `spring-beans` depend on `spring-core`:

```toml
# Entry for spring-context
[[entries]]
[entries.coord]
group = "org.springframework"
artifact = "spring-context"
version = "5.3.23"

[[entries.dependencies]]
group = "org.springframework"
artifact = "spring-beans"
version = "5.3.23"
# NOTE: spring-core is NOT duplicated here (it's in spring-beans.dependencies)

# Entry for spring-beans
[[entries]]
[entries.coord]
group = "org.springframework"
artifact = "spring-beans"
version = "5.3.23"

[[entries.dependencies]]
group = "org.springframework"
artifact = "spring-core"
version = "5.3.23"

# Entry for spring-core
[[entries]]
[entries.coord]
group = "org.springframework"
artifact = "spring-core"
version = "5.3.23"

[[entries.dependencies]]
# (shared by both spring-context and spring-beans)
```

Coursier's optimization assumes:
- If you depend on `spring-webmvc`, you'll get `spring-web` from its dependencies
- If you need `spring-core`, you'll either:
  1. Explicitly depend on the `spring-web` or `spring-core` jvm_artifact targets, OR
  2. The build system will recursively expand spring-web's dependencies

### Pants' Current Behavior (Bug)

In `pants/src/python/pants/jvm/resolve/coursier_fetch.py:667-673`:

```python
@rule(desc="Fetch with coursier")
async def fetch_with_coursier(request: CoursierFetchRequest) -> FallibleClasspathEntry:
    lockfile = await get_coursier_lockfile_for_resolve(request.resolve)
    requirement = ArtifactRequirement.from_jvm_artifact_target(request.component.representative)

    # Gets ONLY the entries listed in spring-webmvc.dependencies = [spring-web]
    root_entry, transitive_entries = lockfile.dependencies(
        request.resolve,
        requirement.coordinate,
    )

    # Fetches: spring-webmvc.jar, spring-web.jar
    # MISSING: spring-core.jar (spring-web's transitive dependency)
    classpath_entries = await concurrently(
        coursier_fetch_one_coord(entry) for entry in (root_entry, *transitive_entries)
    )

    return FallibleClasspathEntry(
        description=str(request.component),
        result=CompileResult.SUCCEEDED,
        output=ClasspathEntry.merge(exported_digest, classpath_entries),
        exit_code=0,
    )
```

**What happens:**
1. Code depends on `spring-webmvc` jvm_artifact
2. Pants calls `lockfile.dependencies(spring-webmvc)` → returns `[spring-web]`
3. Pants fetches `spring-webmvc.jar` and `spring-web.jar`
4. **BUG**: Pants does NOT recursively fetch spring-web's dependencies (`spring-core.jar`)
5. At runtime: `ClassNotFoundException` for spring-core classes

### When This Occurs

This bug manifests when:
1. Multiple `jvm_artifact` targets are defined for libraries with shared transitive dependencies
2. Code depends on artifact A, which transitively depends on artifact B
3. Artifact B is also a top-level `jvm_artifact` in the lockfile
4. Coursier optimizes by omitting B's transitive dependencies from A's `dependencies` field

**Real-world example:**
```python
# BUILD file
jvm_artifact(name="org.springframework_spring-webmvc", ...)  # spring-webmvc
jvm_artifact(name="org.springframework_spring-web", ...)     # spring-web
jvm_artifact(name="org.springframework_spring-core", ...)    # spring-core

java_sources(
    name='myapp',
    dependencies=['3rdparty/jvm:org.springframework_spring-webmvc'],  # Only depends on webmvc
)
```

The app depends on spring-webmvc, which needs spring-web, which needs spring-core. But `spring-core.jar` is missing from the classpath.

## Root Cause Analysis

### The `dependencies()` Method

In `pants/src/python/pants/jvm/resolve/coursier_fetch.py:238-259`:

```python
def dependencies(
    self, key: CoursierResolveKey, coord: Coordinate
) -> tuple[CoursierLockfileEntry, tuple[CoursierLockfileEntry, ...]]:
    """Return the entry for the given Coordinate, and for its transitive dependencies."""
    entries = {(i.coord.group, i.coord.artifact, i.coord.classifier): i for i in self.entries}
    entry = entries.get((coord.group, coord.artifact, coord.classifier))
    if entry is None:
        raise self._coordinate_not_found(key, coord)

    # Returns ONLY what's in entry.dependencies field
    return (
        entry,
        tuple(
            dependency_entry
            for d in entry.dependencies
            if (dependency_entry := entries.get((d.group, d.artifact, d.classifier)))
            is not None
        ),
    )
```

**Problem**: This method name says "transitive dependencies" but it only returns the **immediate** dependencies listed in the lockfile entry's `dependencies` field. It does NOT recursively expand those dependencies.

### Design Assumptions Mismatch

**Coursier's assumption**: The `dependencies` field can be optimized when multiple top-level artifacts exist, because the build system will either:
- Have explicit dependencies on all needed artifacts, OR
- Recursively expand transitive dependencies

**Pants' assumption**: The `dependencies` field contains the complete transitive closure, so no recursive expansion is needed.

**Reality**: Neither assumption is always true, leading to this bug.

## The Fix

### Solution: Add Recursive Transitive Closure Method

Add a new method `transitive_closure()` to `CoursierResolvedLockfile` that recursively computes the full dependency graph:

```python
def transitive_closure(
    self, key: CoursierResolveKey, coord: Coordinate
) -> tuple[CoursierLockfileEntry, tuple[CoursierLockfileEntry, ...]]:
    """Return the entry for the given Coordinate, and its full transitive closure.

    This method recursively expands dependencies to compute the full transitive closure,
    which is necessary when the lockfile contains multiple top-level artifacts that share
    transitive dependencies. In such cases, Coursier may optimize the lockfile by not
    including all transitive dependencies in each artifact's 'dependencies' field.
    """
    from collections import deque

    entries = {(i.coord.group, i.coord.artifact, i.coord.classifier): i for i in self.entries}
    entry = entries.get((coord.group, coord.artifact, coord.classifier))
    if entry is None:
        raise self._coordinate_not_found(key, coord)

    # Breadth-first traversal to compute transitive closure
    visited = set()
    queue = deque([entry])
    transitive_deps = []

    while queue:
        current_entry = queue.popleft()
        entry_key = (
            current_entry.coord.group,
            current_entry.coord.artifact,
            current_entry.coord.classifier,
        )

        # Skip if already visited (handles cycles and duplicates)
        if entry_key in visited:
            continue
        visited.add(entry_key)

        # Don't include the root entry itself in transitive dependencies list
        if current_entry != entry:
            transitive_deps.append(current_entry)

        # Add all dependencies to queue for processing
        for dep_coord in current_entry.dependencies:
            dep_key = (dep_coord.group, dep_coord.artifact, dep_coord.classifier)
            if dep_key not in visited:
                dep_entry = entries.get(dep_key)
                if dep_entry is not None:
                    queue.append(dep_entry)
                # Else: Skip missing dependencies (Coursier bug #2884 workaround)

    return (entry, tuple(transitive_deps))
```

### Update `fetch_with_coursier()`

Change line 667 in `fetch_with_coursier()` from:
```python
root_entry, transitive_entries = lockfile.dependencies(
    request.resolve,
    requirement.coordinate,
)
```

To:
```python
root_entry, transitive_entries = lockfile.transitive_closure(
    request.resolve,
    requirement.coordinate,
)
```

### Why This Works

**Before (broken):**
1. Fetch spring-webmvc → `lockfile.dependencies(spring-webmvc)` → returns `[spring-web]`
2. Fetch `spring-webmvc.jar`, `spring-web.jar`
3. Missing: `spring-core.jar`

**After (fixed):**
1. Fetch spring-webmvc → `lockfile.transitive_closure(spring-webmvc)` → returns `[spring-web, spring-core]`
2. Process:
   - Start with spring-webmvc
   - Add spring-webmvc's dependencies to queue: `[spring-web]`
   - Process spring-web: add spring-web's dependencies to queue: `[spring-core]`
   - Process spring-core: no more dependencies
3. Fetch `spring-webmvc.jar`, `spring-web.jar`, `spring-core.jar`
4. Complete classpath ✓

## Implementation Plan

1. **Add `transitive_closure()` method** to `CoursierResolvedLockfile` class
   - File: `pants/src/python/pants/jvm/resolve/coursier_fetch.py`
   - Location: After the `dependencies()` method (around line 260)

2. **Update `fetch_with_coursier()` rule**
   - File: `pants/src/python/pants/jvm/resolve/coursier_fetch.py`
   - Line: 667
   - Change: Use `transitive_closure()` instead of `dependencies()`

3. **Test the fix**
   - Run: `pants test pants-plugins/tests/test_dependency_inference_integration.py::test_infer_transitive_clojure_dependencies`
   - Expected: Test passes with all transitive dependencies on classpath

4. **Run Pants' own test suite**
   - Ensure no regressions in existing JVM tests
   - Particularly test `pants/src/python/pants/jvm/resolve/coursier_fetch_test.py`

## Alternative Approaches Considered

### 1. Fix the lockfile generation
**Idea**: Force Coursier to always include complete transitive closure in `dependencies` field.

**Rejected because**:
- This is Coursier's intentional optimization behavior
- Would require modifying Coursier upstream
- Lockfiles would become larger and more redundant

### 2. Make dependencies explicit in BUILD files
**Idea**: Always explicitly depend on all transitive dependencies.

**Rejected because**:
- Defeats the purpose of transitive dependency management
- Requires users to manually track transitive dependencies
- Fragile and error-prone

### 3. Modify the Clojure backend
**Idea**: Add special handling in the Clojure backend to expand dependencies.

**Rejected because**:
- This is a general Pants issue, not Clojure-specific
- Would not fix the issue for other JVM languages
- Duplicates logic that should be in Pants core

## Impact Assessment

### Who is affected?
- Any Pants project using JVM dependencies with shared transitive dependencies
- Most commonly seen with:
  - Clojure projects (due to inferred dependencies)
  - Projects with many third-party libraries
  - Monorepos with multiple jvm_artifact targets

### Severity
- **High**: Causes runtime ClassNotFoundException errors
- Hard to debug (missing JARs are transitive, not direct dependencies)
- Can only be worked around by adding explicit dependencies

### Backward Compatibility
- **Safe**: This change only affects `fetch_with_coursier()` behavior
- Adds more JARs to classpath (safe, may be redundant in some cases)
- No API changes
- Existing lockfiles work correctly with this fix

## Testing Strategy

### Unit Tests
Test the new `transitive_closure()` method with:
- Simple linear dependencies: A → B → C
- Shared dependencies: A → C, B → C
- Diamond dependencies: A → B, A → C, B → D, C → D
- Cycles (should be handled by visited set)
- Missing entries (Coursier bug #2884 workaround)

### Integration Tests
- Use the existing `test_infer_transitive_clojure_dependencies` test
- Add similar tests for Java/Scala projects with shared transitive dependencies

### Regression Tests
- Run full Pants test suite, especially JVM-related tests
- Ensure no performance degradation (BFS algorithm is O(V+E))

## References

- Coursier Issue #2884: https://github.com/coursier/coursier/issues/2884
- Pants JVM classpath resolution: `pants/src/python/pants/jvm/classpath.py`
- ClasspathEntry.closure() algorithm: `pants/src/python/pants/jvm/compile.py:302-313`
- Test that reproduces the bug: `pants-plugins/tests/test_dependency_inference_integration.py:258`

## Timeline

1. **Implement fix**: ~1 hour
2. **Test locally**: ~30 minutes
3. **Run full Pants test suite**: ~1 hour
4. **Submit PR to Pants**: TBD
5. **Review and merge**: TBD

## Notes

This bug has likely existed since Pants added Coursier lockfile support. It becomes more apparent in projects with:
- Many third-party dependencies
- Dependency inference (like Clojure backend)
- Complex dependency graphs

The fix is straightforward and follows the same BFS pattern already used in `ClasspathEntry.closure()`.
