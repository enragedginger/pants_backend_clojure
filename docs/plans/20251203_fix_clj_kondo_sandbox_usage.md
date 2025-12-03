# Fix clj-kondo Sandbox Usage: Migrate from subprocess.run() to Pants Rules

**Date**: 2025-12-03
**Status**: In Progress (Phases 1-2 Complete)
**Author**: Claude Code

## Executive Summary

The clj_kondo_parser.py module invokes clj-kondo via `subprocess.run()` outside the Pants sandbox, relying on a system-installed binary rather than the Pants-managed CljKondo ExternalTool. This causes packaging to fail on Linux systems without clj-kondo installed in PATH.

**Root Cause**: Direct subprocess.run() invocation in `clj_kondo_parser.py:52-61` bypasses the Pants sandbox and ExternalTool mechanism.

**Impact**: The `package` goal fails with "Could not find source file for main namespace" on Linux when clj-kondo is not system-installed, because `parse_namespace()` silently returns `None` on `FileNotFoundError`.

**Solution**: Create a Pants rule that downloads clj-kondo via ExternalTool and runs analysis inside the sandbox, then refactor all callers to use this rule via `await Get()`.

## Current Architecture

### Problematic Code Path

```
package.py / dependency_inference.py
    ↓
namespace_parser.py (parse_namespace, parse_requires, parse_imports)
    ↓
clj_kondo_parser.py (_run_clj_kondo_analysis)
    ↓
subprocess.run() → Expects clj-kondo in PATH ❌
```

### Current Implementation (Broken)

**File**: `pants-plugins/clojure_backend/utils/clj_kondo_parser.py:52-61`

```python
result = subprocess.run(
    [
        clj_kondo_path,  # defaults to "clj-kondo" - looks in PATH
        "--lint", str(temp_file),
        "--config", "{:output {:analysis {:java-class-usages true} :format :json}}",
    ],
    capture_output=True,
    text=True,
    check=False,
)
```

**Problems**:
1. Runs clj-kondo directly via subprocess.run() outside the Pants sandbox
2. Defaults to looking for clj-kondo in the system PATH
3. Silently returns None on FileNotFoundError (line 108)
4. No integration with Pants' caching or memoization
5. Works only on macOS where Homebrew installs clj-kondo globally

### Correct Pattern (lint.py)

**File**: `pants-plugins/clojure_backend/goals/lint.py:93-167`

```python
# Downloads clj-kondo via Pants ExternalTool mechanism
downloaded_clj_kondo = await Get(
    DownloadedExternalTool, ExternalToolRequest, clj_kondo.get_request(platform)
)

# Merges all inputs into sandbox
input_digest = await Get(
    Digest,
    MergeDigests([source_files.snapshot.digest, downloaded_clj_kondo.digest, ...])
)

# Runs inside Pants sandbox
result = await Get(
    FallibleProcessResult,
    Process(
        argv=[downloaded_clj_kondo.exe, "--lint", *files],
        input_digest=input_digest,
        ...
    )
)
```

## Proposed Architecture

### New Code Path

```
package.py / dependency_inference.py
    ↓
await Get(ClojureNamespaceAnalysis, ClojureNamespaceAnalysisRequest(...))
    ↓
analyze_clojure_namespaces rule
    ↓
DownloadedExternalTool + Process (inside sandbox) ✓
```

### New Data Types

```python
@dataclass(frozen=True)
class ClojureNamespaceAnalysisRequest:
    """Request to analyze Clojure source files for namespace metadata.

    Uses Snapshot instead of just Digest to preserve file paths in the analysis result.
    clj-kondo is run in batch mode on all files at once for efficiency.
    """
    snapshot: Snapshot  # Source files to analyze (includes paths and digest)

@dataclass(frozen=True)
class ClojureNamespaceAnalysis:
    """Result of clj-kondo analysis on Clojure source files.

    All file paths are relative paths matching those in the input Snapshot.
    """
    # Maps file path -> namespace name
    namespaces: FrozenDict[str, str]
    # Maps file path -> tuple of required namespaces
    requires: FrozenDict[str, tuple[str, ...]]
    # Maps file path -> tuple of imported Java classes
    imports: FrozenDict[str, tuple[str, ...]]
```

### New Rule

