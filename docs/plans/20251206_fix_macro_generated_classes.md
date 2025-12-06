# Fix Macro-Generated Third-Party Classes in Uberjars

**Date:** 2025-12-06
**Status:** Draft
**Issue:** `NoClassDefFoundError` for macro-generated classes like `com/rpl/specter/impl$local_declarepath`

## Problem Statement

When AOT compiling Clojure code that uses macros from third-party libraries (like Specter's `declarepath`), those macros can generate classes in the **macro's namespace** rather than the caller's namespace. Our current uberjar packaging logic incorrectly discards these classes because:

1. They look like "third-party" classes (e.g., `com/rpl/specter/impl$local_declarepath`)
2. Our `is_first_party_class()` filter discards them
3. The original third-party JAR doesn't contain these classes (they're only generated during YOUR compile)
4. Result: `NoClassDefFoundError` at runtime

### Example Error

```
NoClassDefFoundError: com/rpl/specter/impl$local_declarepath
    at dh.utils.interface.utils$fn__14334.invokeStatic(utils.clj:249)
```

### Libraries Affected

This pattern is common in Clojure libraries that use `deftype`/`defrecord` within macro expansions:
- **Specter** - `declarepath`, `providepath`
- **core.async** - `go` macro generates state machines in impl namespace
- **core.match** - pattern compilation uses internal protocols
- Any library using macros that expand to `deftype`/`defrecord`

## Root Cause Analysis

In `package.py`, the current logic (lines 436-441):

```python
if is_first_party_class(arcname):
    jar.writestr(arcname, file_content.content)
    added_entries.add(arcname)
    first_party_count += 1
else:
    third_party_skipped += 1  # <-- BUG: Assumes JAR has this class
```

The assumption "if it's not first-party, the JAR must have it" is **false** for macro-generated classes.

## Solution

Change the decision logic from:
- **Current:** "Is this a first-party class?" (if no, discard)
- **Fixed:** "Is this a first-party class OR does this class NOT exist in any JAR?" (if neither, discard)

This ensures macro-generated classes are kept because they don't exist in any dependency JAR.

### Design Decision: Why Not "Keep All, Let JARs Override"?

An alternative approach would be:
1. Keep ALL AOT classes initially (first-party + third-party)
2. Let Step 2 override third-party classes when extracting JARs

We chose NOT to do this because:
- If a JAR is accidentally excluded (bug, config issue), you'd silently use AOT classes
- This could reintroduce protocol identity issues that are hard to debug
- The explicit "check if exists in JAR" approach is more defensive and predictable

## Implementation Plan

### Phase 1: Refactor JAR Processing for Single-Pass Scanning

**Goal:** Build the JAR class index efficiently without duplicating I/O. Restructure to scan JARs once for both indexing and extraction.

**Location:** `package.py`, restructure the JAR processing section

**Changes:**

1. Move JAR scanning to happen once, collecting both class index AND content for later extraction:

```python
# Scan all dependency JARs once:
# 1. Build index of available classes (for AOT filtering)
# 2. Collect JAR entries for later extraction (Step 2)
jar_class_files: set[str] = set()
jar_entries_to_extract: list[tuple[str, bytes]] = []

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
                    # Skip META-INF and LICENSE files
                    if item.startswith('META-INF/'):
                        continue
                    item_basename = os.path.basename(item).upper()
                    if item_basename.startswith('LICENSE'):
                        continue

                    # Index class files
                    if item.endswith('.class'):
                        jar_class_files.add(item)

                    # Store entry for later extraction
                    try:
                        data = dep_jar.read(item)
                        jar_entries_to_extract.append((item, data))
                    except Exception:
                        pass
        except Exception:
            pass

logger.debug(f"Found {len(jar_class_files)} classes in dependency JARs")
```

2. Update Step 2 to use pre-collected entries instead of re-scanning:

```python
# Step 2: Extract dependency JAR contents (pre-collected during index build)
for arcname, data in jar_entries_to_extract:
    if arcname not in added_entries:
        jar.writestr(arcname, data)
        added_entries.add(arcname)
```

**Files Modified:**
- `pants-plugins/clojure_backend/goals/package.py`

### Phase 2: Update Class Filtering Logic

**Goal:** Modify the AOT class filtering to keep macro-generated classes.

**Location:** `package.py`, lines 436-441

**Changes:**

1. Update the filtering logic with clearer variable names:

```python
# Step 1: Add AOT-compiled classes
# - First-party classes: always include from AOT
# - Third-party classes NOT in any JAR: include from AOT (macro-generated)
# - Third-party classes that exist in JARs: skip (will come from JAR in Step 2)
#
# Why check JAR contents? Some macros (like Specter's declarepath) generate
# classes in the macro's namespace, not the caller's namespace. These classes
# don't exist in the original JAR - they're only created during AOT compilation
# of code that USES the macro. We must keep these or get NoClassDefFoundError.
first_party_count = 0
third_party_aot_kept_count = 0  # Classes not in any JAR (macro-generated)
third_party_skipped = 0

for file_content in digest_contents:
    if file_content.path.startswith('classes/') and file_content.path.endswith('.class'):
        arcname = file_content.path[8:]  # len('classes/') == 8

        # Skip classes belonging to provided Clojure source dependencies
        if is_provided_class(arcname):
            continue

        if is_first_party_class(arcname):
            # First-party class: include from AOT
            jar.writestr(arcname, file_content.content)
            added_entries.add(arcname)
            first_party_count += 1
        elif arcname not in jar_class_files:
            # Third-party class not in any JAR - must be macro-generated
            # Keep from AOT output since no JAR can provide it
            jar.writestr(arcname, file_content.content)
            added_entries.add(arcname)
            third_party_aot_kept_count += 1
        else:
            # Third-party class that exists in a JAR: skip
            # Will be added in Step 2 from the original JAR (protocol safety)
            third_party_skipped += 1

logger.info(f"AOT: included {first_party_count} first-party classes, "
            f"{third_party_aot_kept_count} third-party AOT classes (not in JARs), "
            f"skipped {third_party_skipped} third-party classes (from JARs)")
```

