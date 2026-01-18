# Migration Plan: Convert Pants Plugin to Call-by-Name

**Date:** 2026-01-17
**Target Pants Version:** 2.30.0
**Status:** Completed

## Overview

This plan outlines the migration of the `pants-backend-clojure` plugin from the deprecated `Get()`/`MultiGet()` rule invocation pattern to the new call-by-name pattern. This migration is required because:

- `Get()` and `MultiGet()` are deprecated in Pants 2.30.0
- They will be removed entirely in Pants 2.31.0
- Call-by-name provides better type safety and more explicit dependency graphs

## Background

### Old Pattern (Deprecated)
```python
from pants.engine.rules import Get, MultiGet

# Single call
result = await Get(OutputType, InputType, input_value)

# Multiple calls
results = await MultiGet(
    Get(Type1, Request1(args)),
    Get(Type2, Request2(args)),
)
```

### New Pattern (Call-by-Name)
```python
from pants.engine.rules import concurrently, implicitly

# Single call - direct function invocation
result = await rule_function(input_value, **implicitly())

# Multiple calls - use concurrently
results = await concurrently(
    rule_function1(args, **implicitly()),
    rule_function2(args, **implicitly()),
)
```

### Key Concepts

1. **`implicitly()`** - Passes implicit dependencies (subsystems, environment, etc.) to rule calls
2. **`concurrently()`** - Replaces `MultiGet()` for parallel rule execution
3. **Direct imports** - Rules are imported by name and called directly
4. **Type conversion via implicitly** - When the input type differs: `await rule(**implicitly({value: InputType}))`

## Scope of Changes

### Files to Modify (15 files, ~106 Get/MultiGet calls)

| File | Get Calls | MultiGet Calls | Priority |
|------|-----------|----------------|----------|
| `goals/package.py` | 18 | 2 | High |
| `goals/repl.py` | 12 | 3 | High |
| `goals/test.py` | 10 | 1 | High |
| `goals/check.py` | 9 | 2 | High |
| `tools_build_uberjar.py` | 10 | 1 | High |
| `goals/generate_deps.py` | 6 | 2 | Medium |
| `goals/generate_clojure_lockfile_metadata.py` | 4 | 2 | Medium |
| `goals/lint.py` | 5 | 0 | Medium |
| `compile_clj.py` | 4 | 0 | Medium |
| `dependency_inference.py` | 3 | 1 | Medium |
| `namespace_analysis.py` | 4 | 0 | Medium |
| `provided_dependencies.py` | 4 | 1 | Medium |
| `goals/fmt.py` | 4 | 0 | Low |
| `clojure_symbol_mapping.py` | 2 | 0 | Low |
| `subsystems/tools_build.py` | 1 | 0 | Low |

### Common Transformations Required

#### 1. File System Operations (Intrinsics)

| Old Pattern | New Pattern | Import |
|-------------|-------------|--------|
| `Get(Digest, MergeDigests([...]))` | `await merge_digests(MergeDigests([...]))` | `from pants.engine.intrinsics import merge_digests` |
| `Get(Digest, CreateDigest([...]))` | `await create_digest(CreateDigest([...]))` | `from pants.engine.intrinsics import create_digest` |
| `Get(Digest, PathGlobs([...]))` | `await path_globs_to_digest(PathGlobs([...]))` | `from pants.engine.intrinsics import path_globs_to_digest` |
| `Get(Digest, AddPrefix(d, p))` | `await add_prefix(AddPrefix(d, p))` | `from pants.engine.intrinsics import add_prefix` |
| `Get(DigestContents, Digest, d)` | `await get_digest_contents(d)` | `from pants.engine.intrinsics import get_digest_contents` |
| `Get(Digest, RemovePrefix(d, p))` | `await remove_prefix(RemovePrefix(d, p))` | `from pants.engine.intrinsics import remove_prefix` |

#### 2. Process Execution

| Old Pattern | New Pattern | Import |
|-------------|-------------|--------|
| `Get(FallibleProcessResult, Process, p)` | `await execute_process(p, **implicitly())` | `from pants.engine.intrinsics import execute_process` |
| `Get(ProcessResult, Process, p)` | `await execute_process(p, **implicitly())` then check `.exit_code` | `from pants.engine.intrinsics import execute_process` |
| `Get(Process, JvmProcess, jp)` | `await jvm_process(**implicitly(jp))` | `from pants.jvm.jdk_rules import jvm_process` |

