# Implementation Plan: Provided Dependencies for Clojure Deploy JAR

**Date**: 2025-11-27
**Status**: Complete
**Related**: [Previous Plan](20251123_compile_only_dependencies.md)

## Implementation Progress

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | Add JAR Filtering to Uberjar Creation | ✅ Complete |
| Phase 2 | Handle Transitive Dependencies via Lockfile | ⏭️ Deferred (simple approach works) |
| Phase 3 | Rename Field (compile_dependencies → provided) | ✅ Complete |
| Phase 4 | Documentation and Examples | ✅ Complete |

## Plan Overview

This plan revises the compile-only dependencies implementation to:
1. Fix the filtering mechanism to work with third-party `jvm_artifact` dependencies by matching on `groupId:artifactId` (ignoring version)
2. Rename `compile_dependencies` → `provided` to match Maven terminology (done last to avoid churn)

**Key Problem**: The current implementation has TWO issues:
1. The JAR extraction loop (lines 288-305 in `package.py`) processes ALL JARs without any filtering for provided dependencies
2. Address-based filtering doesn't work across different versions of the same Maven artifact

## Problem Statement

### The Actual Bug Location

**File**: `pants-plugins/clojure_backend/goals/package.py`, lines 288-305

```python
# Extract and add all dependency JARs
for file_content in digest_contents:
    if file_content.path.endswith('.jar'):
        # NO FILTERING HERE - THIS IS THE BUG
        try:
            jar_bytes = io.BytesIO(file_content.content)
            with zipfile.ZipFile(jar_bytes, 'r') as dep_jar:
                for item in dep_jar.namelist():
                    ...
```

This loop extracts ALL JARs into the uberjar without checking if they should be excluded as provided dependencies.

### What Currently Works vs What's Broken

| Component | Status | Notes |
|-----------|--------|-------|
| First-party Clojure source filtering | ✅ Works | Address-based filtering (lines 203-207) |
| First-party .class file filtering | ✅ Works | Namespace-based filtering (lines 314-326) |
| Third-party JAR filtering | ❌ Broken | No filtering in JAR extraction loop |
| Version-agnostic matching | ❌ Missing | Different versions = different addresses |

### Root Cause Analysis

1. **JAR extraction has no filtering**: The loop at lines 288-305 processes every `.jar` file in `digest_contents` without checking against provided dependencies

2. **Classpath includes all transitive deps**: When we call `Get(Classpath, Addresses, runtime_source_addresses)`, Pants resolves ALL transitive dependencies including `jvm_artifact` targets through the dependency graph, regardless of what addresses are in the filtered set

3. **Address filtering only affects source targets**: `runtime_source_addresses` only contains Clojure source targets, not JVM artifact targets directly

### Maven "Provided" Scope Behavior (What We Need)

- Match dependencies by `groupId:artifactId` (ignore version)
- Exclude the matched artifact AND its transitive dependencies that are EXCLUSIVELY reachable through provided paths
- Available at compile time, excluded from final artifact

---

## Proposed Solution

### Approach: Coordinate-Based JAR Filtering

Keep the existing address-based filtering for first-party Clojure sources (it works!) and ADD coordinate-based filtering for third-party JARs:

1. Extract Maven coordinates (`groupId:artifactId`) from provided `jvm_artifact` targets
2. Compute transitive closure of coordinates to exclude using lockfile dependency data
3. Build a mapping from JAR filenames to coordinates using the lockfile
4. Filter JARs during uberjar creation based on coordinate matching

**Critical Design Decision**: Only exclude artifacts that are EXCLUSIVELY reachable through provided dependency paths. If the same artifact is also needed by a non-provided dependency, it should NOT be excluded.

---

## Implementation Plan

### Phase 1: Add JAR Filtering to Uberjar Creation

**Goal**: Fix the immediate bug by adding coordinate-based JAR filtering.

**Files to modify**:
1. `pants-plugins/clojure_backend/goals/package.py`
2. `pants-plugins/clojure_backend/compile_dependencies.py`

