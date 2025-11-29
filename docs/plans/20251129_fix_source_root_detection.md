# Fix Source Root Detection in deps.edn Generation

**Date:** 2025-11-29

**Problem:** When generating deps.edn files, source roots are incorrectly detected. In projects with a structure like:

```
sub-project/
  BUILD             # clojure_sources(sources=["src/**/*.clj"])
  src/
    BUILD           # empty, marks source root
    myapp/
      core.clj      # (ns myapp.core)
  test/
    BUILD           # empty, marks source root
    myapp/
      core_test.clj # (ns myapp.core-test)
```

The deps.edn `:paths` should contain `sub-project/src` but instead contains `sub-project` (the directory with the clojure_sources BUILD file).

## Root Cause Analysis

The bug is in the wrapper function `determine_source_root()` in `generate_deps.py` (lines 152-172). This function attempts to reconstruct the source file path from the target address and source field, instead of using the actual file path available from `DigestContents`.

### The Bug

In `generate_deps.py:152-172`:
```python
def determine_source_root(target: Target, source_field, source_content: str) -> str | None:
    namespace = parse_namespace(source_content)
    if not namespace:
        return None

    # Bug: Constructs path from target address
    source_file = target.address.spec_path  # e.g., "sub-project"
    if source_field.value:
        if source_field.value:
            source_file = str(PurePath(target.address.spec_path) / source_field.value[0])

    return _determine_source_root(source_file, namespace)
```

**The problem:** `source_field.value` contains a **glob pattern** like `["src/**/*.clj"]` from the BUILD file's `sources` field, NOT an actual file path. When the code joins `spec_path` (e.g., `"sub-project"`) with `source_field.value[0]` (e.g., `"src/**/*.clj"`), it produces a malformed path like `"sub-project/src/**/*.clj"`.

When `_determine_source_root` can't match this nonsense path to the namespace, it falls back to returning the directory containing the "file" - which ends up being incorrect.

### The Correct Data Source

The calling code in `gather_clojure_sources_for_resolve` (lines 244-278) already has access to `DigestContents` from `SourceFilesRequest`, which contains the **actual file paths** (e.g., `"sub-project/src/myapp/core.clj"`). This is what should be used.

### Comparison with repl.py

`repl.py` (line 216) does this correctly:
```python
file_path = digest_contents[0].path  # Uses actual file path
source_root = determine_source_root(file_path, namespace)  # Calls utility directly
```

It bypasses the broken wrapper and calls `_determine_source_root` (the utility function in `source_roots.py`) directly with the actual file path.

## Affected Files

1. **`pants-plugins/clojure_backend/goals/generate_deps.py`** (lines 152-172, 244-278)
   - The `determine_source_root` wrapper function incorrectly reconstructs the path
   - Should use the actual file path from digest contents instead

2. **`pants-plugins/clojure_backend/goals/repl.py`** (lines 207-208, 221-222)
   - Fallback logic uses `target.address.spec_path` which could produce wrong results when digest is empty
   - Less critical since the happy path works correctly

## Implementation Plan

### Phase 1: Fix generate_deps.py source root detection

**Goal:** Update `generate_deps.py` to use the actual file path from digest contents instead of the broken wrapper function.

**Changes:**

1. **Modify the `determine_source_root` wrapper function (lines 152-172)** to accept the file path directly:
   ```python
   def determine_source_root(file_path: str, source_content: str) -> str | None:
       """Determine the source root directory for a Clojure file.

       Args:
           file_path: The actual path to the Clojure source file (from DigestContents)
           source_content: The file content for namespace parsing

       Returns None if the namespace can't be parsed.
       """
       namespace = parse_namespace(source_content)
       if not namespace:
           return None

       return _determine_source_root(file_path, namespace)
   ```

2. **Update the calls in `gather_clojure_sources_for_resolve`** (lines 251-259, 270-278) to pass the actual file path:
   ```python
   # Process source targets
   for i, target in enumerate(source_targets):
       digest_contents = all_digest_contents[i]
       if not digest_contents:
           # Fallback: use target directory (only when no files match)
           source_roots.add(target.address.spec_path or ".")
           continue

       source_content = digest_contents[0].content.decode("utf-8")
       file_path = digest_contents[0].path  # Use actual file path from digest
       source_root = determine_source_root(file_path, source_content)

       if source_root:
           source_roots.add(source_root)
       else:
           # Fallback: use directory containing the file
           source_roots.add("/".join(file_path.split("/")[:-1]) or ".")
   ```

3. **Apply the same changes to the test targets processing section** (lines 261-278).

4. **Note on multiple files per target:** The code currently uses only `digest_contents[0]` (the first file). This is acceptable because:
   - All files in a target should share the same source root
   - We only need one file to determine the source root
   - If files are in different directories, they would typically be in separate targets

### Phase 2: Add integration test with content verification

**Goal:** Add a test case that specifically covers the bug scenario and verifies the actual deps.edn content.

**Changes:**

