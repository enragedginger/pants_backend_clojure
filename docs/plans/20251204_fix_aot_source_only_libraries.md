# Fix AOT Compilation for Source-Only Third-Party Libraries

**Date:** 2025-12-04
**Status:** Complete
**Issue:** Third-party libraries distributed as source-only are missing required classes in the uberjar

## Summary

The previous fix for AOT compilation (filtering out third-party AOT classes) introduced a regression: it assumes all third-party libraries ship with pre-compiled `.class` files in their JARs. Many Clojure libraries are distributed as source-only (containing only `.clj` files), and these libraries now fail at runtime because their transitively AOT-compiled classes are being filtered out.

## Bug Analysis

### Current Behavior (Broken)

1. **Dependency JARs are extracted FIRST** (lines 355-385)
   - All JAR contents (including `.clj` source files) are added to the uberjar
   - Entry paths are tracked in `added_entries`
   - Line 375: `if item not in added_entries` - skips duplicates

2. **AOT-compiled classes are added SECOND** (lines 387-415)
   - `is_project_class()` filter **excludes ALL third-party classes** (line 399)
   - Only project namespace classes are included
   - Line 407: `if arcname not in added_entries` - skips duplicates
   - **Problem**: Third-party classes are filtered out BEFORE the duplicate check

### The Problem

When a third-party library is distributed as source-only:
1. The JAR extraction adds `.clj` files but NO `.class` files
2. The `is_project_class()` filter removes the transitively compiled `.class` files
3. **Result**: Missing classes at runtime → `ClassNotFoundException` or protocol errors

### Example

Library `some-lib` is distributed as source-only:
```
some-lib-1.0.0.jar
├── some/lib/core.clj        # Source only
├── some/lib/protocols.clj   # Source only
└── META-INF/...
```

After AOT compilation, we have in `classes/`:
```
classes/
├── my/app/core.class         # Project class - KEPT
├── my/app/core$fn__123.class # Project class - KEPT
├── some/lib/core.class       # Third-party - FILTERED OUT (BUG!)
├── some/lib/core__init.class # Third-party - FILTERED OUT (BUG!)
└── clojure/core.class        # Third-party - FILTERED OUT (OK, in Clojure JAR)
```

The `some/lib/*.class` files are needed but get filtered out, while `clojure/core.class` is correctly filtered because it exists in the Clojure JAR.

## Root Cause

The previous fix had incorrect assumptions:
1. **Assumed**: All third-party libraries ship with pre-compiled classes
2. **Reality**: Many Clojure libraries are source-only
3. **Result**: We filter out required classes that don't exist anywhere else

## Proposed Solution

**Reverse the order of operations**: Add AOT-compiled classes FIRST (all of them), then extract dependency JARs which will override AOT classes when they exist. This way:

1. **All transitively AOT-compiled classes are included initially** (both project and third-party)
2. **Dependency JAR classes override AOT classes when they exist** (solving the protocol issue)
3. **Source-only libraries work** because their AOT classes aren't overridden (no pre-compiled classes exist to override them)

### Why This Works

The original protocol extension issue occurs when:
- A library ships with pre-AOT-compiled protocol classes
- Our AOT compilation produces different class identities
- The wrong version gets loaded → protocol mismatch

By letting **JAR classes win over AOT classes**, we ensure:
- Pre-compiled library classes are used (correct protocol identity)
- Source-only libraries use their AOT-compiled classes (no conflict exists)

### Scenario Analysis

| Scenario | AOT Classes | JAR Contents | Result |
|----------|-------------|--------------|--------|
| Pre-compiled library | `lib/Protocol.class` (wrong identity) | `lib/Protocol.class` (correct) | JAR overwrites → CORRECT |
| Source-only library | `lib/SourceOnly.class` | `lib/source_only.clj` (no class) | AOT class kept → CORRECT |
| Partial library | `lib/A.class`, `lib/B.class` | `lib/A.class`, `lib/b.clj` | JAR overwrites A, AOT B kept → CORRECT |

---

## Implementation Plan

### Phase 1: Reverse the Order of Operations [DONE]

**Goal**: Add AOT-compiled classes first, then overlay dependency JAR contents (overriding AOT when JAR has the class).

**Files to modify**:
- `pants-plugins/clojure_backend/goals/package.py`

**Changes**:

1. **Remove the `is_project_class()` filtering logic entirely** - it's no longer needed
2. **Remove `project_namespace_paths` set construction** - no longer needed
3. **Add ALL AOT-compiled classes first** (both project and third-party)
4. **Then extract dependency JARs**, OVERWRITING entries that already exist

**Key insight about zipfile behavior**: Python's `zipfile.ZipFile.writestr()` with an existing entry name creates a duplicate entry (ZIP format allows this). The last entry wins during extraction. However, for cleanliness we should track and explicitly handle this.

**Code outline**:

```python
# Track what we've added for logging purposes
added_entries = {'META-INF/MANIFEST.MF'}
aot_entries = set()

# Step 1: Add ALL AOT-compiled classes first (no filtering)
# These provide a baseline - source-only libraries need these
for file_content in digest_contents:
    if file_content.path.startswith('classes/') and file_content.path.endswith('.class'):
        arcname = file_content.path[8:]  # len('classes/') == 8
        jar.writestr(arcname, file_content.content)
        added_entries.add(arcname)
        aot_entries.add(arcname)

# Step 2: Extract dependency JARs
# JAR contents OVERRIDE AOT classes - this is intentional for protocol safety
# Pre-compiled library classes have correct protocol relationships
overridden_count = 0
for file_content in digest_contents:
    if file_content.path.endswith('.jar'):
        # Skip provided dependencies
        jar_filename = os.path.basename(file_content.path)
        if any(jar_filename.startswith(prefix) for prefix in excluded_artifact_prefixes):
            continue

        try:
            jar_bytes = io.BytesIO(file_content.content)
            with zipfile.ZipFile(jar_bytes, 'r') as dep_jar:
                for item in dep_jar.namelist():
                    if not item.startswith('META-INF/'):
                        try:
                            data = dep_jar.read(item)
                            if item in aot_entries:
                                # JAR class overrides AOT class (protocol safety)
                                overridden_count += 1
                                logger.debug(f"JAR class overrides AOT: {item}")
                            jar.writestr(item, data)
                            added_entries.add(item)
                        except Exception:
                            pass
        except Exception:
            pass

if overridden_count > 0:
    logger.debug(
        f"Dependency JARs overrode {overridden_count} AOT-compiled classes for {field_set.address}. "
        "This ensures pre-compiled library classes are used for protocol safety."
    )
```

**Note on ZIP duplicate entries**: When `writestr()` is called with an existing entry name, ZIP creates a second entry. During extraction, the LAST entry with that name is used. This is the behavior we want - JAR classes (written second) override AOT classes (written first).

### Phase 2: Update Tests [DONE]

**Goal**: Update existing tests and add new tests for source-only library scenario.

**Files to modify**:
- `pants-plugins/tests/test_package_clojure_deploy_jar.py`

**Changes**:

1. **Remove `TestIsProjectClassFiltering` class** - the filtering logic is removed
2. **Remove `test_third_party_aot_classes_excluded_from_jar`** - behavior changed
3. **Add new test: `test_aot_classes_included_then_jar_overrides`**:
   - Verify AOT classes are added first
   - Verify JAR classes override AOT when they exist
4. **Add new test: `test_source_only_library_classes_work`**:
   - Simulate a source-only library scenario
   - Verify that AOT-compiled classes for that library ARE included

**Note on test design**: We cannot easily test with a real source-only library since we'd need to add it to the lockfile. Instead, we can verify the behavior by checking:
- Project AOT classes are in the JAR
- Clojure core classes come from the JAR (not filtered out entirely)

### Phase 3: Update Documentation [DONE]

**Goal**: Update documentation to reflect the corrected behavior.

**Files to modify**:
- `docs/aot_compilation.md`

**Changes**:
- Update the explanation of how JAR packaging works
- Remove the section about filtering project namespaces
- Explain the new "AOT first, JAR override" approach
- Add section about source-only libraries

### Phase 4: Remove Dead Code [DONE]

**Goal**: Clean up code that's no longer needed.

**Files to modify**:
- `pants-plugins/clojure_backend/goals/package.py`

**Changes**:
- Remove `project_namespace_paths` set construction (lines 278-295)
- Remove `is_project_class()` function (lines 297-315)
- Remove empty namespace warning (lines 288-295)
- Update comments to reflect new behavior

---

## Risk Assessment

### Low Risk
- The change is well-understood and follows the same pattern as other build tools
- The "JAR wins" approach is the standard in the Clojure ecosystem

### Medium Risk
- ZIP duplicate entries: The behavior of having duplicate entries is well-defined (last wins), but some tools might not handle it well
- **Mitigation**: This is standard JAR behavior and works correctly with the JVM

### Edge Cases Considered

1. **Partial compilation libraries**: Some libraries have some classes compiled and some as source. The JAR override approach handles this correctly - compiled classes from JAR win, source-only classes use AOT.

2. **`.clj` source files**: These don't conflict with `.class` files. JARs add `.clj` files which can coexist with AOT `.class` files.

3. **Clojure's class loading**: At runtime, JVM prefers `.class` over `.clj`. When both exist, the class file is used. This is the correct behavior.

### Testing Strategy
1. Run existing test suite
2. Verify the test changes pass
3. Manual testing with the user's production project

---

## Success Criteria

1. Source-only third-party libraries work correctly (their AOT classes are included)
2. Pre-compiled third-party libraries still work (their JAR classes override AOT)
3. Protocol extension errors are resolved
4. All tests pass (with updates as needed)
5. Documentation reflects the correct behavior

---

## Alternative Approaches Considered

### Alternative 1: Only filter AOT classes that exist in JARs

Keep current order (JARs first, AOT second), but change filtering to only exclude AOT classes that have a corresponding `.class` file in a dependency JAR.

**Pros**:
- More surgical change
- Keeps the "JAR first" order

**Cons**:
- Requires scanning all JARs first to build a "known classes" set
- More complex implementation
- Slower (two passes over JARs)

**Decision**: Rejected - the "AOT first, JAR override" approach is simpler and equally correct.

### Alternative 2: Configurable filtering

Add a flag to enable/disable third-party AOT class filtering.

**Pros**:
- User control
- Backwards compatibility

**Cons**:
- Complexity
- Most users won't know which setting to use
- Doesn't solve the root problem

**Decision**: Rejected - we should just do the right thing by default.

---

## Comparison with Other Tools

| Tool | Behavior |
|------|----------|
| **Leiningen** | JAR contents merged, last wins |
| **tools.build** | Uses copy-dir, last wins |
| **depstar** | JAR contents merged, conflicts resolved by order |

Our new approach aligns with the ecosystem standard: the last write wins, and dependency JARs are processed after AOT classes.