**Tasks**:

1. **Extend `CompileOnlyDependencies` to include coordinates**:
   ```python
   @dataclass(frozen=True)
   class CompileOnlyDependencies:
       addresses: FrozenOrderedSet[Address]
       # NEW: Maven coordinates for third-party deps
       coordinates: FrozenOrderedSet[tuple[str, str]]  # (group_id, artifact_id)
   ```

2. **Update the resolution rule to extract coordinates**:
   - Reuse existing `TransitiveTargets` logic (it already works)
   - For each address that's a `jvm_artifact`, extract `group` and `artifact` fields
   - Build set of coordinate tuples to exclude

   ```python
   from pants.jvm.target_types import JvmArtifactArtifactField, JvmArtifactGroupField

   @rule
   async def resolve_compile_only_dependencies(
       field: ClojureCompileDependenciesField,
   ) -> CompileOnlyDependencies:
       # ... existing address resolution code ...

       # Extract coordinates from jvm_artifact targets
       coordinates = set()
       for target in all_targets:
           if target.has_field(JvmArtifactGroupField):
               group = target[JvmArtifactGroupField].value
               artifact = target[JvmArtifactArtifactField].value
               coordinates.add((group, artifact))

       return CompileOnlyDependencies(
           addresses=FrozenOrderedSet(sorted(all_addresses)),
           coordinates=FrozenOrderedSet(sorted(coordinates)),
       )
   ```

3. **Add JAR filtering in package.py**:

   At line ~287, before the JAR extraction loop, add filtering:
   ```python
   # Build set of JAR filenames to exclude based on coordinates
   # JAR filenames typically follow: {artifact}-{version}.jar pattern
   excluded_jar_prefixes = set()
   for group, artifact in compile_only_deps.coordinates:
       # Match JAR files that start with the artifact name
       excluded_jar_prefixes.add(f"{artifact}-")

   # Extract and add all dependency JARs (EXCEPT provided ones)
   for file_content in digest_contents:
       if file_content.path.endswith('.jar'):
           jar_filename = os.path.basename(file_content.path)

           # Check if this JAR should be excluded
           should_exclude = False
           for prefix in excluded_jar_prefixes:
               if jar_filename.startswith(prefix):
                   should_exclude = True
                   break

           if should_exclude:
               continue  # Skip provided dependency JAR

           # ... existing JAR extraction code ...
   ```

**Validation**:
- Integration test with `jvm_artifact` as provided dependency
- Verify provided JAR is NOT in final uberjar
- Verify non-provided JARs ARE in final uberjar
- Existing first-party filtering tests still pass

---

### Phase 2: Handle Transitive Dependencies via Lockfile

**Goal**: Properly exclude transitive dependencies of provided artifacts using lockfile data.

**Files to modify**:
1. `pants-plugins/clojure_backend/compile_dependencies.py`
2. `pants-plugins/clojure_backend/goals/package.py`

**Tasks**:

1. **Load the Coursier lockfile for coordinate mapping**:
   ```python
   from pants.jvm.resolve.coursier_fetch import CoursierResolvedLockfile
   from pants.jvm.resolve.jvm_tool import rules as jvm_tool_rules

   @rule
   async def build_jar_to_coordinate_mapping(
       lockfile: CoursierResolvedLockfile,
   ) -> JarCoordinateMapping:
       """Build mapping from JAR filename to (group, artifact) coordinate."""
       mapping = {}
       for entry in lockfile.entries:
           # entry.file_name is like "lib-1.0.jar"
           # entry.coord has group, artifact, version
           mapping[entry.file_name] = (entry.coord.group, entry.coord.artifact)
       return JarCoordinateMapping(mapping)
   ```