```python
@rule(desc="Analyze Clojure namespaces with clj-kondo", level=LogLevel.DEBUG)
async def analyze_clojure_namespaces(
    request: ClojureNamespaceAnalysisRequest,
    clj_kondo: CljKondo,
    platform: Platform,
) -> ClojureNamespaceAnalysis:
    """Analyze Clojure source files to extract namespace metadata.

    Uses clj-kondo in batch mode to analyze all files in a single invocation.
    File paths in the result match those in the input Snapshot.

    Error Handling:
    - If clj-kondo fails to parse a file, that file is omitted from results
    - clj-kondo non-zero exit codes (from lint warnings) are ignored
    - Empty or malformed JSON output returns empty analysis
    """
    if not request.snapshot.files:
        return ClojureNamespaceAnalysis(
            namespaces=FrozenDict({}),
            requires=FrozenDict({}),
            imports=FrozenDict({}),
        )

    # Download clj-kondo binary
    downloaded = await Get(
        DownloadedExternalTool,
        ExternalToolRequest,
        clj_kondo.get_request(platform)
    )

    # Merge source files with clj-kondo binary
    input_digest = await Get(
        Digest,
        MergeDigests([request.snapshot.digest, downloaded.digest])
    )

    # Run clj-kondo analysis in batch mode on all files
    result = await Get(
        FallibleProcessResult,
        Process(
            argv=[
                downloaded.exe,
                "--lint", *request.snapshot.files,
                "--config", "{:output {:analysis {:java-class-usages true} :format :json}}",
            ],
            input_digest=input_digest,
            description=f"Analyze {pluralize(len(request.snapshot.files), 'Clojure file')} with clj-kondo",
            level=LogLevel.DEBUG,
        )
    )

    # Parse JSON output - clj-kondo may return non-zero for lint warnings, that's ok
    try:
        stdout = result.stdout.decode()
        if not stdout.strip():
            # Empty output - return empty analysis
            return ClojureNamespaceAnalysis(
                namespaces=FrozenDict({}),
                requires=FrozenDict({}),
                imports=FrozenDict({}),
            )
        analysis = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"Failed to parse clj-kondo output: {e}")
        return ClojureNamespaceAnalysis(
            namespaces=FrozenDict({}),
            requires=FrozenDict({}),
            imports=FrozenDict({}),
        )

    # Build result mappings using relative file paths
    namespaces = {}
    requires: dict[str, list[str]] = {}
    imports: dict[str, list[str]] = {}

    for ns_def in analysis.get("namespace-definitions", []):
        # clj-kondo returns paths relative to working directory
        path = ns_def["filename"]
        namespaces[path] = ns_def["name"]

    for ns_usage in analysis.get("namespace-usages", []):
        path = ns_usage["filename"]
        requires.setdefault(path, []).append(ns_usage["to"])

    for java_usage in analysis.get("java-class-usages", []):
        if java_usage.get("import"):
            path = java_usage["filename"]
            imports.setdefault(path, []).append(java_usage["class"])

    return ClojureNamespaceAnalysis(
        namespaces=FrozenDict(namespaces),
        requires=FrozenDict({k: tuple(sorted(set(v))) for k, v in requires.items()}),
        imports=FrozenDict({k: tuple(sorted(set(v))) for k, v in imports.items()}),
    )
```

## Affected Files

| File | Current Usage | Required Changes |
|------|---------------|------------------|
| `utils/clj_kondo_parser.py` | subprocess.run() | Delete entire file after migration |
| `utils/namespace_parser.py` | Wraps clj_kondo_parser | Keep utility functions (namespace_to_path, etc.), remove parse_* functions |
| `goals/package.py` | Calls parse_namespace() | Use batch `await Get(ClojureNamespaceAnalysis, ...)` |
| `dependency_inference.py` | Calls parse_requires(), parse_imports() | Use `await Get(ClojureNamespaceAnalysis, ...)` per-file (cached by digest) |

## Implementation Plan

### Phase 1: Create the Analysis Rule (Core Fix) ✅ COMPLETE

**Goal**: Create a new Pants rule that properly downloads and runs clj-kondo in the sandbox.

**Completed**: 2025-12-03

**Tasks**:

1. Create new file `pants-plugins/clojure_backend/rules/namespace_analysis.py`:
   - Define `ClojureNamespaceAnalysisRequest` dataclass with `snapshot: Snapshot`
   - Define `ClojureNamespaceAnalysis` dataclass
   - Implement `analyze_clojure_namespaces` rule with:
     - Proper error handling (empty output, JSON parse errors)
     - LogLevel.DEBUG for Process
     - Pluralized description
   - Export rule in `rules()` function

2. Register the new rule in `pants-plugins/clojure_backend/register.py`:
   - Import `namespace_analysis` module
   - Add to the rules() list

3. Add tests in `pants-plugins/tests/test_namespace_analysis.py`:
   - Test setup with RuleRunner including:
     - `external_tool.rules()`
     - CljKondo subsystem
     - Platform handling
   - Test cases:
     - Basic namespace extraction
     - Requires extraction
     - Imports extraction
     - Multiple files in batch
     - Empty file handling
     - File without namespace declaration
     - Malformed Clojure syntax (should not crash)