2. Update the existing comment at line 422 that says "Third-party AOT output is discarded" to reflect the new behavior.

**Files Modified:**
- `pants-plugins/clojure_backend/goals/package.py`

### Phase 3: Add Test Coverage

**Goal:** Add comprehensive tests to prevent regression and document expected behavior.

**Location:** `pants-plugins/tests/test_package_clojure_deploy_jar.py`

**Test Cases:**

1. **`test_aot_class_not_in_jars_is_kept`** (Core fix validation)
   - Simulate a scenario where AOT generates a class that doesn't exist in any JAR
   - Verify the class IS included in the final JAR
   - This is the primary test for the bug fix

2. **`test_third_party_classes_still_come_from_jars`** (Regression prevention)
   - Verify third-party classes that DO exist in JARs still come from JARs
   - Important: verify the JAR version wins, not the AOT version
   - Can check by comparing class file size or contents if possible

3. **`test_nested_inner_class_not_in_jars_is_kept`** (Edge case)
   - Test that classes with multiple levels of inner classes work
   - e.g., `com/rpl/specter/impl$local_declarepath$fn__123.class`

4. **`test_transitive_macro_generated_classes_included`** (Transitive case)
   - First-party code A uses first-party code B
   - B uses a macro that generates a third-party-namespaced class
   - Verify the class is included

**Test Pattern to Follow:**
```python
def test_aot_class_not_in_jars_is_kept(rule_runner: RuleRunner):
    """Verify AOT classes not found in any dependency JAR are kept.

    This handles macro-generated classes like Specter's declarepath which
    generates classes in com.rpl.specter.impl namespace during AOT compilation
    of user code.
    """
    # Setup: Create scenario where AOT generates a class not in any JAR
    # ...

    # Build JAR
    result = rule_runner.request(BuiltPackage, [field_set])

    # Verify: class IS in JAR
    jar_entries = get_jar_entries(rule_runner, result)
    assert 'some/third/party/namespace$macro_generated.class' in jar_entries

    # Verify: first-party classes also present
    assert 'myapp/core__init.class' in jar_entries
```

**Files Modified:**
- `pants-plugins/tests/test_package_clojure_deploy_jar.py`

### Phase 4: Update Documentation

**Goal:** Update docs to explain the macro-generated class handling.

**Changes:**

1. **`docs/aot_compilation.md`** - Add new section "Macro-Generated Classes":
   ```markdown
   ## Macro-Generated Classes

   Some Clojure macros generate classes in the macro's namespace rather than
   the calling namespace. For example, Specter's `declarepath` macro generates
   classes like `com.rpl.specter.impl$local_declarepath` when you use it in
   your code.

   These classes don't exist in the original library JAR - they're only
   created during AOT compilation of YOUR code. The plugin detects these
   by checking if the class exists in any dependency JAR. If not, it's
   kept from AOT output.

   ### Libraries with this pattern
   - Specter (`declarepath`, `providepath`)
   - core.async (`go` macro)
   - core.match (pattern compilation)
   ```

2. **`docs/uberjar_comparison.md`** - Update comparison table:
   - Add row: "Macro-generated classes"
   - Leiningen: "Depends on merge order"
   - tools.build: "Depends on conflict strategy"
   - pants-clojure: "Kept from AOT (detected via JAR contents check)"

3. **`package.py` inline comments** - Update the Step 1 comment block to explain the macro-generated class handling (the code already has this in Phase 2, just noting it here).

**Files Modified:**
- `docs/aot_compilation.md`
- `docs/uberjar_comparison.md`

## Testing Strategy

### Unit Tests

Run the existing test suite plus new tests:
```bash
pants test pants-plugins/::
```

### Integration Test

Test with a real project that uses Specter:
1. Build a deploy JAR for the affected project
2. Verify `com/rpl/specter/impl$local_declarepath.class` is in the JAR:
   ```bash
   jar tf dist/app.jar | grep 'specter/impl.*declarepath'
   ```
3. Run the JAR and confirm no `NoClassDefFoundError`:
   ```bash
   java -jar dist/app.jar
   ```

### Verification Checklist

- [ ] New tests pass
- [ ] Existing tests still pass
- [ ] JAR contains macro-generated classes
- [ ] JAR does NOT contain duplicate entries
- [ ] Third-party protocol classes come from JARs (not AOT)
- [ ] Performance is acceptable (no noticeable slowdown)

## Rollback Plan

If issues arise:
1. The change is isolated to `package.py`
2. Can revert to previous behavior by removing the `jar_class_files` check
3. Workaround: users can use source-only mode (`main="clojure.main"`)

## Performance Considerations

The JAR scanning adds overhead:
- Scans dependency JARs once (combined with existing Step 2 processing)
- Memory for class index: ~20-30KB per 100 classes (including Python object overhead)
- Typical project with 50-100 JARs, ~3000 classes each = 150K-300K entries
- Estimated memory: 30-90MB for the set
- Single O(1) lookup per AOT class during filtering

This is acceptable because:
1. It happens once per package operation
2. Memory is released after packaging completes
3. Correctness is more important than marginal overhead
4. The alternative (runtime NoClassDefFoundError) is far worse

## Success Criteria

1. `pants test pants-plugins/::` passes
2. No `NoClassDefFoundError` for macro-generated classes in affected project
3. Third-party classes still come from JARs (protocol safety preserved)
4. No duplicate entries in generated JARs
5. Documentation updated to explain the behavior
