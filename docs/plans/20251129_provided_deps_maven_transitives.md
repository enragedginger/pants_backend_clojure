# Implementation Plan: Provided Dependencies Maven Transitive Exclusion

**Date:** 2025-11-29
**Bug:** `provided` field does not exclude Maven transitive dependencies

## Summary

The `provided` field on `clojure_deploy_jar` only excludes the directly specified `jvm_artifact` target, but does not exclude its Maven transitive dependencies. This results in much larger JARs than expected.

### Current Behavior

```python
jvm_artifact(name="rama", group="com.rpl", artifact="rama", version="1.0.0")

clojure_deploy_jar(
    name="app",
    main="my.app",
    dependencies=[":rama", ":sources"],
    provided=[":rama"],  # Expects rama + all its transitives excluded
)
```

- The `com.rpl:rama` JAR is excluded
- Its transitive dependencies (rama-shaded-deps, rama-shaded-asm, netty, zookeeper, curator, etc.) are **still bundled**

### Impact

- JAR with provided working correctly: ~25MB (estimated)
- JAR with current behavior: 121MB
- JAR without provided: 217MB

The 96MB bloat is from transitive Maven dependencies that should be excluded.

## Root Cause Analysis

### The Problem

In `provided_dependencies.py`, the rule uses `TransitiveTargetsRequest` which only traverses the **Pants target graph**. Maven transitive dependencies resolved by Coursier don't have corresponding Pants targets - they exist only in the lockfile.

```python
# Current implementation (provided_dependencies.py:61-64)
all_transitive = await MultiGet(
    Get(TransitiveTargets, TransitiveTargetsRequest([target.address]))
    for target in provided_targets
)
```

This retrieves:
- `jvm_artifact` targets explicitly declared in BUILD files
- `clojure_source` targets and their dependencies

This does NOT retrieve:
- Maven transitive dependencies only present in the lockfile
- Dependencies like `com.google.guava:guava` bringing in `jsr305`, `error_prone_annotations`, `failureaccess`, etc.

### Lockfile Structure

The lockfile contains complete dependency information:

```toml
[[entries]]
file_name = "com.google.guava_guava_31.1-jre.jar"

[[entries.directDependencies]]
group = "com.google.code.findbugs"
artifact = "jsr305"
version = "3.0.2"

[[entries.dependencies]]
group = "com.google.code.findbugs"
artifact = "jsr305"
version = "3.0.2"
# ... more dependencies

[entries.coord]
group = "com.google.guava"
artifact = "guava"
version = "31.1-jre"
```

Each entry has:
- `coord`: The Maven coordinate (group, artifact, version)
- `dependencies`: Full list of transitive dependencies
- `directDependencies`: Only immediate dependencies

---

## Solution Design

### Key Discovery from Pants Core Analysis

Analysis of the Pants core repo (`/Users/hopper/workspace/python/pants`) revealed that **the lockfile already contains pre-computed transitive closures**:

- `CoursierLockfileEntry.dependencies` contains the **full transitive closure** (not just immediate deps)
- `CoursierLockfileEntry.direct_dependencies` contains only immediate dependencies
- `CoursierResolvedLockfile.dependencies(key, coord)` returns the entry and all transitive entries

This means **we don't need BFS traversal** - we can simply look up each provided artifact in the lockfile and iterate its `dependencies` field.

### Approach

Enhance `resolve_provided_dependencies` to:

1. First, resolve Pants target graph transitives (existing behavior)
2. Then, for each `jvm_artifact` coordinate found, look up its entry in the lockfile
3. Add all coordinates from `entry.dependencies` to the result (these are pre-computed transitives)
4. Return the expanded coordinate set in `ProvidedDependencies.coordinates`

### Key Components

1. **Lockfile Access**: Use `JvmSubsystem.resolves` to get the lockfile path for the resolve
2. **Entry Lookup**: Build a lookup dict from lockfile entries by `(group, artifact)`
3. **Transitive Extraction**: Iterate `entry.dependencies` for each provided artifact (already the full closure)

---

## Implementation Plan

### Phase 1: Create Request Type and Lockfile Helper ✅ DONE

**Goal:** Define a request type that carries the resolve name, and create the lockfile helper function

**File:** `pants-plugins/clojure_backend/provided_dependencies.py`

**Changes:**

1. Add new imports:
   ```python
   from pants.engine.fs import Digest, DigestContents, PathGlobs
   from pants.jvm.resolve.coursier_fetch import CoursierResolvedLockfile, CoursierLockfileEntry
   from pants.jvm.subsystems import JvmSubsystem
   ```

2. Create a new request dataclass:
   ```python
   @dataclass(frozen=True)
   class ResolveProvidedDependenciesRequest:
       """Request to resolve provided dependencies for a specific JVM resolve."""
       field: ClojureProvidedDependenciesField
       resolve_name: str | None  # None when only first-party sources are provided
   ```

