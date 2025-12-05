# Fix AOT-Compiled Classes Not Being Overridden by Dependency JAR Classes (v2)

**Date:** 2025-12-05
**Status:** Implemented (Phases 1-5 complete, pending verification on actual project)
**Severity:** Critical - causes runtime failures in production
**Related:**
- `docs/plans/20251204_fix_aot_jar_override_bug.md` (previous attempt)
- Bug report from 2025-12-05 showing the fix didn't work

## Summary

The previous fix attempted to solve the wrong problem. The real issue is that **Clojure's AOT compilation is inherently transitive** - when you compile `myapp.core`, Clojure also compiles all namespaces it requires, including third-party libraries. The fix tried to filter these out during JAR packaging, but this approach is fragile and wasn't working.

**The correct solution is to filter AOT output to only include first-party namespaces, excluding all third-party class files entirely.**

## Root Cause Analysis

### Why the Previous Fix Failed

The previous fix (package.py lines 314-404) tried to:
1. Pre-scan dependency JARs to find what classes they contain
2. Skip AOT classes that exist in dependency JARs
3. Use JAR versions instead of AOT versions

This approach has multiple failure modes:
- Dependency JARs may not be in `digest_contents` due to classpath structure
- Source-only libraries have no pre-compiled classes to compare against
- The logic is complex and hard to debug

### The Real Problem

Clojure's `(compile 'namespace)` function is **transitively recursive** by design:
- It loads the namespace and all its dependencies
- It compiles ALL loaded namespaces to `.class` files
- There's no built-in way to prevent this

This is documented behavior. From the [Clojure Cookbook](https://github.com/clojure-cookbook/clojure-cookbook/blob/master/08_deployment-and-distribution/8-01_aot-compilation.asciidoc):
> AOT compilation is transitive, so in addition to your main namespace with its (:gen-class), this will also compile everything that namespace requires.

### The Correct Solution

Tools like [depstar](https://cljdoc.org/d/com.github.seancorfield/depstar/2.1.303/doc/getting-started/aot-compilation) handle this by:
1. Let Clojure compile everything transitively (can't prevent this)
2. **Filter the output** to only include first-party namespace classes
3. Third-party classes come from their pre-compiled JARs, not from AOT

This is simpler, more robust, and matches how other Clojure build tools work.

## Proposed Solution

### Core Change: Only Include First-Party AOT Classes

The solution is simple:

1. **User controls what to AOT compile** via the `aot` field (unchanged)
2. **Clojure compiles transitively** (can't prevent this)
3. **We only KEEP first-party AOT output** - discard third-party AOT classes
4. **Third-party content comes from JARs** - whether `.class` or `.clj` files

### Why This Works for All Cases

Third-party JARs can contain `.class` files, `.clj` files, or both. Either way:
- If JAR has `.class` files → extracted in Step 2, used at runtime
- If JAR has only `.clj` files → extracted in Step 2, compiled at runtime by Clojure
- If JAR has both → extracted in Step 2, `.class` files used

**We never need third-party AOT output.** The JAR contents handle everything.

### User Control of AOT

The existing `aot` field controls what namespaces we *ask* Clojure to compile:
- `aot=[":all"]` - compile all first-party namespaces
- `aot=[]` (default) - compile just the main namespace
- `aot=["my.ns1", "my.ns2"]` - explicit list

Clojure will transitively compile dependencies, but we filter the output to only keep what the user asked for (first-party namespaces).

### Implementation Details

**In `package.py`, simplify to first-party-only filtering:**

```python
# Build set of first-party namespace paths for filtering AOT classes
first_party_namespace_paths: set[str] = set()
for source_path, namespace in namespace_analysis.namespaces.items():
    namespace_path = namespace.replace('.', '/').replace('-', '_')
    first_party_namespace_paths.add(namespace_path)

def is_first_party_class(arcname: str) -> bool:
    """Check if a class file belongs to a first-party namespace."""
    class_path = arcname[:-6]  # Remove .class
    base_class_path = class_path.split('$')[0]  # Handle inner classes
    if base_class_path.endswith('__init'):
        base_class_path = base_class_path[:-6]
    return base_class_path in first_party_namespace_paths

