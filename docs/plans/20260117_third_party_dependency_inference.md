# Third-Party Dependency Inference for Clojure

**Date:** 2026-01-17
**Status:** Draft
**Author:** Claude

## Overview

This plan outlines improvements to the Clojure backend's third-party dependency inference capabilities. The key insight is that **JAR files contain the ground truth** about what namespaces they provide - we should analyze them directly rather than relying on heuristics or manual mappings.

## Current State

The Clojure backend has foundational third-party dependency inference:

1. **Manual Metadata Generation** (`generate-clojure-lockfile-metadata` goal)
   - Analyzes JARs in lockfiles for Clojure namespaces
   - Generates `*_clojure_namespaces.json` metadata files
   - Users must run this manually after lockfile changes

2. **Metadata Loading** (`ClojureNamespaceMapping`)
   - Globs for metadata files
   - Provides `addresses_for_namespace(namespace, resolve)` lookup

3. **Inference Integration** (`dependency_inference.py`)
   - First-party sources checked first via `OwnersRequest`
   - Falls back to `ClojureNamespaceMapping` for third-party

## Problem Statement

The current approach has significant UX issues:

1. **Manual step required**: Users must remember to run `generate-clojure-lockfile-metadata` after every lockfile change
2. **Stale metadata**: Easy to have metadata that doesn't match the lockfile
3. **No automatic discovery**: Unlike the existing JAR analysis, the inference doesn't "just work"

Additionally, the Java/Scala approach of inferring packages from Maven coordinates has limitations:
- Can't infer `cheshire.core` from `cheshire:cheshire`
- Can't infer `next.jdbc` from `com.github.seancorfield:next.jdbc`
- A single JAR can contain namespaces from multiple root packages

## Proposed Solution: Automatic JAR Analysis

**Core insight**: Since Pants already downloads and caches JARs via Coursier, we can analyze them on-demand during dependency inference. Pants' rule caching ensures this only happens once per lockfile digest.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Dependency Inference                      │
│                                                              │
│  1. Parse source file → extract :require namespaces          │
│  2. For each namespace:                                      │
│     a. Check first-party sources (OwnersRequest)             │
│     b. Check ClojureNamespaceMapping (third-party)           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              ClojureNamespaceMapping (NEW)                   │
│                                                              │
│  Built lazily from:                                          │
│  1. Manual `packages` field on jvm_artifact (highest prio)   │
│  2. JAR analysis from lockfile (automatic!)                  │
│  3. Default mappings for common libraries (fallback)         │
│                                                              │
│  Cached by: lockfile digest + jvm_artifact target hashes     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    JAR Analysis                              │
│                                                              │
│  For each entry in lockfile:                                 │
│  1. Fetch JAR via coursier_fetch_one_coord (cached)          │
│  2. Analyze for Clojure namespaces:                          │
│     - Parse .clj/.cljc/.clje files for (ns ...) declarations │
│     - Fall back to .class file analysis for AOT JARs         │
│  3. Map namespace → jvm_artifact address                     │
└─────────────────────────────────────────────────────────────┘
```

### Benefits

1. **Zero configuration**: Works automatically after `pants generate-lockfiles`
2. **Always accurate**: Analyzes actual JAR contents, not heuristics
3. **Cached efficiently**: Pants' rule system handles caching by lockfile digest
4. **Backwards compatible**: Existing metadata files still work as override

---

## Implementation Phases

### Phase 1: Create ClojureInferSubsystem [DONE]

**Goal:** Provide configuration infrastructure for dependency inference.

**Changes:**

1. **Create `ClojureInferSubsystem`** in `subsystems/clojure_infer.py`:
   ```python
   from pants.option.option_types import BoolOption, DictOption
   from pants.option.subsystem import Subsystem

   class ClojureInferSubsystem(Subsystem):
       options_scope = "clojure-infer"
       help = "Options controlling Clojure dependency inference."

       namespaces = BoolOption(
           default=True,
           help="Infer dependencies from :require forms in Clojure source files."
       )

       java_imports = BoolOption(
           default=True,
           help="Infer dependencies from :import forms in Clojure source files."
       )

       third_party_namespace_mapping = DictOption[str](
           default={},
           help="""
           A mapping of Clojure namespace patterns to Maven coordinates.

           For example: {"my.custom.lib.**": "com.example:my-lib"}

           The namespace pattern may be made recursive by adding `.**` to the end.
           This is useful when JAR analysis cannot detect namespaces (e.g., AOT-only JARs
           with non-standard class naming).
           """
       )
   ```

2. **Register subsystem** in `register.py`

**Files to create/modify:**
- `pants-plugins/pants_backend_clojure/subsystems/clojure_infer.py` (new)
- `pants-plugins/pants_backend_clojure/register.py`

**Testing:**
- Test that subsystem options are parsed correctly
- Test default values

---

### Phase 2: Implement Automatic JAR Analysis [DONE]

**Goal:** Build `ClojureNamespaceMapping` automatically by analyzing JARs from the lockfile.

#### Improved AOT Detection

The current `jar_analyzer.py` has incorrect logic for AOT-compiled JARs. The fix:

**Current (wrong):**
```python
# Skips __init files, but those ARE the namespace loaders!
if '__' in path or '$' in path:
    return None