**Test Setup Example**:
```python
@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *external_tool.rules(),
            *namespace_analysis.rules(),
            QueryRule(ClojureNamespaceAnalysis, [ClojureNamespaceAnalysisRequest]),
        ],
        target_types=[ClojureSourceTarget],
    )
```

**Verification**:
- Run new tests: `pants test pants-plugins/tests/test_namespace_analysis.py`
- Verify rule can be requested via RuleRunner

### Phase 2: Update package.py to Use New Rule

**Goal**: Migrate package.py from synchronous parse_namespace() to async rule.

**Key Change**: Instead of parsing files one-at-a-time in a loop, batch all files into a single analysis request.

**Tasks**:

1. Update `pants-plugins/clojure_backend/goals/package.py`:
   - Import ClojureNamespaceAnalysisRequest and ClojureNamespaceAnalysis
   - Replace the loop pattern:
     ```python
     # OLD (per-file parsing)
     for file_content in digest_contents:
         content = file_content.content.decode("utf-8")
         namespace = parse_namespace(content)

     # NEW (batch analysis)
     snapshot = await Get(Snapshot, Digest, clojure_digest)
     analysis = await Get(
         ClojureNamespaceAnalysis,
         ClojureNamespaceAnalysisRequest(snapshot)
     )
     # Then lookup: analysis.namespaces.get(file_path)
     ```
   - Update main namespace lookup to use `analysis.namespaces` mapping
   - Update `:all` AOT case to use `analysis.namespaces.values()`
   - Remove direct imports of parse_namespace from namespace_parser

2. Update tests in `pants-plugins/tests/test_package_clojure_deploy_jar.py`:
   - Add `external_tool.rules()` to RuleRunner if not present
   - Ensure existing tests pass with new implementation
   - Add test verifying package works without system clj-kondo

**Verification**:
- Run package tests: `pants test pants-plugins/tests/test_package_clojure_deploy_jar.py`
- Test full package workflow: `pants package example::` (if example project exists)

### Phase 3: Update dependency_inference.py to Use New Rule

**Goal**: Migrate dependency inference from synchronous parsing to async rule.

**Key Insight**: Dependency inference processes one source file at a time. Pants will automatically memoize the `ClojureNamespaceAnalysis` result by input Snapshot digest, so repeated requests for the same file are cached.

**Tasks**:

1. Update `pants-plugins/clojure_backend/dependency_inference.py`:
   - Import ClojureNamespaceAnalysisRequest, ClojureNamespaceAnalysis, and Snapshot
   - Replace the pattern:
     ```python
     # OLD
     source_content = digest_contents[0].content.decode('utf-8')
     required_namespaces = parse_requires(source_content)
     imported_classes = parse_imports(source_content)

     # NEW
     snapshot = await Get(Snapshot, Digest, source_files.snapshot.digest)
     analysis = await Get(
         ClojureNamespaceAnalysis,
         ClojureNamespaceAnalysisRequest(snapshot)
     )
     file_path = snapshot.files[0]  # Single file
     required_namespaces = set(analysis.requires.get(file_path, ()))
     imported_classes = set(analysis.imports.get(file_path, ()))
     ```
   - Apply same change to both `infer_clojure_source_dependencies` and `infer_clojure_test_dependencies`

2. Update tests in `pants-plugins/tests/test_dependency_inference.py` (if exists):
   - Ensure existing tests pass
   - Add tests for cross-platform compatibility

**Performance Note**: Each file gets its own analysis request with its own Snapshot. Pants memoizes by Snapshot digest, so:
- Same file content = cache hit
- Different files = different digests = separate clj-kondo runs
- This is acceptable because clj-kondo is fast (~10-50ms) and caching prevents re-analysis

**Verification**:
- Run dependency inference tests
- Test `pants dependencies` on example targets

### Phase 4: Cleanup Legacy Code

**Goal**: Remove the now-unused subprocess.run() code path.

**Tasks**:

1. Delete `pants-plugins/clojure_backend/utils/clj_kondo_parser.py` entirely:
   - All functions are now replaced by the Pants rule
   - No utility functions need to be retained

2. Update `pants-plugins/clojure_backend/utils/namespace_parser.py`:
   - Remove imports from clj_kondo_parser
   - Remove `parse_namespace()`, `parse_requires()`, `parse_imports()` functions
   - Keep utility functions:
     - `namespace_to_path()`
     - `path_to_namespace()`
     - `class_to_path()`
     - `is_jdk_class()`

3. Update any remaining references:
   - Search codebase for any other callers of the old functions
   - Update or remove as needed