#### 3. JVM Operations

| Old Pattern | New Pattern | Import |
|-------------|-------------|--------|
| `Get(JdkEnvironment, JdkRequest, r)` | `await prepare_jdk_environment(**implicitly(r))` | `from pants.jvm.jdk_rules import prepare_jdk_environment` |
| `Get(ToolClasspath, ToolClasspathRequest(...))` | `await materialize_classpath_for_tool(ToolClasspathRequest(...))` | `from pants.jvm.resolve.coursier_fetch import materialize_classpath_for_tool` |
| `Get(Classpath, Addresses(...))` | `await classpath(**implicitly({addresses: Addresses}))` | `from pants.jvm.classpath import classpath` |
| `Get(ClasspathEntry, CoursierLockfileEntry, e)` | `await coursier_fetch_one_coord(e)` | `from pants.jvm.resolve.coursier_fetch import coursier_fetch_one_coord` |

#### 4. Source Files

| Old Pattern | New Pattern | Import |
|-------------|-------------|--------|
| `Get(SourceFiles, SourceFilesRequest(...))` | `await determine_source_files(SourceFilesRequest(...))` | `from pants.core.util_rules.source_files import determine_source_files` |
| `Get(StrippedSourceFiles, SourceFiles, sf)` | `await strip_source_roots(sf)` | `from pants.core.util_rules.stripped_source_files import strip_source_roots` |
| `Get(StrippedSourceFiles, SourceFilesRequest(...))` | `await strip_source_roots(**implicitly(SourceFilesRequest(...)))` | Same as above |

#### 5. Target Resolution

| Old Pattern | New Pattern | Import |
|-------------|-------------|--------|
| `Get(TransitiveTargets, TransitiveTargetsRequest(...))` | `await transitive_targets(TransitiveTargetsRequest(...), **implicitly())` | `from pants.engine.internals.graph import transitive_targets` |
| `Get(Targets, Addresses(...))` | `await resolve_targets(**implicitly({Addresses(...): Addresses}))` | `from pants.engine.internals.graph import resolve_targets` |
| `Get(Targets, UnparsedAddressInputs, u)` | `await resolve_unparsed_address_inputs(u, **implicitly())` | `from pants.engine.internals.graph import resolve_unparsed_address_inputs` |
| `Get(Owners, OwnersRequest(...))` | `await find_owners(OwnersRequest(...), **implicitly())` | `from pants.engine.internals.graph import find_owners` |
| `Get(AllTargets)` | `await find_all_targets(**implicitly())` | `from pants.engine.internals.graph import find_all_targets` |

#### 6. External Tools & Config

| Old Pattern | New Pattern | Import |
|-------------|-------------|--------|
| `Get(DownloadedExternalTool, ExternalToolRequest, r)` | `await download_external_tool(r)` | `from pants.core.util_rules.external_tool import download_external_tool` |
| `Get(ConfigFiles, ConfigFilesRequest(...))` | `await find_config_file(ConfigFilesRequest(...))` | `from pants.core.util_rules.config_files import find_config_file` |

#### 7. Other

| Old Pattern | New Pattern | Import |
|-------------|-------------|--------|
| `Get(ExplicitlyProvidedDependencies, DependenciesRequest(...))` | `await determine_explicitly_provided_dependencies(DependenciesRequest(...))` | `from pants.engine.target import determine_explicitly_provided_dependencies` |
| `Get(EnvironmentVars, EnvironmentVarsRequest(...))` | `await environment_vars_subset(EnvironmentVarsRequest(...))` | `from pants.core.util_rules.env_vars import environment_vars_subset` |
| `Get(ProcessResultWithRetries, ProcessWithRetries(...))` | `await execute_process_with_retry(ProcessWithRetries(...), **implicitly())` | `from pants.engine.intrinsics import execute_process_with_retry` |

---

## Implementation Phases

### Phase 1: Low-Risk Files (Foundation) - **DONE**

**Goal:** Migrate simple files with few Get calls to establish patterns and validate approach.

**Files:**
1. `subsystems/tools_build.py` (1 Get)
2. `clojure_symbol_mapping.py` (2 Gets)
3. `goals/fmt.py` (4 Gets)

**Tasks:**
- [x] Update imports in each file
- [x] Convert Get() calls to call-by-name
- [x] Run tests to verify functionality
- [x] Document any issues or edge cases discovered