2. **Compute transitive closure of coordinates to exclude**:
   ```python
   def compute_transitive_exclusions(
       provided_coords: set[tuple[str, str]],
       lockfile: CoursierResolvedLockfile,
       all_required_coords: set[tuple[str, str]],  # coords needed by non-provided deps
   ) -> set[tuple[str, str]]:
       """
       Compute coordinates to exclude, respecting shared dependencies.

       Only exclude artifacts EXCLUSIVELY reachable through provided paths.
       If an artifact is also needed by a non-provided dependency, keep it.
       """
       # Build dependency graph from lockfile
       deps_of = {}  # coord -> set of direct dependency coords
       for entry in lockfile.entries:
           coord = (entry.coord.group, entry.coord.artifact)
           deps_of[coord] = {
               (dep.group, dep.artifact) for dep in entry.direct_dependencies
           }

       # BFS to find all transitive deps of provided coords
       to_exclude = set(provided_coords)
       queue = list(provided_coords)
       while queue:
           coord = queue.pop(0)
           for dep in deps_of.get(coord, []):
               if dep not in to_exclude:
                   to_exclude.add(dep)
                   queue.append(dep)

       # Remove any coords that are also required by non-provided deps
       to_exclude -= all_required_coords

       return to_exclude
   ```

3. **Get the lockfile in package.py**:
   ```python
   from pants.jvm.resolve.coursier_fetch import CoursierResolvedLockfile
   from pants.jvm.resolve.common import ArtifactRequirements

   # In package_clojure_deploy_jar rule:
   lockfile = await Get(
       CoursierResolvedLockfile,
       ArtifactRequirements,
       # Build requirements from the resolve
   )
   ```

**Technical Details - Lockfile Access**:

The lockfile can be accessed via:
```python
from pants.jvm.resolve.coursier_fetch import (
    CoursierResolvedLockfile,
    CoursierLockfileForTargetRequest,
)

lockfile = await Get(
    CoursierResolvedLockfile,
    CoursierLockfileForTargetRequest(Addresses([field_set.address])),
)
```

This returns the lockfile for the resolve used by the target.

**Validation**:
- Test that transitive deps of provided are excluded
- Test that shared deps (needed by both provided and non-provided) are kept
- Test the overlap scenario described in reviewer feedback

---

### Phase 3: Rename Field (compile_dependencies → provided)

**Goal**: Rename the field to match Maven terminology. Done LAST to avoid churn during implementation.

**Files to modify**:
1. `pants-plugins/clojure_backend/target_types.py`
2. `pants-plugins/clojure_backend/goals/package.py`
3. `pants-plugins/clojure_backend/compile_dependencies.py` → rename to `provided_dependencies.py`
4. All test files

**Tasks**:

1. **Rename field class** in `target_types.py`:
   - `ClojureCompileDependenciesField` → `ClojureProvidedDependenciesField`
   - Update `alias` from `"compile_dependencies"` to `"provided"`
   - Update help text to reference Maven "provided" scope

2. **Rename module**:
   - `compile_dependencies.py` → `provided_dependencies.py`
   - `CompileOnlyDependencies` → `ProvidedDependencies`

3. **Update all imports and references**

4. **Update tests and BUILD files**

**Validation**: All tests pass with renamed field.

---

### Phase 4: Documentation and Examples

**Goal**: Document the feature thoroughly.

**Files to modify**:
1. `docs/provided_dependencies.md` (create or update from `compile_dependencies.md`)
2. Update inline help text in `target_types.py`

**Tasks**:

1. **Update field help text**:
   ```python
   help = (
       "Dependencies that are 'provided' at runtime but should be excluded from the JAR.\n\n"
       "Similar to Maven's 'provided' scope. Dependencies listed here will be available "
       "during AOT compilation but excluded (along with their transitive dependencies) "
       "from the final packaged JAR.\n\n"
       "Matching is based on Maven groupId:artifactId coordinates (version is ignored). "
       "This means if you mark `org.example:lib:1.0` as provided, any version of "
       "`org.example:lib` will be excluded.\n\n"
       "Note: If a dependency is needed by both a provided and non-provided path, "
       "it will be KEPT in the JAR (only exclusively-provided deps are excluded).\n\n"
       "Example:\n"
       "  clojure_deploy_jar(\n"
       "      name='webapp',\n"
       "      main='my.web.handler',\n"
       "      dependencies=[':servlet-api', ':my-lib'],\n"
       "      provided=[':servlet-api'],  # Container provides this at runtime\n"
       "  )"
   )
   ```