# Step 1: Add ONLY first-party AOT-compiled classes
# Third-party AOT output is discarded - their content comes from JARs
for file_content in digest_contents:
    if file_content.path.startswith('classes/') and file_content.path.endswith('.class'):
        arcname = file_content.path[8:]
        if is_provided_class(arcname):
            continue
        if is_first_party_class(arcname):
            jar.writestr(arcname, file_content.content)
            added_entries.add(arcname)
        # else: discard - third-party content comes from JARs

# Step 2: Extract dependency JARs (unchanged)
# This provides all third-party content (.class, .clj, resources)
for file_content in digest_contents:
    if file_content.path.endswith('.jar'):
        # ... existing JAR extraction logic ...
```

## Implementation Plan

### Phase 1: Build First-Party Namespace Set

**Goal:** Build a set of class path prefixes for first-party namespaces.

**File to modify:** `pants-plugins/clojure_backend/goals/package.py`

**Changes:**

Add after namespace_analysis is obtained (around line 120):

```python
# Build set of first-party namespace paths for filtering AOT classes
# These represent namespaces from clojure_source targets in the project
first_party_namespace_paths: set[str] = set()
for source_path, namespace in namespace_analysis.namespaces.items():
    # Convert namespace to class path: my.app.core -> my/app/core
    # Clojure converts hyphens to underscores in class names
    namespace_path = namespace.replace('.', '/').replace('-', '_')
    first_party_namespace_paths.add(namespace_path)

logger.debug(f"First-party namespaces: {first_party_namespace_paths}")
```

### Phase 2: Replace Pre-Scan with First-Party Filter

**Goal:** Remove the JAR pre-scan and use simple first-party filtering.

**File to modify:** `pants-plugins/clojure_backend/goals/package.py`

**Changes:**

1. **Remove the pre-scan loop entirely** (lines 314-336) - no longer needed

2. **Remove `items_in_dependency_jars`** - no longer needed

3. Add `is_first_party_class` helper function:

```python
def is_first_party_class(arcname: str) -> bool:
    """Check if a class file belongs to a first-party namespace."""
    if not first_party_namespace_paths:
        return True  # Fallback: include all if no analysis available
    class_path = arcname[:-6]  # Remove .class
    base_class_path = class_path.split('$')[0]  # Handle inner classes
    if base_class_path.endswith('__init'):
        base_class_path = base_class_path[:-6]
    return base_class_path in first_party_namespace_paths
```

4. Update Step 1 to filter by first-party only:

```python
# Step 1: Add ONLY first-party AOT-compiled classes
# Third-party AOT output is discarded - their content comes from JARs
first_party_count = 0
third_party_skipped = 0

for file_content in digest_contents:
    if file_content.path.startswith('classes/') and file_content.path.endswith('.class'):
        arcname = file_content.path[8:]
        if is_provided_class(arcname):
            continue
        if is_first_party_class(arcname):
            jar.writestr(arcname, file_content.content)
            added_entries.add(arcname)
            first_party_count += 1
        else:
            third_party_skipped += 1

logger.info(f"AOT: included {first_party_count} first-party classes, "
            f"skipped {third_party_skipped} third-party classes")
```

### Phase 3: Simplify JAR Extraction (Step 2)

**Goal:** Remove override tracking logic since there's nothing to override.

Step 2 extracts all content from dependency JARs. Since we no longer write third-party AOT classes, there's no "override" - JAR classes are the only source for third-party content.

```python
# Step 2: Extract dependency JARs
# All third-party content comes from here (.class, .clj, resources)
for file_content in digest_contents:
    if file_content.path.endswith('.jar'):
        jar_filename = os.path.basename(file_content.path)
        should_exclude = any(
            jar_filename.startswith(prefix) for prefix in excluded_artifact_prefixes
        )
        if should_exclude:
            continue

        try:
            jar_bytes = io.BytesIO(file_content.content)
            with zipfile.ZipFile(jar_bytes, 'r') as dep_jar:
                for item in dep_jar.namelist():
                    if item.startswith('META-INF/'):
                        continue
                    if item in added_entries:
                        continue  # Already added from another JAR
                    try:
                        data = dep_jar.read(item)
                        jar.writestr(item, data)
                        added_entries.add(item)
                    except Exception:
                        pass
        except Exception:
            pass