**Notes:**
- `materialize_classpath_for_tool`, `download_external_tool`, `find_config_file` don't require `implicitly()`
- Intrinsics like `path_globs_to_digest`, `get_digest_contents`, `merge_digests` don't require `implicitly()`
- `execute_process` DOES require `**implicitly()` for ProcessExecutionEnvironment

**Validation:**
```bash
pants check pants-plugins/pants_backend_clojure/subsystems/::
pants check pants-plugins/pants_backend_clojure/clojure_symbol_mapping.py
pants check pants-plugins/pants_backend_clojure/goals/fmt.py
pants fmt pants-plugins/::  # Test fmt goal works
```

---

### Phase 2: Analysis & Inference Rules - **DONE**

**Goal:** Migrate files involved in namespace analysis and dependency inference.

**Files:**
1. `namespace_analysis.py` (4 Gets)
2. `dependency_inference.py` (3 Gets, 1 MultiGet)
3. `provided_dependencies.py` (4 Gets, 1 MultiGet)

**Tasks:**
- [x] Migrate `namespace_analysis.py`
- [x] Migrate `dependency_inference.py`
- [x] Migrate `provided_dependencies.py`
- [x] Run dependency inference tests

**Notes:**
- `determine_explicitly_provided_dependencies` takes `ExplicitlyProvidedDependenciesRequest`, NOT `DependenciesRequest`
- `resolve_targets` uses `**implicitly({Addresses(owners): Addresses})` syntax
- `concurrently()` can take generator expressions for dynamic collections

**Validation:**
```bash
pants check pants-plugins/pants_backend_clojure/namespace_analysis.py
pants check pants-plugins/pants_backend_clojure/dependency_inference.py
pants check pants-plugins/pants_backend_clojure/provided_dependencies.py
pants dependencies example/src/example/core.clj  # Test inference
```

---

### Phase 3: Compilation Rules - **DONE**

**Goal:** Migrate compilation-related rules.

**Files:**
1. `compile_clj.py` (4 Gets)
2. `tools_build_uberjar.py` (10 Gets, 1 MultiGet)

**Tasks:**
- [x] Migrate `compile_clj.py`
- [x] Migrate `tools_build_uberjar.py`
- [x] Test compilation workflow

**Notes:**
- When passing variables (not direct expressions) to `implicitly()`, use the dict syntax: `**implicitly({jdk_request: JdkRequest})`
- `strip_source_roots(source_files)` takes SourceFiles directly without type annotation
- `jvm_process(**implicitly({process: JvmProcess}))` for JVM process conversion

**Validation:**
```bash
pants check pants-plugins/pants_backend_clojure/compile_clj.py
pants check pants-plugins/pants_backend_clojure/tools_build_uberjar.py
pants check example/::  # Full check
```

---

### Phase 4: Goal Rules (Part 1 - Simpler Goals) - **DONE**

**Goal:** Migrate simpler goal implementations.

**Files:**
1. `goals/lint.py` (5 Gets)
2. `goals/check.py` (9 Gets, 2 MultiGets)
3. `goals/generate_clojure_lockfile_metadata.py` (4 Gets, 2 MultiGets)
4. `goals/generate_deps.py` (6 Gets, 2 MultiGets)

**Tasks:**
- [x] Migrate `goals/lint.py`
- [x] Migrate `goals/check.py`
- [x] Migrate `goals/generate_clojure_lockfile_metadata.py`
- [x] Migrate `goals/generate_deps.py`
- [x] Run each goal to verify

**Validation:**
```bash
pants lint example/::
pants check example/::
pants generate-clojure-lockfile-metadata ::
pants generate-deps-edn
```

---

### Phase 5: Goal Rules (Part 2 - Complex Goals) - **DONE**

**Goal:** Migrate the most complex goal implementations.

**Files:**
1. `goals/test.py` (10 Gets, 1 MultiGet)
2. `goals/repl.py` (12 Gets, 3 MultiGets)
3. `goals/package.py` (18 Gets, 2 MultiGets)

**Tasks:**
- [x] Migrate `goals/test.py`
- [x] Migrate `goals/repl.py`
- [x] Migrate `goals/package.py`
- [x] Run comprehensive tests

