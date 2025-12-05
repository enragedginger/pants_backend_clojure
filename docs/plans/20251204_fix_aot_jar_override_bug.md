# Fix AOT-Compiled Classes Not Being Overridden by Dependency JAR Classes

**Date:** 2025-12-04
**Status:** Completed
**Severity:** Critical - causes runtime failures in production

## Summary

The uberjar packaging logic in `package.py` intends to allow dependency JAR classes to override AOT-compiled classes (for Clojure protocol safety), but due to a logic bug, this override never happens. This causes `IllegalArgumentException: No implementation of method` errors at runtime when using Clojure protocols.

## Bug Analysis

### Error Observed

```
Caused by: java.lang.IllegalArgumentException: No implementation of method: :spec of protocol: #'rpl.schema.core/Schema found for class: rpl.rama.util.schema.Volatile
```

### Root Cause (package.py lines 314-378)

The code has two steps:

1. **Step 1 (lines 317-325):** Write AOT-compiled classes and add them to both `added_entries` and `aot_entries` sets
2. **Step 2 (lines 331-372):** Extract dependency JARs, intending to override AOT classes

The bug is at lines 354-361:

```python
# Skip if already processed (from another JAR or already overridden)
if item in added_entries:
    continue  # <-- BUG: This prevents ALL overrides!

# Check if this is an AOT-compiled .class file that should be overridden
if item in aot_entries:
    # JAR class overrides AOT class (protocol safety)
    overridden_count += 1
    logger.debug(f"JAR class overrides AOT: {item}")

# Write the entry (either new, or overriding AOT)
try:
    data = dep_jar.read(item)
    jar.writestr(item, data)
```

**The problem:** At line 324, AOT classes are added to `added_entries`. Then at line 354, any item in `added_entries` is skipped with `continue`. This means the code at lines 358-366 is **unreachable** for AOT classes - they can never be overridden.

The `overridden_count` variable will always be 0, and the comment "JAR class overrides AOT class" is misleading because the override never actually happens.

### Why This Matters (Clojure Protocol Safety)

When Clojure AOT compiles code:
1. Protocol definitions (e.g., `rpl.schema.core/Schema`) generate Java interface classes
2. Types implementing protocols (e.g., `rpl.rama.util.schema.Volatile`) generate classes that implement those interfaces
3. The implementing class must be compiled against the **exact same** protocol interface class it will run against

If the uberjar contains:
- AOT-compiled `rpl/schema/core/Schema.class` (from project compilation)
- Pre-compiled `rpl/rama/util/schema/Volatile.class` (from dependency JAR)

The `Volatile` class was compiled against the JAR's version of `Schema`, not the AOT version. At runtime, the JVM sees that `Volatile` doesn't implement the AOT `Schema` interface, causing the "No implementation of method" error.

### ZIP Duplicate Entry Behavior

Python's `zipfile.ZipFile.writestr()` appends entries - it doesn't overwrite. If you call `writestr()` twice with the same path, you get duplicate entries in the ZIP.

**Which entry does Java load?** The behavior is implementation-dependent and not clearly documented:
- Java's `ZipFile` reads from the central directory (CEN) at the end of the file
- Different tools may load the first or last entry
- This ambiguity has been used for security exploits (signature checker verifies one entry, loader uses another)

**Due to this ambiguity, we should avoid creating duplicate entries.** The safest approach is to pre-scan JARs and skip AOT classes that exist in dependency JARs.

---

## Proposed Solution: Pre-Scan to Avoid Duplicate Entries

Use a pre-scan approach that identifies which classes exist in dependency JARs **before** writing any AOT classes, then skip those AOT classes entirely.

### Key Insight

The pre-scan approach:
1. Scans all dependency JARs to build a set of classes they contain
2. Skips AOT classes during Step 1 if they exist in dependency JARs
3. No duplicate entries - each class appears exactly once in the final JAR
4. Dependency JAR classes win by being the only version written

### Why This Works

1. **Pre-scan identifies conflicts** - We know which classes exist in dependency JARs before writing anything
2. **AOT classes are skipped, not duplicated** - No ambiguous duplicate entry behavior
3. **Dependency JAR classes win** - Protocol interfaces and implementations come from the same pre-compiled source
4. **First-party AOT classes preserved** - Project code that doesn't exist in any JAR is still AOT-compiled and included
5. **Source-only libraries work** - Their AOT classes are included because they don't exist in any JAR

---

