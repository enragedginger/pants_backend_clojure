# Fix: Provided Dependencies JAR Filename Matching

**Date**: 2025-11-29
**Status**: Planned
**Related**: [Previous Plan](20251127_provided_dependencies.md)

## Summary

The `provided` field on `clojure_deploy_jar` targets does not exclude third-party JARs from the final uberjar because the JAR filename matching logic uses the wrong naming pattern.

## Root Cause Analysis

### The Bug Location

**File**: `pants-plugins/clojure_backend/goals/package.py`, lines 290-294

```python
excluded_artifact_prefixes = set()
for group, artifact in provided_deps.coordinates:
    # Match JAR files that start with the artifact name followed by a dash
    # e.g., "clojure-1.12.0.jar" matches artifact "clojure"
    excluded_artifact_prefixes.add(f"{artifact}-")
```

### Problem

The code expects JAR filenames to follow standard Maven convention:
- Expected pattern: `{artifact}-{version}.jar` (e.g., `clojure-1.11.0.jar`)

However, Pants/Coursier uses a different naming convention that includes the group:
- Actual pattern: `{group}_{artifact}_{version}.jar`

### Evidence from Lockfile

From `locks/jvm/java17.lock.jsonc`:
```
file_name = "com.google.code.findbugs_jsr305_3.0.2.jar"
group = "com.google.code.findbugs"
artifact = "jsr305"
version = "3.0.2"

file_name = "org.clojure_clojure_1.11.0.jar"
group = "org.clojure"
artifact = "clojure"
version = "1.11.0"
```

The pattern is: `{group}_{artifact}_{version}.jar`
- Dots in group names are PRESERVED (not replaced)
- Group and artifact are separated by underscore
- Artifact and version are separated by underscore

### Example of the Bug

For a dependency with coordinates `com.rpl/rama:1.0.0`:
- Current code creates prefix: `rama-`
- Actual JAR filename: `com.rpl_rama_1.0.0.jar`
- Result: **No match** - the JAR is incorrectly included in the uberjar

## The Fix

Change the prefix construction from:
```python
excluded_artifact_prefixes.add(f"{artifact}-")
```

To:
```python
excluded_artifact_prefixes.add(f"{group}_{artifact}_")
```

This correctly matches Pants/Coursier's naming convention:
- New prefix: `com.rpl_rama_`
- JAR filename: `com.rpl_rama_1.0.0.jar`
- Result: **Match** - the JAR is correctly excluded

---

## Implementation Plan

### Phase 1: Fix the JAR Filename Matching

**Goal**: Correct the prefix pattern to match Pants/Coursier's naming convention.

**File to modify**:
- `pants-plugins/clojure_backend/goals/package.py`

**Tasks**:

1. **Update the prefix construction** (line 294):

   Change:
   ```python
   excluded_artifact_prefixes.add(f"{artifact}-")
   ```

   To:
   ```python
   excluded_artifact_prefixes.add(f"{group}_{artifact}_")
   ```

2. **Update the comment** (lines 288-293):

   Change:
   ```python
   # Build set of artifact names to exclude based on coordinates
   # JAR filenames typically follow: {artifact}-{version}.jar pattern
   ```

   To:
   ```python
   # Build set of artifact prefixes to exclude based on coordinates
   # Pants/Coursier JAR filenames follow: {group}_{artifact}_{version}.jar pattern
   # e.g., "org.clojure_clojure_1.11.0.jar" for org.clojure:clojure:1.11.0
   ```

**Validation**:
- Run existing tests: `pants test pants-plugins::`
- All tests should pass

---

### Phase 2: Add Integration Test for Third-Party JAR Exclusion

**Goal**: Add a test that specifically verifies third-party `jvm_artifact` dependencies are correctly excluded when marked as provided.

**File to modify**:
- `pants-plugins/tests/test_package_clojure_deploy_jar.py`

**Tasks**:

1. **Add a new test function** `test_provided_jvm_artifact_excluded_from_jar`:

   This test will use a jvm_artifact dependency (such as org.clojure/clojure which is already available in the lockfiles) and verify it's excluded from the final JAR when marked as provided.

   ```python
   def test_provided_jvm_artifact_excluded_from_jar(rule_runner: RuleRunner) -> None:
       """Test that provided jvm_artifact dependencies are excluded from the final JAR."""
       import io
       import zipfile

       rule_runner.write_files(
           {
               "locks/jvm/java17.lock.jsonc": <existing lockfile with dependencies>,
               "src/app/BUILD": dedent(
                   """\
                   jvm_artifact(
                       name="guava",
                       group="com.google.guava",
                       artifact="guava",
                       version="31.1-jre",
                   )

                   clojure_source(
                       name="core",
                       source="core.clj",
                       dependencies=[":guava"],
                   )

                   clojure_deploy_jar(
                       name="app",
                       main="app.core",
                       dependencies=[":core", ":guava"],
                       provided=[":guava"],
                   )
                   """
               ),
               "src/app/core.clj": dedent(
                   """\
                   (ns app.core
                     (:gen-class))

                   (defn -main [& args]
                     (println "Hello"))
                   """
               ),
           }
       )

       target = rule_runner.get_target(Address("src/app", target_name="app"))
       field_set = ClojureDeployJarFieldSet.create(target)
       result = rule_runner.request(BuiltPackage, [field_set])

       # Read JAR contents and verify guava classes are NOT present
       jar_content = get_jar_content(result)
       with zipfile.ZipFile(io.BytesIO(jar_content), 'r') as jar:
           jar_entries = set(jar.namelist())

       # Guava classes should NOT be in the JAR
       guava_entries = [e for e in jar_entries if 'google' in e or 'guava' in e]
       assert len(guava_entries) == 0, f"Provided dependency guava should NOT be in JAR"
   ```

---

## Files Changed

| File | Change |
|------|--------|
| `pants-plugins/clojure_backend/goals/package.py` | Fix prefix pattern from `f"{artifact}-"` to `f"{group}_{artifact}_"` |
| `pants-plugins/tests/test_package_clojure_deploy_jar.py` | Add test for third-party JAR exclusion |

---

## Verification Checklist

After the fix:
- [ ] Third-party JARs (e.g., `com.rpl/rama`) are correctly excluded when marked as provided
- [ ] Artifacts with same name but different group (e.g., `org.example/rama` vs `com.rpl/rama`) are distinguished correctly
- [ ] First-party Clojure source exclusion continues to work (address-based filtering)
- [ ] All existing tests pass: `pants test pants-plugins::`

---

## Risk Assessment

**Low Risk**: This is a minimal, surgical fix that:
1. Changes only the prefix string format (1 line of code)
2. Updates a comment (cosmetic)
3. Does not change any control flow or logic
4. Does not affect first-party source filtering (which uses address-based, not filename-based matching)
5. The new format matches the actual JAR naming convention verified from existing lockfiles

## Alternative Considered

**Use lockfile for exact filename mapping**: Instead of pattern matching, load the Coursier lockfile and build an exact `filename -> coordinate` mapping. This was deferred in the original plan because:
1. Pattern matching is sufficient for the standard naming convention
2. Adds complexity without clear benefit for common cases
3. Can be added later if edge cases arise

The current fix aligns with the "simple approach" but corrects the pattern to match Pants' actual naming convention as verified from lockfile inspection.