```

**Remove:**
- `aot_entries` set - no longer needed
- `overridden_count` tracking - no longer needed
- The log message about "classes from dependency JARs instead of AOT"

### Phase 4: Add/Update Tests

**Goal:** Ensure the new behavior is tested comprehensively.

**File to modify:** `pants-plugins/tests/test_package_clojure_deploy_jar.py`

**New tests to add:**

1. **`test_only_first_party_aot_classes_included`**
   - Verify first-party namespace classes come from AOT
   - Check that inner classes (`$`) are handled correctly
   - Check that `__init` classes are handled correctly

2. **`test_third_party_classes_not_from_aot`**
   - Verify third-party classes (e.g., `clojure/core*.class`) are NOT from AOT
   - They should come from the Clojure JAR instead
   - Can verify by checking that third-party classes exist but weren't in AOT output

3. **`test_third_party_content_extracted_from_jars`**
   - Verify all third-party content (`.class`, `.clj`, resources) comes from JARs
   - Ensures source-only libraries have their `.clj` files included

**Existing tests to update:**
- `test_transitive_first_party_classes_included` - keep, still valid
- `test_no_duplicate_entries_in_jar` - keep, still important
- `test_aot_classes_included_then_jar_overrides` - rename/update to reflect simpler logic

### Phase 5: Verify and Document

1. Run all tests: `pants test pants-plugins::`
2. Build the actual app JAR:
   ```bash
   pants package bases/app:app_main_jar
   ```
3. Verify timestamps show correct behavior:
   ```bash
   unzip -l dist/app.jar | grep 'rpl/schema/core/Schema.class'
   # Should show old timestamp from JAR, not today's date
   ```
4. Run the app to verify no protocol errors
5. Update plan status to completed

## Risk Assessment

### Low Risk
- The new approach is simpler and more predictable
- First-party namespaces are already analyzed, so no new data needed
- Tests will catch regressions

### Medium Risk
- Need to handle edge cases like inner classes (`$`) and `__init` classes
- Need to ensure namespace-to-path conversion handles hyphens correctly

### Mitigations
- Comprehensive test coverage for edge cases
- Logging of skipped classes for debugging

## Success Criteria

1. Only first-party `.class` files come from AOT compilation
2. All third-party `.class` files come from dependency JARs
3. `rpl/schema/core/Schema.class` in final JAR has timestamp from dependency JAR
4. Protocol extension errors are resolved at runtime
5. All existing tests pass
6. No duplicate entries in final JAR

## Files to Modify

1. `pants-plugins/clojure_backend/goals/package.py` - Simplify filtering logic
2. `pants-plugins/tests/test_package_clojure_deploy_jar.py` - Add/update tests

## Why This Approach is Better

| Aspect | Previous (JAR pre-scan) | New (First-party filter) |
|--------|-------------------------|--------------------------|
| Complexity | High - scan JARs, match paths | Low - check namespace |
| First-party detection | No | Yes - explicit check |
| Third-party handling | Complex override logic | Simple - always from JAR |
| Debugging | Hard - many failure modes | Easy - clear categorization |
| Performance | Scans all JARs | No JAR scanning needed |

### Decision Matrix for AOT Classes

| Class Type | Action | Rationale |
|------------|--------|-----------|
| First-party | Include from AOT | It's our code, we compiled it |
| Third-party | Discard AOT | JAR provides content (`.class` or `.clj`) |

## References

- [Stack Overflow: How to avoid transitive AOT compiling](https://stackoverflow.com/questions/34717278/how-to-avoid-transitive-aot-compiling-of-clojure-source-files)
- [depstar AOT Compilation docs](https://cljdoc.org/d/com.github.seancorfield/depstar/2.1.303/doc/getting-started/aot-compilation)
- [Clojure Cookbook: AOT Compilation](https://github.com/clojure-cookbook/clojure-cookbook/blob/master/08_deployment-and-distribution/8-01_aot-compilation.asciidoc)