```

**Correct approach:**
```python
def namespace_from_class_path(class_path: str) -> str | None:
    """Extract Clojure namespace from __init.class files.

    Clojure AOT compilation generates these classes per namespace:
    - my/app/core__init.class    <- Namespace loader (WE WANT THIS)
    - my/app/core$main.class     <- Named function
    - my/app/core$fn__1234.class <- Anonymous function

    The __init.class suffix definitively identifies a Clojure namespace.

    LIMITATION: Both `my-app.core` and `my_app.core` compile to `my_app/core__init.class`.
    We use the demunge heuristic (underscore → hyphen) which works for idiomatic code,
    but may be wrong for namespaces that intentionally use underscores.
    Users can override via the `packages` field if needed.
    """
    if not class_path.endswith('__init.class'):
        return None

    # Remove __init.class suffix
    path = class_path[:-12]  # len('__init.class') == 12

    # Convert path to namespace: my/app/core -> my.app.core
    # Apply demunge heuristic: underscores -> hyphens (convention, not guaranteed)
    namespace = path.replace('/', '.').replace('_', '-')

    return namespace
```

**Why this is mostly reliable:**
1. `__init.class` is ALWAYS generated for every AOT-compiled namespace
2. The naming is deterministic - Clojure compiler guarantees this pattern
3. No false positives - regular Java classes never have this suffix
4. The underscore → hyphen heuristic works for ~99% of idiomatic Clojure code

**Known limitation - underscore ambiguity:**

Both `(ns my-app.core)` and `(ns my_app.core)` are valid Clojure and compile to
the same class file: `my_app/core__init.class`. The `demunge` operation is not
reversible - we cannot know which was the original.

**Mitigation:** Always prefer source file analysis when `.clj` files are available.
For AOT-only JARs, use the heuristic and allow manual override via `packages` field.

**Optional: javap validation for edge cases:**
```python
async def validate_clojure_class_with_javap(
    class_digest: Digest,
    class_name: str,
) -> bool:
    """Use javap to confirm a class is Clojure-compiled.

    Checks for:
    - References to clojure/lang/RT or clojure/lang/Var in constant pool
    - Static fields named const__N of type clojure.lang.Var
    """
    result = await Get(
        ProcessResult,
        Process(
            argv=["javap", "-v", class_name],
            input_digest=class_digest,
            description=f"Validating {class_name} is Clojure-compiled",
        )
    )
    output = result.stdout.decode()
    return "clojure/lang" in output
