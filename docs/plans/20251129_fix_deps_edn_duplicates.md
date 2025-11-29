# Add Defensive Deduplication to deps.edn Generation

**Date:** 2024-11-29
**Status:** Complete
**Type:** Defensive improvement / Code hardening

## Problem Statement

The user reported that deps.edn generation "sometimes" produces duplicate entries. While the standard lock file parsing path (`parse_lock_file()`) creates exactly one `LockFileEntry` per `[[entries]]` section (making duplicates unlikely through normal operation), the `format_deps_edn_deps()` function does not defensively guard against duplicate `group/artifact` combinations.

In EDN/Clojure, duplicate keys in a map result in only the last value being retained, which could cause:
- Non-deterministic behavior if entry order changes
- Silent data loss if versions differ
- Confusion when debugging dependency issues

## Analysis

### Current Code Path

In `pants-plugins/clojure_backend/goals/generate_deps.py`, the `format_deps_edn_deps()` function (lines 132-147):

```python
def format_deps_edn_deps(entries: list[LockFileEntry]) -> str:
    """Format lock file entries as deps.edn :deps map."""
    if not entries:
        return "{}"

    dep_lines = []
    for entry in sorted(entries, key=lambda e: (e.group, e.artifact)):
        dep_key = f"{entry.group}/{entry.artifact}"
        dep_value = f'{{:mvn/version "{entry.version}" :exclusions [*]}}'
        dep_lines.append(f"   {dep_key} {dep_value}")

    return "{\n" + "\n".join(dep_lines) + "}"
```

### Why Duplicates Are Unlikely in Normal Operation

1. **Lock file structure**: Each `[[entries]]` section has exactly one `[entries.coord]` subsection
2. **Coursier behavior**: Produces one resolved version per artifact
3. **Parser behavior**: `parse_lock_file()` creates one `LockFileEntry` per `[[entries]]`

### Why Add Defensive Deduplication Anyway

1. **User report**: The user indicated duplicates do occur "sometimes" - this warrants investigation
2. **Defensive programming**: Guards against edge cases like corrupted/manually-edited lock files
3. **Future-proofing**: Protects against lock file format changes
4. **Low cost**: Simple change with minimal performance impact

## Solution

### Approach

Deduplicate entries by `(group, artifact)` key before formatting. Since we use `:exclusions [*]` on all deps (preventing transitive resolution), we only need one entry per artifact. If duplicates exist:

1. Keep the first entry encountered after sorting by `(group, artifact)` - deterministic behavior
2. Version selection doesn't matter much since `:exclusions [*]` prevents transitive resolution anyway

### Implementation

Modify `format_deps_edn_deps()` to deduplicate by key:

```python
def format_deps_edn_deps(entries: list[LockFileEntry]) -> str:
    """Format lock file entries as deps.edn :deps map.

    Each dependency includes :exclusions [*] to prevent transitive resolution,
    since Pants lock files already have all transitives flattened.

    Defensively deduplicates entries by (group, artifact) - if duplicates exist,
    the first one encountered (after sorting) is kept.
    """
    if not entries:
        return "{}"

    # Deduplicate by (group, artifact), keeping first entry after sorting
    seen: dict[tuple[str, str], LockFileEntry] = {}
    for entry in sorted(entries, key=lambda e: (e.group, e.artifact)):
        key = (entry.group, entry.artifact)
        if key not in seen:
            seen[key] = entry

    dep_lines = []
    for entry in seen.values():
        dep_key = f"{entry.group}/{entry.artifact}"
        dep_value = f'{{:mvn/version "{entry.version}" :exclusions [*]}}'
        dep_lines.append(f"   {dep_key} {dep_value}")

    # Sort for consistent output
    dep_lines.sort()

    return "{\n" + "\n".join(dep_lines) + "}"
```

## Implementation Plan

### Phase 1: Add Defensive Test Coverage [DONE]

**Goal:** Add tests that verify deduplication behavior works correctly

**Tasks:**
1. Add `test_format_deps_edn_deps_handles_duplicate_artifacts()` in `test_generate_deps_edn.py`:
   - Creates multiple `LockFileEntry` objects with the same `group/artifact` but different versions
   - Verifies that `format_deps_edn_deps()` produces unique keys (no duplicates in output)
   - Verifies deterministic output (first entry after sorting is kept)

2. Add `test_format_deps_edn_deps_duplicate_same_version()`:
   - Tests the edge case where duplicates have identical versions
   - Verifies only one entry appears in output

### Phase 2: Implement Defensive Deduplication [DONE]

**Goal:** Add deduplication logic to `format_deps_edn_deps()`

**Tasks:**
1. Modify `format_deps_edn_deps()` in `generate_deps.py` to:
   - Use a dictionary keyed by `(group, artifact)` to track seen entries
   - Keep the first entry encountered after sorting
   - Maintain sorted output order

2. Update docstring to document the defensive deduplication behavior

### Phase 3: Verify and Test [DONE]

**Goal:** Ensure changes work correctly and don't break existing functionality

**Tasks:**
1. Run the new deduplication tests - should pass
2. Run all existing tests: `pants test pants-plugins::`
3. Verify existing behavior is preserved (sorting, formatting, etc.)

## Testing Strategy

### New Unit Tests

1. **test_format_deps_edn_deps_handles_duplicate_artifacts** - Tests that duplicate group/artifact entries are deduplicated, keeping first after sort
2. **test_format_deps_edn_deps_duplicate_same_version** - Tests duplicates with identical versions produce single entry

### Existing Tests (Regression)

All existing tests in `test_generate_deps_edn.py` should continue to pass:
- `test_parse_lock_file`
- `test_format_deps_edn_deps`
- `test_format_deps_edn_deps_empty`
- `test_format_deps_edn_deps_sorting`
- `test_format_deps_edn_complete`
- `test_generate_deps_edn_simple_project`
- `test_generate_deps_edn_multiple_sources`
- `test_generate_deps_edn_with_tests`
- `test_generate_deps_edn_nested_source_dirs`

## Files to Modify

1. `pants-plugins/clojure_backend/goals/generate_deps.py` - Add defensive deduplication
2. `pants-plugins/tests/test_generate_deps_edn.py` - Add test coverage

## Rollback Plan

If issues arise, the change is isolated to `format_deps_edn_deps()`. Reverting to the original implementation is straightforward since no other files are affected.

## Success Criteria

1. New unit tests pass
2. All existing tests pass
3. Generated deps.edn files have unique keys in `:deps` map
4. Output remains sorted alphabetically by group/artifact