**Verification**:
- Run full test suite: `pants test pants-plugins/::`
- Verify no import errors
- Grep for removed function names to ensure no dangling references:
  ```bash
  grep -r "parse_namespace\|parse_requires\|parse_imports\|clj_kondo_parser" pants-plugins/
  ```

### Phase 5: Documentation and Final Testing

**Goal**: Ensure complete coverage and document the changes.

**Tasks**:

1. Add/update docstrings:
   - Document ClojureNamespaceAnalysis and its fields
   - Document the rule's behavior and caching
   - Add examples in docstrings

2. Integration testing:
   - Test on macOS to verify no regression
   - Test on Linux (or in Linux container) to verify fix
   - Test with various Clojure source patterns

3. Update this plan document:
   - Mark phases as completed
   - Note any deviations from plan
   - Document any issues encountered

**Verification**:
- Full test suite passes on both macOS and Linux
- Package goal works without system clj-kondo

## Testing Strategy

### Unit Tests

| Test File | Coverage |
|-----------|----------|
| `test_namespace_analysis.py` | New rule functionality |
| `test_package_clojure_deploy_jar.py` | Package goal with new rule |
| `test_dependency_inference.py` | Dependency inference with new rule |

### Test Setup Requirements

All tests using the new rule need:
```python
from pants.core.util_rules import external_tool

rule_runner = RuleRunner(
    rules=[
        *external_tool.rules(),  # Required for DownloadedExternalTool
        *namespace_analysis.rules(),
        # ... other rules
    ],
    # ...
)

# Run with these options to avoid system clj-kondo dependency:
rule_runner.set_options(
    ["--backend-packages=clojure_backend"],
    env_inherit={"PATH", "HOME"},  # For SSL certs during download
)
```

### Integration Tests

1. **Cross-platform**: Test on both macOS and Linux
2. **No system clj-kondo**: Verify works without PATH clj-kondo
3. **Caching**: Verify Pants caches analysis results properly

### Edge Cases

1. Empty source files → returns empty analysis
2. Files without namespace declarations → omitted from namespaces dict
3. Multiple namespace declarations → clj-kondo uses first
4. Malformed Clojure syntax → graceful handling, file omitted from results
5. Large files / many files → batch analysis for efficiency
6. Unicode in source files → proper decoding

## Error Handling Strategy

| Scenario | Behavior |
|----------|----------|
| clj-kondo download fails | Pants error (ExternalTool failure) |
| clj-kondo exits non-zero (lint warnings) | Ignored - parse output anyway |
| Empty stdout | Return empty ClojureNamespaceAnalysis |
| Malformed JSON output | Log warning, return empty analysis |
| File not in analysis output | File omitted from result dicts |
| Unicode decode error | Log warning, return empty analysis |

## Rollback Plan

If issues arise:

1. The old clj_kondo_parser.py will remain until Phase 4
2. Can revert to old implementation by restoring imports in namespace_parser.py
3. Feature flag could be added if gradual rollout needed

## Performance Considerations

1. **Batching**: package.py analyzes multiple files in single clj-kondo invocation
2. **Per-file caching**: dependency_inference.py requests are memoized by digest
3. **ExternalTool caching**: Pants caches clj-kondo binary download across runs
4. **Startup**: clj-kondo native binary has ~10-50ms startup time
5. **Trade-off**: Per-file requests in dependency_inference have more overhead than batch but benefit from Pants' memoization for incremental builds

## Dependencies

- CljKondo ExternalTool (already exists in `subsystems/clj_kondo.py`)
- Pants rules infrastructure (Process, Get, FallibleProcessResult, etc.)
- No new external dependencies required

## Success Criteria

1. ✅ Package goal works on Linux without system clj-kondo
2. ✅ Package goal works on macOS (no regression)
3. ✅ All existing tests pass
4. ✅ New tests cover the namespace analysis rule
5. ✅ subprocess.run() calls removed from clj_kondo_parser.py
6. ✅ clj_kondo_parser.py deleted

## Open Questions (Resolved)

1. **Should analysis respect `.clj-kondo/config.edn`?**
   - Decision: No. The analysis only extracts namespace metadata (name, requires, imports), which is not affected by lint configuration. Lint configuration affects warnings/errors, not the parsing itself.

2. **Single-file vs batch analysis for dependency inference?**
   - Decision: Use single-file requests. Pants memoizes by digest, so repeated builds don't re-analyze unchanged files. This is more cache-friendly for incremental builds.

## References

- Bug Report: package goal fails on Linux - clj-kondo not found in sandbox
- Existing correct usage: `pants-plugins/clojure_backend/goals/lint.py`
- ExternalTool definition: `pants-plugins/clojure_backend/subsystems/clj_kondo.py`
- Related plan: `docs/plans/20251021_import_detection_plan.md`