**Notes:**
- `resolve_unparsed_address_inputs` returns `Addresses`, not `Targets`. Need two steps: first resolve addresses, then resolve targets.
- Renamed local variables to avoid conflicts with imported functions (e.g., `transitive_targets` → `trans_targets`)

**Validation:**
```bash
pants test example/::
pants repl example/src/example:lib
pants package example/::
```

---

### Phase 6: Cleanup & Documentation - **DONE**

**Goal:** Final cleanup and documentation updates.

**Tasks:**
- [x] Remove any unused imports from migrated files
- [x] Update any documentation referencing old patterns
- [x] Run full test suite
- [x] Update CHANGELOG.md

**Validation:**
```bash
pants check ::
pants lint ::
pants test ::
pants package ::
```

---

## Risk Mitigation

### Testing Strategy
1. **Unit tests** - Run existing tests after each file migration
2. **Integration tests** - Test goals end-to-end after each phase
3. **Rollback plan** - Git commits after each successful phase allow easy rollback

### Common Pitfalls to Avoid

1. **Forgetting `**implicitly()`** - Many rule calls require implicit context for subsystems and environment
2. **Wrong type in implicitly dict** - Use `**implicitly({value: InputType})` when the input type differs from what the rule expects
3. **Missing imports** - Each intrinsic/rule function needs explicit import from its module
4. **ProcessResult vs FallibleProcessResult** - `execute_process` always returns `FallibleProcessResult`; if the old code expected `ProcessResult` (guaranteed success), you need to check `.exit_code == 0` or raise on failure
5. **get_digest_contents takes Digest directly** - No `implicitly()` needed: `await get_digest_contents(digest)`
6. **classpath requires type hint in implicitly** - Use `await classpath(**implicitly({addresses: Addresses}))` not just `**implicitly(addresses)`

### Verification Commands

After each phase, run:
```bash
# Type checking (if configured)
pants check pants-plugins/::

# Run plugin tests
pants test pants-plugins/::

# Integration test with example project
pants check example/::
pants test example/::
pants package example/::

# Check for deprecation warnings (critical!)
pants --no-pantsd check example/:: 2>&1 | grep -i deprecat
```

---

## Reference: Import Cheat Sheet

```python
# Intrinsics (file system and process operations)
from pants.engine.intrinsics import (
    add_prefix,
    create_digest,
    digest_to_snapshot,
    execute_process,
    execute_process_with_retry,
    get_digest_contents,
    merge_digests,
    path_globs_to_digest,
    remove_prefix,
)

# Rules API
from pants.engine.rules import concurrently, implicitly, rule

# Source files
from pants.core.util_rules.source_files import (
    SourceFiles,
    SourceFilesRequest,
    determine_source_files,
)
from pants.core.util_rules.stripped_source_files import (
    StrippedSourceFiles,
    strip_source_roots,
)

# External tools and config
from pants.core.util_rules.external_tool import (
    DownloadedExternalTool,
    ExternalToolRequest,
    download_external_tool,
)
from pants.core.util_rules.config_files import (
    ConfigFiles,
    ConfigFilesRequest,
    find_config_file,
)

# JVM operations
from pants.jvm.jdk_rules import (
    JdkEnvironment,
    JdkRequest,
    JvmProcess,
    jvm_process,
    prepare_jdk_environment,
)
from pants.jvm.classpath import Classpath, classpath
from pants.jvm.resolve.coursier_fetch import (
    ClasspathEntry,
    CoursierLockfileEntry,
    ToolClasspath,
    ToolClasspathRequest,
    coursier_fetch_one_coord,
    materialize_classpath_for_tool,
)

# Target resolution
from pants.engine.internals.graph import (
    find_all_targets,
    find_owners,
    resolve_targets,
    resolve_unexpanded_targets,
    resolve_unparsed_address_inputs,
    transitive_targets,
)

# Environment
from pants.core.util_rules.env_vars import environment_vars_subset
```

---

## Success Criteria

1. All `Get()` and `MultiGet()` calls removed from codebase
2. No deprecation warnings when running pants commands
3. All existing tests pass
4. All goals function correctly:
   - `pants check`
   - `pants fmt`
   - `pants lint`
   - `pants test`
   - `pants repl`
   - `pants package`
   - `pants generate-deps-edn`
   - `pants generate-clojure-lockfile-metadata`

---

## Appendix: Full Migration Mapping

See the detailed analysis above for the complete list of Get/MultiGet calls in each file and their corresponding call-by-name replacements.