3. Add a helper function to expand Maven transitives (simple iteration, no BFS):
   ```python
   def get_maven_transitive_coordinates(
       lockfile: CoursierResolvedLockfile,
       coordinates: set[tuple[str, str]]
   ) -> set[tuple[str, str]]:
       """Get full transitive closure of Maven coordinates from lockfile.

       Simply looks up each coordinate in the lockfile and collects the
       pre-computed transitive dependencies from entry.dependencies.
       No graph traversal needed - Coursier pre-computes the full closure.
       """
   ```

**Testing:** Unit tests for the helper function with mock lockfile entries

### Phase 2: Update resolve_provided_dependencies Rule ✅ DONE

**Goal:** Integrate lockfile lookup into the existing rule

**File:** `pants-plugins/clojure_backend/provided_dependencies.py`

**Changes:**

1. Change rule to accept the new request type:
   ```python
   @rule
   async def resolve_provided_dependencies(
       request: ResolveProvidedDependenciesRequest,
       jvm: JvmSubsystem,
   ) -> ProvidedDependencies:
   ```

2. Handle the case when `request.resolve_name` is None (no jvm_artifacts in provided):
   - Skip lockfile lookup entirely
   - Return only Pants target-based coordinates (existing behavior)

3. When `resolve_name` is provided, load and parse the lockfile:
   ```python
   if request.resolve_name and coordinates:
       lockfile_path = jvm.resolves[request.resolve_name]
       lockfile_digest = await Get(Digest, PathGlobs([lockfile_path]))
       lockfile_contents = await Get(DigestContents, Digest, lockfile_digest)
       lockfile = CoursierResolvedLockfile.from_serialized(lockfile_contents[0].content)

       # Expand coordinates with Maven transitives
       coordinates = get_maven_transitive_coordinates(lockfile, coordinates)
   ```

4. Return expanded coordinates in `ProvidedDependencies`

### Phase 2b: Update Caller in package.py ✅ DONE

**Goal:** Update the JAR packaging code to create the request with resolve name

**File:** `pants-plugins/clojure_backend/goals/package.py`

**Changes:**

1. Determine the resolve name from the deploy JAR target:
   ```python
   resolve_name = field_set.resolve.normalized_value(jvm)
   ```

2. Create the request with both field and resolve:
   ```python
   provided_deps = await Get(
       ProvidedDependencies,
       ResolveProvidedDependenciesRequest(field_set.provided, resolve_name),
   )
   ```

This ensures the rule has access to the correct resolve without needing to infer it from targets.

### Phase 3: Add Unit Tests ✅ DONE

**Goal:** Comprehensive test coverage for Maven transitive resolution

**File:** `pants-plugins/tests/test_provided_dependencies.py`

**Test Cases:**

1. **test_maven_transitive_simple**: Single jvm_artifact with one level of transitives
2. **test_maven_transitive_deep**: Multi-level transitive chain (A → B → C)
3. **test_maven_transitive_diamond**: Diamond dependency (A → B, A → C, B → D, C → D)
4. **test_maven_transitive_with_first_party**: Mix of first-party sources and third-party with transitives
5. **test_empty_lockfile_transitives**: jvm_artifact with no Maven transitives in lockfile

**Testing Approach:**
- Create mock lockfiles with known dependency structures
- Verify that `ProvidedDependencies.coordinates` contains all expected transitives

### Phase 4: Integration Testing ✅ DONE

**Goal:** Verify end-to-end JAR packaging excludes Maven transitives

**File:** `pants-plugins/tests/test_package_clojure_deploy_jar.py`

**Test Cases:**

1. Add a test that uses a `jvm_artifact` with known Maven transitives
2. Mark it as provided
3. Verify the output JAR does NOT contain the transitive JARs' contents

**Approach:**
- Use the existing test lockfile or create a test-specific one
- Pick an artifact like `guava` that has well-known transitives
- Inspect the JAR contents to verify exclusion

### Phase 5: Documentation Update ✅ DONE

**Goal:** Update help text and documentation

**Files:**
- `pants-plugins/clojure_backend/target_types.py` - Update `ClojureProvidedDependenciesField.help`
- `docs/provided_dependencies.md` - Update documentation to explain Maven transitive behavior

**Changes:**
- Clarify that Maven transitive dependencies are now automatically excluded
- Add examples showing the difference in JAR sizes

---

## Technical Details

### Simplified Transitive Lookup (No BFS Needed!)

After analyzing the Pants core repo, we discovered that **Coursier pre-computes the full transitive closure** and stores it in each lockfile entry's `dependencies` field. This means we don't need BFS/DFS traversal - we simply look up each coordinate and collect its pre-computed transitives.