1. Add a new test `test_generate_deps_edn_nested_source_dirs` to `test_generate_deps_edn.py` that:
   - Creates a project structure with BUILD file in parent directory
   - Has src/ subdirectory with source files
   - Has test/ subdirectory with test files
   - **Verifies the actual content** of the generated deps.edn (not just exit code)

**Test structure:**
```
sub-project/
  BUILD                       # clojure_sources(sources=["src/**/*.clj"])
                              # clojure_tests(sources=["test/**/*.clj"])
  src/
    myapp/
      core.clj                # (ns myapp.core)
  test/
    myapp/
      core_test.clj           # (ns myapp.core-test)
```

**Test implementation:**
```python
def test_generate_deps_edn_nested_source_dirs(rule_runner: RuleRunner) -> None:
    """Test that source roots are correctly detected for nested directory structures."""
    rule_runner.write_files(
        {
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="org.clojure_clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="1.12.0",
                )
                """
            ),
            "3rdparty/jvm/default.lock": _CLOJURE_LOCKFILE,
            "sub-project/BUILD": dedent(
                """\
                clojure_sources(
                    name='lib',
                    sources=["src/**/*.clj"],
                    dependencies=[
                        '3rdparty/jvm:org.clojure_clojure',
                    ],
                )
                clojure_tests(
                    name='tests',
                    sources=["test/**/*.clj"],
                    dependencies=[
                        ':lib',
                        '3rdparty/jvm:org.clojure_clojure',
                    ],
                )
                """
            ),
            "sub-project/src/myapp/core.clj": dedent(
                """\
                (ns myapp.core)

                (defn greet [name]
                  (str "Hello, " name "!"))
                """
            ),
            "sub-project/test/myapp/core_test.clj": dedent(
                """\
                (ns myapp.core-test
                  (:require [clojure.test :refer [deftest is]]
                            [myapp.core :refer [greet]]))

                (deftest test-greet
                  (is (= "Hello, World!" (greet "World"))))
                """
            ),
        }
    )

    args = [
        f"--jvm-resolves={repr(_JVM_RESOLVES)}",
        "--jvm-default-resolve=jvm-default",
    ]
    rule_runner.set_options(args, env_inherit=PYTHON_BOOTSTRAP_ENV)

    # Run the goal
    result = rule_runner.run_goal_rule(
        GenerateDepsEdn,
        args=["--generate-deps-edn-resolve=jvm-default"],
        env_inherit=PYTHON_BOOTSTRAP_ENV,
    )

    assert result.exit_code == 0

    # Read and verify the generated deps.edn content
    deps_edn_path = rule_runner.build_root / "deps.edn"
    deps_edn_content = deps_edn_path.read_text()

    # Verify source paths contain the correct nested path
    assert "sub-project/src" in deps_edn_content, \
        f"Expected 'sub-project/src' in :paths, got: {deps_edn_content}"

    # Verify test paths contain the correct nested path
    assert "sub-project/test" in deps_edn_content, \
        f"Expected 'sub-project/test' in :test :extra-paths, got: {deps_edn_content}"

    # Verify we DON'T have the incorrect parent directory
    # Check that "sub-project" alone (not followed by /src or /test) is not in paths
    import re
    # Match "sub-project" that's not followed by /src or /test
    incorrect_path_pattern = r'"sub-project"(?!/(?:src|test))'
    assert not re.search(incorrect_path_pattern, deps_edn_content), \
        f"Found incorrect path 'sub-project' without /src or /test suffix: {deps_edn_content}"
```

### Phase 3: Verify repl.py consistency

**Goal:** Ensure repl.py handles source roots consistently.

**Analysis:**
- `repl.py` already uses `digest_contents[0].path` (line 216) for the happy path - this is correct
- The fallback at lines 207-208 and 221-222 uses `target.address.spec_path`, which only triggers when `digest_contents` is empty (no files matched the glob)
- When no files are found, using the target directory is a reasonable fallback

**No changes needed** for repl.py - the current implementation is correct for the cases that matter.

### Phase 4: Run tests and verify

**Goal:** Ensure all existing tests pass and the new test passes.

**Steps:**
1. Run `pants test pants-plugins::` to verify all tests pass
2. Verify the new test specifically tests the bug scenario

## Summary of Changes

| File | Change |
|------|--------|
| `generate_deps.py` | Modify `determine_source_root` to accept file path directly instead of reconstructing it |
| `generate_deps.py` | Update calls to use `digest_contents[0].path` for the actual file path |
| `test_generate_deps_edn.py` | Add new test with content verification for nested source roots |

## Verification

After implementation, verify:

1. For a project with structure:
   ```
   sub-project/BUILD         # clojure_sources(sources=["src/**/*.clj"])
   sub-project/src/myapp/core.clj
   ```

   Running `pants generate-deps-edn` should produce:
   ```clojure
   {:paths ["sub-project/src"]
    ...}
   ```

   NOT:
   ```clojure
   {:paths ["sub-project"]
    ...}
   ```

2. All existing tests should continue to pass
3. The new integration test should pass with content verification