```

**Changes:**

1. **Create `ThirdPartyClojureNamespaceMapping`** request/response types:
   ```python
   @dataclass(frozen=True)
   class ThirdPartyClojureNamespaceMappingRequest:
       """Request to build namespace mapping for a resolve by analyzing JARs."""
       resolve_name: str

   @dataclass(frozen=True)
   class ThirdPartyClojureNamespaceMapping:
       """Mapping of Clojure namespaces to jvm_artifact addresses for a resolve."""
       # namespace -> addresses (multiple if ambiguous)
       mapping: FrozenDict[str, tuple[Address, ...]]
   ```

2. **Create rule to analyze JARs from lockfile**:
   ```python
   @rule(desc="Analyzing JARs for Clojure namespaces", level=LogLevel.DEBUG)
   async def build_third_party_clojure_namespace_mapping(
       request: ThirdPartyClojureNamespaceMappingRequest,
       jvm: JvmSubsystem,
   ) -> ThirdPartyClojureNamespaceMapping:
       # 1. Load lockfile for this resolve
       lockfile_path = jvm.resolves.get(request.resolve_name)
       if not lockfile_path:
           return ThirdPartyClojureNamespaceMapping(FrozenDict())

       lockfile_digest = await path_globs_to_digest(PathGlobs([lockfile_path]))
       lockfile_contents = await get_digest_contents(lockfile_digest)
       lockfile = CoursierResolvedLockfile.from_serialized(lockfile_contents[0].content)

       # 2. Fetch all JARs (uses Coursier cache)
       classpath_entries = await concurrently(
           coursier_fetch_one_coord(entry) for entry in lockfile.entries
       )

       # 3. Analyze each JAR for Clojure namespaces
       mapping: dict[str, list[Address]] = {}
       for entry, cp_entry in zip(lockfile.entries, classpath_entries):
           namespaces = await _analyze_jar_for_namespaces(cp_entry.digest)
           address = Address.parse(entry.pants_address) if entry.pants_address else None
           if address:
               for ns in namespaces:
                   mapping.setdefault(ns, []).append(address)

       return ThirdPartyClojureNamespaceMapping(
           FrozenDict({ns: tuple(addrs) for ns, addrs in mapping.items()})
       )
   ```

3. **Refactor JAR analysis to work with Digest**:
   ```python
   async def _analyze_jar_for_namespaces(jar_digest: Digest) -> tuple[str, ...]:
       """Analyze a JAR digest for Clojure namespaces.

       This is a pure function over the digest, so Pants will cache it.
       """
       # Use Process to run analysis in sandbox, or
       # materialize and use existing analyze_jar_for_namespaces()
   ```

4. **Update `ClojureNamespaceMapping` to use automatic analysis**:
   ```python
   @rule
   async def build_clojure_namespace_mapping(
       clojure_infer: ClojureInferSubsystem,
       jvm: JvmSubsystem,
       all_jvm_artifact_tgts: AllJvmArtifactTargets,
   ) -> ClojureNamespaceMapping:
       # Build mapping for each resolve
       resolve_names = set(jvm.resolves.keys())

       # Get automatic JAR analysis for each resolve
       third_party_mappings = await concurrently(
           build_third_party_clojure_namespace_mapping(
               ThirdPartyClojureNamespaceMappingRequest(resolve)
           )
           for resolve in resolve_names
       )

       # Merge with manual `packages` field overrides (highest priority)
       # Merge with subsystem custom mappings
       # ...
   ```

**Files to modify:**
- `pants-plugins/pants_backend_clojure/clojure_symbol_mapping.py`
- `pants-plugins/pants_backend_clojure/utils/jar_analyzer.py` (refactor for Digest)
- `pants-plugins/pants_backend_clojure/register.py`

**Testing:**
- Test JAR analysis discovers namespaces from .clj files
- Test JAR analysis discovers namespaces from AOT .class files
- Test mapping is cached by lockfile digest
- Test that changes to lockfile trigger re-analysis

---

### Phase 3: Support Manual Overrides via `packages` Field [DONE]

**Goal:** Allow users to manually specify or override namespaces using the existing `packages` field on `jvm_artifact`.

**Rationale:** Some JARs may have issues with automatic analysis (AOT-only with unusual class names, shaded JARs, etc.). Users need an escape hatch.

**Changes:**

1. **Read `packages` field from jvm_artifact targets**:
   ```python
   @dataclass(frozen=True)
   class AvailableClojureArtifactPackages:
       """Manual namespace overrides from jvm_artifact targets."""
       # Maps (resolve, coordinate) -> (addresses, packages)
       mapping: FrozenDict[tuple[str, UnversionedCoordinate], tuple[tuple[Address, ...], tuple[str, ...]]]

   @rule
   async def find_clojure_artifact_packages(
       all_jvm_artifact_tgts: AllJvmArtifactTargets,
       jvm: JvmSubsystem,
   ) -> AvailableClojureArtifactPackages:
       """Extract packages field from jvm_artifact targets for Clojure namespace inference."""
       mapping = {}
       for tgt in all_jvm_artifact_tgts:
           packages = tgt[JvmArtifactPackagesField].value
           if packages:  # Only include if explicitly set
               coord = UnversionedCoordinate(
                   group=tgt[JvmArtifactGroupField].value,
                   artifact=tgt[JvmArtifactArtifactField].value,
               )
               resolve = tgt[JvmResolveField].normalized_value(jvm)
               key = (resolve, coord)
               # ... build mapping
       return AvailableClojureArtifactPackages(FrozenDict(mapping))
   ```

2. **Update mapping builder with precedence**:
   ```python
   Resolution order (highest to lowest precedence):
   1. Manual `packages` field on jvm_artifact
   2. Automatic JAR analysis
   3. Subsystem `third_party_namespace_mapping` option
   ```

**Files to modify:**
- `pants-plugins/pants_backend_clojure/clojure_symbol_mapping.py`

**Documentation:**
- Document that `packages=["my.namespace.**"]` can be used for Clojure namespaces
- Document when manual overrides are needed

**Testing:**
- Test that `packages` field overrides automatic analysis
- Test pattern matching with `.**` suffix

---

### Phase 4: Implement Trie-Based Pattern Matching [DONE]

**Goal:** Efficiently support wildcard patterns like `ring.middleware.**`.

**Changes:**

1. **Use trie structure from upstream Pants**:
   ```python
   from pants.jvm.dependency_inference.artifact_mapper import (
       MutableTrieNode,
       FrozenTrieNode,
   )
   ```

2. **Update ClojureNamespaceMapping to use trie**:
   ```python
   @dataclass(frozen=True)
   class ClojureNamespaceMapping:
       """Mapping from Clojure namespaces to jvm_artifact addresses using trie."""
       mapping_per_resolve: FrozenDict[str, FrozenTrieNode]

       def addresses_for_namespace(
           self, namespace: str, resolve: str
       ) -> tuple[Address, ...]:
           trie = self.mapping_per_resolve.get(resolve)
           if not trie:
               return ()
           matches = trie.addresses_for_symbol(namespace)
           return tuple(addr for addrs in matches.values() for addr in addrs)
   ```

3. **Support `.**` patterns in all sources**:
   - Subsystem `third_party_namespace_mapping`
   - Manual `packages` field
   - (Automatic JAR analysis returns exact namespaces, no patterns needed)

**Files to modify:**
- `pants-plugins/pants_backend_clojure/clojure_symbol_mapping.py`

**Testing:**
- Test exact namespace matching
- Test recursive pattern matching (`ns.**`)
- Test pattern precedence (specific over general)

---

### Phase 5: Deprecate Manual Metadata Generation

**Goal:** Keep `generate-clojure-lockfile-metadata` for backwards compatibility but mark as deprecated.

**Changes:**

1. **Add deprecation warning to goal**:
   ```python
   @goal_rule
   async def generate_clojure_lockfile_metadata(
       console: Console,
       # ...
   ) -> GenerateClojureLockfileMetadata:
       console.print_stderr(
           "WARNING: generate-clojure-lockfile-metadata is deprecated. "
           "Clojure namespace inference now analyzes JARs automatically. "
           "This goal will be removed in a future version."
       )
       # ... existing logic for users who want explicit metadata files
   ```

2. **Update documentation** to reflect automatic behavior

3. **Keep metadata file support** for edge cases where users want to:
   - Check in metadata to avoid JAR analysis overhead in CI
   - Debug namespace discovery issues

**Files to modify:**
- `pants-plugins/pants_backend_clojure/goals/generate_clojure_lockfile_metadata.py`

---

## Implementation Order

| Phase | Description | Dependencies |
|-------|-------------|--------------|
| 1 | Create ClojureInferSubsystem | None |
| 2 | Implement automatic JAR analysis | Phase 1 |
| 3 | Support manual `packages` overrides | Phase 2 |
| 4 | Implement trie-based pattern matching | Phase 3 |
| 5 | Deprecate manual metadata generation | Phase 2 |

Phases 3-5 can be done in parallel after Phase 2.

---

## Testing Strategy

Each phase includes:
1. **Unit tests** for new classes/functions
2. **Integration tests** with sample JARs

Test files (in `pants-plugins/tests/`):
- `test_clojure_infer_subsystem.py` (new - Phase 1)
- `test_third_party_namespace_mapping.py` (new - Phase 2)
- `test_clojure_namespace_mapping.py` (extend - Phases 3, 4)
- `test_dependency_inference.py` (extend existing)

Key test scenarios:
- JAR with .clj source files → parse `(ns ...)` declarations
- AOT-compiled JAR with `__init.class` files → extract namespace from path
- Mixed JAR with both source and AOT → prefer source parsing
- Shaded/uber JAR with relocated classes → may need manual `packages` override
- JAR with namespaces from multiple root packages (e.g., `com.foo` and `org.bar`)
- JAR with hyphenated namespaces → `my-app.core` compiles to `my_app/core__init.class`
- Large lockfile performance test (100+ JARs)

---

## Performance Considerations

**JAR Analysis Cost:**
- First run: O(n) where n = number of JARs in lockfile
- Subsequent runs: O(1) - cached by Pants rule system

**Mitigation strategies:**
1. Pants caches rule results by input digests
2. JARs are already cached by Coursier
3. Only analyze JARs that haven't been seen before

**Benchmarks to run:**
- Lockfile with 100 JARs
- Lockfile with 500 JARs (large project)
- Incremental: add 1 new dependency to existing lockfile

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Slow first-run for large lockfiles | Medium | Analyze in parallel; show progress |
| Trie import not public API | High | Implement own trie if needed; it's ~100 lines |
| AOT underscore/hyphen ambiguity | Low | Prefer source analysis; heuristic works for idiomatic code; manual `packages` override available |
| AOT JAR analysis false positives | Low | Only look for `__init.class` suffix (no false positives) |
| Memory usage for large mappings | Low | Frozen/immutable data structures |

---

## Migration Path

1. **Existing users with metadata files**: Files continue to work; automatic analysis supplements them
2. **New users**: Everything works automatically after `generate-lockfiles`
3. **CI optimization**: Can still generate metadata files to avoid analysis overhead

---

## Success Criteria

1. `pants check ::` works immediately after `pants generate-lockfiles` - no manual metadata step
2. Common Clojure libraries (Ring, Compojure, etc.) resolve correctly via JAR analysis
3. Users can override with `packages` field when automatic analysis fails
4. Performance is acceptable (< 30s for 100-JAR lockfile on first run)
5. Subsystem options allow disabling/customizing behavior