```python
def get_maven_transitive_coordinates(
    lockfile: CoursierResolvedLockfile,
    coordinates: set[tuple[str, str]]
) -> set[tuple[str, str]]:
    """Get full transitive closure of Maven coordinates from lockfile.

    Args:
        lockfile: The parsed Coursier lockfile containing all entries
        coordinates: The initial set of (group, artifact) coordinates to expand

    Returns:
        The expanded set including all transitive Maven dependencies
    """
    # Build lookup dictionary: (group, artifact) -> entry
    # Note: We ignore version since provided uses version-agnostic matching.
    entries_by_coord: dict[tuple[str, str], CoursierLockfileEntry] = {}
    for entry in lockfile.entries:
        key = (entry.coord.group, entry.coord.artifact)
        entries_by_coord[key] = entry

    # Collect transitives - no BFS needed since entry.dependencies is already
    # the full transitive closure pre-computed by Coursier
    result = set(coordinates)
    for coord in coordinates:
        entry = entries_by_coord.get(coord)
        if entry is None:
            # Coordinate not in lockfile - skip silently
            # This can happen if the provided artifact isn't in the resolve
            continue

        # entry.dependencies is the FULL transitive closure, not just direct deps
        for dep in entry.dependencies:
            result.add((dep.group, dep.artifact))

    return result
```

**Key implementation notes:**

1. **No graph traversal needed**: `CoursierLockfileEntry.dependencies` already contains the complete transitive closure. Coursier computes this during `pants generate-lockfiles`.

2. **Version-agnostic matching**: We use `(group, artifact)` tuples without version because `provided` scope in Maven semantics excludes all versions of an artifact.

3. **Missing entries handled gracefully**: If a coordinate isn't found in the lockfile, we skip it silently. This handles the case where a jvm_artifact is defined in BUILD but not in the current resolve's lockfile.

4. **Simple iteration**: One pass through the provided coordinates, collecting each entry's pre-computed transitives.

### Pants Core API Reference

From `/Users/hopper/workspace/python/pants/src/python/pants/jvm/resolve/coursier_fetch.py`:

```python
@dataclass(frozen=True)
class CoursierLockfileEntry:
    coord: Coordinate
    file_name: str
    direct_dependencies: Coordinates  # Immediate deps only
    dependencies: Coordinates         # FULL transitive closure (pre-computed)
    file_digest: FileDigest
    remote_url: str | None = None
    pants_address: str | None = None
```

The `CoursierResolvedLockfile.dependencies()` method (lines 238-257) also returns full transitives:
```python
def dependencies(
    self, key: CoursierResolveKey, coord: Coordinate
) -> tuple[CoursierLockfileEntry, tuple[CoursierLockfileEntry, ...]]:
    """Return the entry for the given Coordinate, and for its transitive dependencies."""
```

### Handling Multiple Resolves

If `provided_targets` contains jvm_artifacts from different resolves, we need to handle this gracefully:

1. Group jvm_artifact targets by their resolve
2. Load each resolve's lockfile once
3. Expand transitives for each resolve separately
4. Combine all coordinates into the result

For the initial implementation, we can assume all jvm_artifacts in `provided` use the same resolve (validate and error if not).

### Edge Cases

1. **No jvm_artifacts in provided**: Skip lockfile lookup entirely (only first-party sources)
2. **jvm_artifact not in lockfile**: Log warning, skip that artifact's transitives
3. **Circular dependencies**: BFS with visited set handles this
4. **Missing resolve**: Error with helpful message

---

## Files to Modify

| File | Changes |
|------|---------|
| `pants-plugins/clojure_backend/provided_dependencies.py` | Add request type, lockfile lookup logic, BFS helper |
| `pants-plugins/clojure_backend/goals/package.py` | Update to create request with resolve name |
| `pants-plugins/tests/test_provided_dependencies.py` | Add Maven transitive tests |
| `pants-plugins/tests/test_package_clojure_deploy_jar.py` | Add integration tests |
| `pants-plugins/clojure_backend/target_types.py` | Update help text |
| `docs/provided_dependencies.md` | Update documentation |

## Dependencies

- No external dependencies
- Uses existing Pants APIs: `CoursierResolvedLockfile`, `JvmSubsystem`

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Performance impact from lockfile parsing | Lockfile is already loaded by Pants; parsing is fast |
| Breaking existing behavior | Add tests for current behavior first, ensure they still pass |
| Multiple resolve edge case | Validate all jvm_artifacts use same resolve, error if not |

## Success Criteria

1. Unit tests pass for Maven transitive resolution
2. Integration test verifies JAR size reduction
3. Real-world test with `rama` artifact shows expected ~25MB JAR instead of 121MB
4. No regression in existing `provided` behavior for first-party sources