## Implementation Plan

### Phase 1: Implement Pre-Scan Logic (DONE)

**Goal:** Add a pre-scan step that identifies which classes exist in dependency JARs before writing AOT classes.

**File to modify:** `pants-plugins/clojure_backend/goals/package.py`

**Changes:**

1. Add a new pre-scan loop **before** Step 1 (around line 314) that:
   - Iterates through all dependency JARs
   - Excludes provided/excluded JARs (same logic as Step 2)
   - Collects all non-META-INF entries into a set `items_in_dependency_jars`

2. Modify Step 1 (lines 317-325) to:
   - Skip AOT classes that exist in `items_in_dependency_jars`
   - Track these skipped classes in `aot_entries` for logging purposes (but don't write them)

3. Modify Step 2 (lines 331-372) to:
   - Remove the now-dead override logic (lines 357-361)
   - Track when we use JAR class instead of AOT for logging
   - Keep the duplicate skip logic for entries from earlier JARs

**Code:**

```python
# NEW: Pre-scan Step - Before writing any AOT classes, scan JARs to find what they contain
# This ensures we don't create duplicate ZIP entries (behavior is undefined for duplicates)
items_in_dependency_jars = set()
for file_content in digest_contents:
    if file_content.path.endswith('.jar'):
        jar_filename = os.path.basename(file_content.path)
        # Skip provided/excluded JARs (same logic as Step 2)
        should_exclude = any(jar_filename.startswith(prefix) for prefix in excluded_artifact_prefixes)
        if should_exclude:
            continue

        try:
            jar_bytes = io.BytesIO(file_content.content)
            with zipfile.ZipFile(jar_bytes, 'r') as dep_jar:
                for item in dep_jar.namelist():
                    if item.startswith('META-INF/'):
                        continue
                    items_in_dependency_jars.add(item)
        except Exception:
            pass

# Step 1: Add AOT-compiled classes EXCEPT those that exist in dependency JARs
# Source-only third-party libraries need their AOT classes; pre-compiled libraries don't
for file_content in digest_contents:
    if file_content.path.startswith('classes/') and file_content.path.endswith('.class'):
        arcname = file_content.path[8:]  # len('classes/') == 8
        if is_provided_class(arcname):
            continue
        # Skip AOT classes that will come from dependency JARs instead
        if arcname in items_in_dependency_jars:
            aot_entries.add(arcname)  # Track for logging, but don't write
            continue
        jar.writestr(arcname, file_content.content)
        added_entries.add(arcname)
        aot_entries.add(arcname)

# Step 2: Extract dependency JARs (simplified - no override logic needed)
overridden_count = 0
for file_content in digest_contents:
    if file_content.path.endswith('.jar'):
        # ... existing exclusion logic ...
        for item in dep_jar.namelist():
            if item.startswith('META-INF/'):
                continue
            if item in added_entries:
                continue  # Already have from earlier JAR (not AOT)

            # Track when we use JAR class instead of AOT
            if item in aot_entries:
                overridden_count += 1
                logger.debug(f"Using JAR class instead of AOT: {item}")

            data = dep_jar.read(item)
            jar.writestr(item, data)
            added_entries.add(item)

if overridden_count > 0:
    logger.info(
        f"Used {overridden_count} classes from dependency JARs instead of AOT compilation "
        "(ensures Clojure protocol safety)"
    )
```

**Testing:** Run existing test suite to ensure no regressions.

### Phase 2: Add Test for Protocol Override Behavior (DONE)

**Goal:** Add a specific test that verifies JAR classes are used instead of AOT classes when available.

**File to modify:** `pants-plugins/tests/test_package_clojure_deploy_jar.py`

**Changes:**

1. Enhance the existing `test_aot_classes_included_then_jar_overrides` test to verify that the final JAR has no duplicate entries
2. Add a new test `test_no_duplicate_entries_in_jar` that verifies unique entries

**Test approach:**

```python
def test_no_duplicate_entries_in_jar(rule_runner: RuleRunner) -> None:
    """Verify that the final JAR has no duplicate entries.

    Duplicate entries in JAR files have undefined behavior across different
    JVM implementations and tools. We should always produce clean JARs.
    """
    # ... setup code ...

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        entries = jar.namelist()
        unique_entries = set(entries)
        assert len(entries) == len(unique_entries), (
            f"JAR has duplicate entries: {[e for e in entries if entries.count(e) > 1]}"
        )
```

### Phase 3: Verify No Regressions (DONE)

**Goal:** Ensure all existing functionality continues to work.

**Verification checklist:**

1. Run `pants test pants-plugins::` to verify all tests pass
2. Verify source-only libraries still work (their AOT classes are included because they're not in any JAR)
3. Verify provided dependencies are still excluded correctly
4. Verify first-party transitive dependencies are included correctly

### Phase 4: Update Comments and Logging (DONE)

**Goal:** Update comments to accurately reflect the behavior.

**File to modify:** `pants-plugins/clojure_backend/goals/package.py`

**Changes:**

1. Add comment explaining the pre-scan step and why it's needed
2. Update Step 1 comment to explain the filtering logic
3. Update Step 2 to remove the misleading "override" comments
4. Change log message from "overrode" to "used X instead of Y" for clarity

---

## Risk Assessment

### Low Risk

- The change is additive (adds pre-scan) rather than fundamentally restructuring
- Existing test suite covers the main use cases
- No duplicate entries means predictable behavior across all JVM implementations
- First-party AOT classes are still included correctly

### Medium Risk

- The pre-scan adds an additional pass over dependency JARs, increasing build time slightly
- **Mitigation:** The JAR scanning is in-memory and should be fast; only reads the central directory, not file contents

### Edge Cases Verified

1. **Source-only libraries**: Their AOT classes won't be in `items_in_dependency_jars`, so they'll be included in Step 1. Correct behavior.
2. **Pre-compiled libraries**: Their classes are in `items_in_dependency_jars`, so AOT versions are skipped. JAR versions are used. Correct behavior.
3. **JAR-to-JAR duplicates**: Still prevented by `added_entries` tracking after JAR writes. Correct behavior.
4. **Provided dependencies**: Their JARs are excluded from the pre-scan (matching Step 2 exclusion logic). If they're also in `aot_entries`, they'll be filtered by `is_provided_class`. Correct behavior.
5. **Clojure core classes**: Always in the Clojure JAR, so AOT versions are skipped. Correct behavior.

---

## Success Criteria

1. Protocol extension errors (like the Rama example) are resolved
2. All existing tests pass
3. `overridden_count` is non-zero when third-party library classes are used instead of AOT
4. No duplicate entries in the final JAR
5. Source-only libraries continue to work
6. Documentation is updated

---

## Alternative Approaches Considered

### Alternative 1: Don't add AOT entries to `added_entries` (rely on ZIP duplicate semantics)

Simply remove the `added_entries.add(arcname)` line from Step 1, allowing JAR entries to write duplicates.

**Pros:**
- Minimal code change
- No additional memory overhead

**Cons:**
- ZIP duplicate entry behavior is implementation-dependent and not clearly documented
- Different tools (jar, unzip, Java) may load different entries
- Has been used for security exploits (signature verification bypasses)
- Creates non-standard JAR files

**Decision:** Rejected due to undefined duplicate entry behavior across JVM implementations.

### Alternative 2: Build JAR in memory with dict

Use a dictionary to store entries, allowing true overwrites, then write to ZIP at the end.

**Pros:**
- Clean final JAR with no duplicates
- Simple "last write wins" semantics

**Cons:**
- Higher memory usage (all contents in memory at once)
- Loses streaming capability
- More complex code change

**Decision:** Rejected due to memory concerns for large uberjars.

### Alternative 3: Two-pass with temp file

Write JAR as currently, then post-process to remove duplicates.

**Cons:**
- Extra I/O overhead
- Complex duplicate detection and removal logic
- Still doesn't solve the core issue (need to know which version to keep)

**Decision:** Rejected due to complexity.

---

## References

- [Python zipfile duplicate entry handling (CPython issue #117779)](https://github.com/python/cpython/issues/117779)
- [JAR Hell and class loading problems](https://www.herongyang.com/JVM/ClassLoader-Class-Load-Problem-JAR-Hell.html)
- [JDK-8345431: Detect duplicate entries in jar files](https://bugs.openjdk.org/browse/JDK-8345431)
- [Stack Overflow: Create ZIP with duplicate entries](https://stackoverflow.com/questions/39958486/java-create-a-zip-file-with-duplicate-entries)

---

## Files Changed

1. `pants-plugins/clojure_backend/goals/package.py` - Add pre-scan logic, update Step 1 and Step 2
2. `pants-plugins/tests/test_package_clojure_deploy_jar.py` - Add test for no duplicate entries