2. **Create documentation file** explaining:
   - Maven "provided" scope semantics
   - Coordinate-based matching (ignores version)
   - Transitive exclusion behavior
   - Shared dependency handling
   - Examples with `jvm_artifact` targets

---

## Testing Strategy

### Unit Tests
- Coordinate extraction from `jvm_artifact` targets
- Transitive closure computation
- Shared dependency handling (overlap scenarios)

### Integration Tests

1. **Basic jvm_artifact exclusion**:
   ```python
   jvm_artifact(name="servlet-api", group="javax.servlet", artifact="servlet-api", version="4.0.1")
   clojure_deploy_jar(
       name="app",
       main="my.app.core",
       dependencies=[":servlet-api", ":handler"],
       compile_dependencies=[":servlet-api"],
   )
   # Verify: servlet-api-4.0.1.jar NOT in final JAR
   ```

2. **Transitive exclusion**:
   ```python
   # If servlet-api depends on commons-logging, verify commons-logging is also excluded
   ```

3. **Shared dependency preservation**:
   ```python
   # If commons-io is needed by both servlet-api (provided) and my-lib (not provided),
   # verify commons-io IS in the final JAR
   ```

4. **Version-agnostic matching**:
   ```python
   # If servlet-api:3.0 is provided but transitive dep brings in servlet-api:4.0,
   # verify both versions are excluded
   ```

5. **First-party provided dependencies** (existing tests should pass)

### Manual Testing
- Build sample webapp with servlet-api as provided
- Verify JAR size reduction
- Verify JAR runs correctly when deployed to container

---

## Success Criteria

1. ✅ Third-party `jvm_artifact` dependencies in `provided` are excluded from final JAR
2. ✅ Transitive dependencies of provided artifacts are excluded
3. ✅ Shared dependencies (needed by non-provided paths) are preserved
4. ✅ Matching based on `groupId:artifactId` (version ignored)
5. ✅ Provided dependencies available at compile/AOT time
6. ✅ First-party Clojure sources as provided still work (existing functionality)
7. ✅ Field renamed from `compile_dependencies` to `provided`
8. ✅ Clear documentation with examples

---

## Potential Issues and Mitigations

### Issue 1: Lockfile Access Pattern
**Problem**: Getting the correct lockfile for the target's resolve.

**Mitigation**: Use `CoursierLockfileForTargetRequest` which handles resolve selection automatically based on the target's `resolve` field.

### Issue 2: JAR Filename Matching
**Problem**: JAR filenames may not always follow `{artifact}-{version}.jar` pattern (classifiers, packaging).

**Mitigation**:
- Use the lockfile's `file_name` field for exact mapping
- Build a direct `filename -> coordinate` lookup table

### Issue 3: Coordinate Matching Edge Cases
**Problem**: Classifiers (e.g., `-sources`, `-javadoc`) and packaging types.

**Mitigation**:
- Initial implementation matches on `group:artifact` only
- Document that classifiers are not considered
- Lockfile contains full coordinate info if needed later

### Issue 4: Performance
**Problem**: Loading and parsing lockfile for every deploy jar build.

**Mitigation**:
- Pants caches rule results - lockfile only loaded once per resolve
- Lockfile parsing is fast (JSON)

---

## References

- Maven dependency scopes: https://maven.apache.org/guides/introduction/introduction-to-dependency-mechanism.html
- Pants JVM lockfiles: `pants.jvm.resolve.coursier_fetch`
- Pants JVM artifacts: `pants.jvm.target_types`
- Previous plan: `docs/plans/20251123_compile_only_dependencies.md`
- Bug location: `pants-plugins/clojure_backend/goals/package.py:288-305`