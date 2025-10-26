# Third-Party Clojure Dependency Inference Plan

## Executive Summary

Enable automatic inference of dependencies on third-party Clojure libraries packaged as JARs. Currently, users must manually specify `jvm_artifact` dependencies in BUILD files when Clojure code requires third-party namespaces. This plan proposes inspecting JAR contents to build a namespace�artifact mapping, enabling automatic dependency inference similar to how Java class imports are handled.

## Current State

###  What Works Today

1. **First-party Clojure namespace inference**
   - Automatically infers dependencies on first-party Clojure code via `:require`/`:use`
   - Example: `(:require [myproject.util :as util])` � automatically finds the target owning `myproject/util.clj`
   - Implementation: Uses `OwnersRequest` to find file owners

2. **Java class import inference (first-party AND third-party)**
   - Automatically infers dependencies on Java classes via `:import`
   - Works for both first-party Java sources and third-party JARs
   - Example: `(:import (com.google.common.collect ImmutableList))` � automatically finds Guava artifact
   - Implementation: Uses Pants' `SymbolMapping` infrastructure

### L What Doesn't Work

**Third-party Clojure namespace inference** - When Clojure code requires a namespace from a third-party JAR:

```clojure
(ns myproject.core
  (:require [clojure.data.json :as json]      ; L NOT inferred
            [clojure.tools.logging :as log])) ; L NOT inferred
```

Users must manually specify:
```python
clojure_source(
    name="core",
    source="core.clj",
    dependencies=[
        "3rdparty/jvm:data-json",     # L Manual specification required
        "3rdparty/jvm:tools-logging", # L Manual specification required
    ],
)
```

### Why It Doesn't Work

Looking at `dependency_inference.py:131-173`:

```python
for namespace in required_namespaces:
    file_path = namespace_to_path(namespace)  # e.g., "clojure/data/json.clj"
    owners = await Get(Owners, OwnersRequest((path,)))  # L Only finds file owners, not JARs
```

The current implementation:
1. Converts namespace to file path: `clojure.data.json` � `clojure/data/json.clj`
2. Uses `OwnersRequest` to find targets that **own that source file**
3. Only matches first-party source files, never JAR artifacts

In contrast, Java class imports use `SymbolMapping.addresses_for_symbol()` which **can** resolve to `jvm_artifact` targets, but Pants' SymbolMapping only understands Java packages, not Clojure namespaces.

## Problem Statement

### User Pain Points

1. **Manual dependency management is tedious**
   - Every third-party namespace requires explicit BUILD file entry
   - Easy to forget dependencies (leads to runtime errors)
   - Slows down development velocity

2. **Maintenance burden**
   - When code changes require new libraries, must update BUILD files
   - Refactoring is more painful
   - Inconsistent with how Java imports work (those are automatic)

3. **Poor discoverability**
   - Users coming from `deps.edn` expect automatic resolution
   - Not obvious why some dependencies are automatic (first-party, Java) but others aren't (third-party Clojure)

### Goals

1. **Automatic inference**: `(:require [clojure.data.json :as json])` should automatically infer the `org.clojure:data.json` artifact dependency
2. **Zero manual work**: No need to specify `dependencies` for third-party Clojure namespaces
3. **Accurate resolution**: Correctly map namespaces to the JARs that provide them
4. **Handle ambiguity**: When multiple JARs provide the same namespace, warn and allow disambiguation
5. **Extensibility**: Design to support other JVM languages that might need similar namespace introspection (e.g., Kotlin, Scala objects)

## Design Approaches

### Approach 1: Static Hardcoded Mapping

Maintain a hardcoded map of common Clojure namespaces to Maven coordinates.

```python
# In clojure_backend/third_party_namespaces.py
NAMESPACE_TO_ARTIFACT = {
    "clojure.data.json": ("org.clojure", "data.json"),
    "clojure.tools.logging": ("org.clojure", "tools.logging"),
    "clojure.tools.cli": ("org.clojure", "tools.cli"),
    # ... 100+ entries
}
```

**Pros:**
- Simple to implement
- Zero performance overhead
- Deterministic

**Cons:**
- L Requires manual curation
- L Only works for curated libraries
- L Doesn't scale to private/internal libraries
- L Doesn't handle non-standard namespace�artifact mappings
- L Maintenance burden to keep up-to-date
- L Fails silently for unknown libraries

**Verdict:** L Not recommended as primary solution. Could be useful as a fallback.

### Approach 2: Manual Field on jvm_artifact (Like Python's module_mapping)

Add a `clojure_namespaces` field to `jvm_artifact`, similar to Python's `module_mapping`.

```python
jvm_artifact(
    name="data-json",
    group="org.clojure",
    artifact="data.json",
    version="2.4.0",
    clojure_namespaces=["clojure.data.json"],  # � Manual specification
)
```

Then build a mapping from these declarations and use it during inference.

**Pros:**
- Simple to implement
- User has full control
- Works for any library (including private ones)
- No performance overhead
- Explicit and auditable

**Cons:**
- L Manual work required for every artifact
- L Error-prone (users can specify wrong namespaces)
- L Doesn't scale well
- L Inconsistent with automatic Java class inference
- � Better than nothing, but not automatic

**Verdict:** � Good as a **fallback/override mechanism**, but not sufficient alone.

### Approach 3: JAR Introspection at Build Time

During dependency inference, inspect JAR contents on-demand to discover namespaces.

```python
@rule
async def infer_clojure_namespace_from_jars(
    namespace: str,
    resolve: str,
    all_artifacts: AllJvmArtifactTargets,
) -> Address | None:
    # For each jvm_artifact:
    #   - Download JAR
    #   - List .clj/.cljc files
    #   - Parse namespace declarations
    #   - Check if matches required namespace
    #   - Return address if found
```

**Pros:**
- Fully automatic
- Always accurate
- Works for any JAR
- No manual configuration

**Cons:**
- L **Performance overhead**: Analyzing JARs during every build
- L **I/O heavy**: Downloading and reading JARs repeatedly
- L **Slow dependency inference**: Blocks on JAR analysis
- L **Complexity**: Parsing logic in hot path
- � Caching would be required (but still expensive)

**Verdict:** L Too slow for build-time execution. Better to do this ahead of time.

### Approach 4: JAR Introspection During Lock File Generation (RECOMMENDED)

**Inspect JARs once during lock file generation and store the results in a metadata file alongside the lockfile.**

During `pants generate-lockfiles`:
1. Resolve dependencies (standard behavior)
2. Download all resolved JARs
3. Introspect each JAR to extract Clojure namespaces
4. Write namespace�artifact mapping to metadata file: `<resolve>_clojure_namespaces.json`

During dependency inference:
1. Load the metadata file (fast, cached)
2. Look up required namespace in the mapping
3. Return the `jvm_artifact` address

**File structure:**
```
3rdparty/jvm/
  default.lock                           # Standard Pants lockfile
  default_clojure_namespaces.json        # � New: namespace mapping
  java17.lock
  java17_clojure_namespaces.json
```

**Example metadata file:**
```json
{
  "org.clojure:data.json:2.4.0": {
    "address": "3rdparty/jvm:data-json",
    "namespaces": ["clojure.data.json"]
  },
  "org.clojure:tools.logging:1.2.4": {
    "address": "3rdparty/jvm:tools-logging",
    "namespaces": ["clojure.tools.logging"]
  },
  "org.clojure:core.async:1.6.681": {
    "address": "3rdparty/jvm:core-async",
    "namespaces": [
      "clojure.core.async",
      "clojure.core.async.impl.protocols",
      "clojure.core.async.impl.channels",
      "clojure.core.async.impl.buffers"
    ]
  }
}
```

**Pros:**
-  **Fully automatic**: No manual configuration needed
-  **Fast at build time**: Metadata is pre-computed and cached
-  **Always accurate**: Based on actual JAR contents
-  **Works for any library**: Including private/internal ones
-  **Scales well**: One-time cost during lock file generation
-  **Explicit**: Metadata file is auditable and can be version-controlled
-  **Overrideable**: Users can manually edit metadata if needed
-  **Consistent with Pants patterns**: Similar to how Python lockfiles work

**Cons:**
- � Slightly more complex implementation
- � Requires extending lock file generation
- � Metadata file must stay in sync with lockfile (could get stale)
- � Initial lock file generation takes longer (but only once)

**Mitigations:**
- Validate metadata freshness (compare lockfile hash)
- Auto-regenerate metadata if stale
- Cache JAR analysis results

**Verdict:**  **RECOMMENDED** - Best balance of automation, performance, and accuracy.

### Approach 5: Extend Pants' SymbolMapping Infrastructure

Extend Pants' existing `SymbolMapping` (currently Java-only) to support Clojure namespaces.

Modify Pants core to:
1. Analyze JARs for both Java packages AND Clojure namespaces
2. Store both in a unified symbol table
3. Query both when inferring dependencies

**Pros:**
- Unified infrastructure for all JVM languages
- Consistent API
- Centralized caching

**Cons:**
- L Requires changes to Pants core (not just plugin)
- L More complex (affects all JVM backends)
- L Slower to implement
- L May not be accepted upstream
- � Overkill for Clojure-specific needs

**Verdict:** � **Future optimization**, but not for initial implementation. Stick with plugin-level solution first.

## Recommended Solution: Approach 4 + Approach 2

**Primary mechanism:** JAR introspection during lock file generation (Approach 4)
**Override mechanism:** Manual `clojure_namespaces` field on `jvm_artifact` (Approach 2)

This combination provides:
- Automatic inference for 99% of cases
- Manual override for edge cases
- Good performance (pre-computed metadata)
- Extensibility for future enhancements

## Implementation Plan

### Phase 1: Namespace Metadata Generation (Lock File Extension)

**Goal:** Generate namespace mapping metadata during `pants generate-lockfiles`

**Tasks:**

1. **Create JAR analysis utility** (`jar_analyzer.py`)
   ```python
   @dataclass(frozen=True)
   class JarNamespaceAnalysis:
       """Result of analyzing a JAR for Clojure namespaces."""
       coordinate: str  # "org.clojure:data.json:2.4.0"
       namespaces: tuple[str, ...]  # ("clojure.data.json",)

   async def analyze_jar_for_namespaces(jar_path: Path) -> JarNamespaceAnalysis:
       """Extract Clojure namespaces from a JAR file."""
       # 1. List all .clj, .cljc, .clje files in JAR
       # 2. For each file, extract namespace declaration
       # 3. Return set of namespaces
   ```

2. **Implement namespace extraction logic**
   - Use `zipfile` to read JAR contents
   - List files matching `**/*.{clj,cljc,clje}`
   - For each Clojure file:
     - Read content
     - Use existing `parse_namespace()` function (from `namespace_parser.py`)
     - Extract namespace name
   - Return deduplicated list of namespaces

3. **Integrate with lock file generation**
   - Hook into `pants generate-lockfiles` goal
   - After resolving dependencies, trigger JAR analysis
   - For each resolved JAR in the lockfile:
     - Download JAR (use existing Coursier integration)
     - Run `analyze_jar_for_namespaces()`
     - Collect results

4. **Write metadata file**
   - Format: `<resolve>_clojure_namespaces.json`
   - Schema:
     ```json
     {
       "version": "1.0",
       "generated_at": "2025-10-26T12:34:56Z",
       "lockfile_hash": "sha256:abc123...",
       "artifacts": {
         "org.clojure:data.json:2.4.0": {
           "address": "3rdparty/jvm:data-json",
           "namespaces": ["clojure.data.json"]
         }
       }
     }
     ```
   - Include metadata for validation:
     - Version (for schema evolution)
     - Generation timestamp
     - Lockfile hash (to detect staleness)

**Deliverables:**
- `jar_analyzer.py` - JAR introspection utility
- Extension to `generate-lockfiles` goal
- Metadata file writer
- Tests for JAR analysis

**Estimated effort:** 8-12 hours

### Phase 2: Clojure Symbol Mapping Data Structure

**Goal:** Create an in-memory mapping structure for namespace�artifact lookups

**Tasks:**

1. **Define data structure** (`clojure_symbol_mapping.py`)
   ```python
   @dataclass(frozen=True)
   class ClojureNamespaceMapping:
       """Mapping from Clojure namespaces to jvm_artifact addresses.

       Similar to Pants' SymbolMapping but for Clojure namespaces instead of Java classes.
       """
       # Maps (namespace, resolve) -> tuple of addresses (handles ambiguity)
       mapping: FrozenDict[tuple[str, str], tuple[Address, ...]]

       def address_for_namespace(
           self, namespace: str, resolve: str
       ) -> tuple[Address, ...]:
           """Look up which jvm_artifact(s) provide a given namespace."""
           return self.mapping.get((namespace, resolve), ())
   ```

2. **Create rule to build mapping from metadata file**
   ```python
   @rule
   async def load_clojure_namespace_mapping(
       jvm: JvmSubsystem,
   ) -> ClojureNamespaceMapping:
       """Load namespace�artifact mapping from metadata files."""
       # 1. Find all *_clojure_namespaces.json files
       # 2. Parse JSON
       # 3. Build in-memory mapping
       # 4. Return ClojureNamespaceMapping
   ```

3. **Handle metadata freshness**
   - Check if metadata file exists
   - Validate `lockfile_hash` matches current lockfile
   - If stale, warn user to regenerate: `pants generate-lockfiles`

4. **Handle ambiguity**
   - If multiple artifacts provide the same namespace, store all addresses
   - Return `tuple[Address, ...]` to represent multiple candidates
   - Let disambiguation logic (existing infrastructure) handle it

**Deliverables:**
- `ClojureNamespaceMapping` data structure
- Rule to load metadata and build mapping
- Staleness detection logic
- Tests for mapping construction

**Estimated effort:** 4-6 hours

### Phase 3: Integrate with Dependency Inference

**Goal:** Use `ClojureNamespaceMapping` during dependency inference

**Tasks:**

1. **Modify `dependency_inference.py`**

   Current code (lines 131-173):
   ```python
   for namespace in required_namespaces:
       file_path = namespace_to_path(namespace)
       owners = await Get(Owners, OwnersRequest((path,)))
       # ... only checks first-party sources
   ```

   New code:
   ```python
   for namespace in required_namespaces:
       # Try first-party sources first (existing logic)
       file_path = namespace_to_path(namespace)
       owners = await Get(Owners, OwnersRequest((path,)))

       if owners:
           # Found first-party source, use it
           # ... existing logic
       else:
           # Not first-party, check third-party mapping
           third_party_addrs = clojure_mapping.address_for_namespace(
               namespace, my_resolve
           )
           if third_party_addrs:
               # Found in third-party mapping
               explicitly_provided_deps.maybe_warn_of_ambiguous_dependency_inference(
                   third_party_addrs,
                   field_set.address,
                   import_reference="namespace",
                   context=f"The target {field_set.address} requires `{namespace}`",
               )
               maybe_disambiguated = explicitly_provided_deps.disambiguated(
                   third_party_addrs
               )
               if maybe_disambiguated:
                   dependencies.add(maybe_disambiguated)
   ```

2. **Add `ClojureNamespaceMapping` to rule signature**
   ```python
   @rule(desc="Infer Clojure source dependencies", level=LogLevel.DEBUG)
   async def infer_clojure_source_dependencies(
       request: InferClojureSourceDependencies,
       jvm: JvmSubsystem,
       symbol_mapping: SymbolMapping,  # � Existing (for Java)
       clojure_mapping: ClojureNamespaceMapping,  # � New (for Clojure)
   ) -> InferredDependencies:
   ```

3. **Prioritize first-party over third-party**
   - Always check first-party sources first
   - Only fallback to third-party mapping if no first-party match
   - This prevents third-party libs from shadowing local code

4. **Handle not found**
   - If namespace not in first-party OR third-party:
     - Currently: silently ignored
     - New: could optionally warn (behind a flag)
     - Or: trust that missing dep will cause runtime error

**Deliverables:**
- Modified `dependency_inference.py` with third-party namespace lookup
- Tests for third-party namespace inference
- Tests for first-party precedence
- Tests for ambiguity handling

**Estimated effort:** 6-8 hours

### Phase 4: Manual Override Support (Optional Enhancement)

**Goal:** Allow users to manually specify namespace mappings

**Tasks:**

1. **Add `clojure_namespaces` field to `jvm_artifact`**
   ```python
   class ClojureNamespacesField(StringSequenceField):
       alias = "clojure_namespaces"
       help = """
       Clojure namespaces provided by this artifact.

       Usually auto-detected from JAR contents, but can be manually specified
       to override or supplement auto-detection.

       Example: ["clojure.data.json"]
       """
   ```

2. **Merge manual specifications into mapping**
   - When building `ClojureNamespaceMapping`, also read `clojure_namespaces` fields
   - Manual entries take precedence over metadata file
   - Union semantics: manual + auto-detected

3. **Update metadata file generator to respect manual fields**
   - When generating metadata, also include manually-specified namespaces
   - Mark them as "manual" in metadata for transparency

**Deliverables:**
- `clojure_namespaces` field on `jvm_artifact`
- Merging logic in `ClojureNamespaceMapping` construction
- Tests for manual override
- Documentation

**Estimated effort:** 4-6 hours

### Phase 5: Testing & Documentation

**Goal:** Comprehensive test coverage and user documentation

**Tasks:**

1. **Unit tests**
   - JAR analysis: `test_jar_analyzer.py`
     - Test extracting namespaces from sample JARs
     - Test handling empty JARs
     - Test handling JARs without Clojure files
     - Test handling AOT-compiled JARs

   - Mapping construction: `test_clojure_symbol_mapping.py`
     - Test loading from metadata file
     - Test handling missing metadata
     - Test staleness detection
     - Test ambiguity (multiple artifacts, same namespace)

   - Dependency inference: `test_dependency_inference.py` (extend existing)
     - Test third-party namespace inference
     - Test first-party precedence
     - Test ambiguity resolution
     - Test missing namespace handling

2. **Integration tests**
   - Create sample project with third-party Clojure dependencies
   - Run `pants generate-lockfiles`
   - Verify metadata file generated correctly
   - Run `pants dependencies` and verify inferred deps
   - Test with multiple resolves (java17, java21)

3. **Documentation**
   - Update plugin README with third-party inference section
   - Document the metadata file format
   - Document manual override mechanism
   - Add troubleshooting guide (stale metadata, missing namespaces, etc.)
   - Add examples to integration tests

**Deliverables:**
- Comprehensive test suite (20+ test cases)
- Updated documentation
- Integration test project
- Troubleshooting guide

**Estimated effort:** 6-10 hours

## Technical Details

### JAR Analysis Implementation

**Parsing approach:**

Option A: **Use existing `parse_namespace()` function** (RECOMMENDED)
- Already have `namespace_parser.py` with `parse_namespace(source_content)`
- Proven to work for first-party code
- Handles edge cases (multi-line strings, comments, reader conditionals)
- Consistent with existing code

```python
async def analyze_jar_for_namespaces(jar_path: Path) -> tuple[str, ...]:
    namespaces = set()
    with zipfile.ZipFile(jar_path) as jar:
        for entry in jar.namelist():
            if entry.endswith(('.clj', '.cljc', '.clje')):
                content = jar.read(entry).decode('utf-8', errors='ignore')
                ns = parse_namespace(content)
                if ns:
                    namespaces.add(ns)
    return tuple(sorted(namespaces))
```

Option B: Use clj-kondo for JAR analysis
- More robust, but overkill for simple namespace extraction
- Would require running clj-kondo on extracted JAR contents
- Slower, more complex

**Recommendation:** Option A. Use existing `parse_namespace()` function.

### Handling AOT-Compiled JARs

Some Clojure JARs are AOT-compiled and only contain `.class` files, no `.clj` source.

**Strategy:**
1. Check for `.clj` files first
2. If none found, check for Clojure-specific `.class` files
3. Clojure namespaces compile to classes like: `clojure/data/json.class`, `clojure/data/json__init.class`
4. Parse class filenames to infer namespaces:
   - `clojure/data/json.class` � `clojure.data.json`
   - Ignore `__init`, `$fn`, etc. (internal implementation classes)

```python
def namespace_from_class_path(class_path: str) -> str | None:
    """Infer Clojure namespace from .class file path."""
    if not class_path.endswith('.class'):
        return None
    # Remove .class extension
    path = class_path[:-6]
    # Ignore internal classes
    if '__' in path or '$' in path:
        return None
    # Convert path to namespace
    return path.replace('/', '.')
```

### Handling Ambiguity

When multiple JARs provide the same namespace (rare but possible):

1. **Store all addresses in mapping**
   ```python
   mapping: FrozenDict[tuple[str, str], tuple[Address, ...]]
   ```

2. **Use existing disambiguation mechanism**
   - Pants already has `ExplicitlyProvidedDependencies.disambiguated()`
   - Shows warning to user:
     ```
     The target //src:core has multiple providers for namespace 'example.util':
       - 3rdparty/jvm:lib-a
       - 3rdparty/jvm:lib-b

     Please explicitly specify which one in the dependencies field.
     ```

3. **Resolution via explicit dependency**
   - User adds explicit entry to `dependencies` field
   - Disambiguation logic picks the explicit one

### Metadata File Schema

**Version 1.0 schema:**

```json
{
  "$schema": "https://pantsbuild.org/schemas/clojure-namespaces-v1.json",
  "version": "1.0",
  "generated_at": "2025-10-26T12:34:56.789Z",
  "lockfile": "default.lock",
  "lockfile_hash": "sha256:abc123...",
  "resolve": "default",
  "artifacts": {
    "org.clojure:data.json:2.4.0": {
      "address": "3rdparty/jvm:data-json",
      "group": "org.clojure",
      "artifact": "data.json",
      "version": "2.4.0",
      "namespaces": ["clojure.data.json"],
      "source": "jar-analysis"
    },
    "com.example:utils:1.0.0": {
      "address": "3rdparty/jvm:utils",
      "group": "com.example",
      "artifact": "utils",
      "version": "1.0.0",
      "namespaces": [
        "com.example.utils.core",
        "com.example.utils.helpers"
      ],
      "source": "jar-analysis"
    }
  }
}
```

**Fields:**
- `version`: Schema version (for future evolution)
- `generated_at`: Timestamp (for debugging)
- `lockfile`: Corresponding lockfile name
- `lockfile_hash`: SHA256 of lockfile (for staleness detection)
- `resolve`: JVM resolve name
- `artifacts`: Map of coordinate � artifact info
  - `address`: Pants address of `jvm_artifact` target
  - `group`, `artifact`, `version`: Maven coordinates
  - `namespaces`: List of Clojure namespaces provided
  - `source`: How namespaces were determined (`jar-analysis`, `manual`, `heuristic`)

**File location:** Same directory as lockfile
- `3rdparty/jvm/default.lock` � `3rdparty/jvm/default_clojure_namespaces.json`
- `3rdparty/jvm/java17.lock` � `3rdparty/jvm/java17_clojure_namespaces.json`

### Staleness Detection

Metadata can become stale if:
1. Lockfile is regenerated
2. Artifact versions change
3. Manual edits to metadata

**Staleness check:**
```python
def is_metadata_stale(metadata_file: Path, lockfile: Path) -> bool:
    if not metadata_file.exists():
        return True

    # Read metadata
    with open(metadata_file) as f:
        metadata = json.load(f)

    # Compute current lockfile hash
    with open(lockfile, 'rb') as f:
        current_hash = hashlib.sha256(f.read()).hexdigest()

    # Compare
    return metadata.get('lockfile_hash') != f"sha256:{current_hash}"
```

**Handling staleness:**
- Option 1: Error and tell user to regenerate
  ```
  Error: Clojure namespace metadata is stale. Run: pants generate-lockfiles
  ```
- Option 2: Auto-regenerate (if possible)
- Option 3: Fall back to heuristics (use artifact name as namespace prefix)

**Recommendation:** Option 1 (explicit error) for initial implementation.

### Caching Strategy

**JAR analysis caching:**
- Cache key: SHA256 of JAR file
- Cache value: List of namespaces
- Location: Pants cache directory (`.pants.d/cache/jar_namespace_analysis/`)
- Invalidation: Automatic (based on JAR hash)

This ensures we don't re-analyze the same JAR multiple times across different resolves or projects.

```python
@dataclass(frozen=True)
class JarNamespaceAnalysisRequest:
    jar_digest: Digest
    jar_filename: str

@rule
async def analyze_jar_namespaces(
    request: JarNamespaceAnalysisRequest,
) -> JarNamespaceAnalysis:
    # Pants will automatically cache based on input digests
    # ...
```

## Testing Strategy

### Unit Tests

**JAR Analysis (`test_jar_analyzer.py`)**

1. `test_analyze_jar_with_clojure_sources()`
   - Create test JAR with `.clj` files
   - Extract namespaces
   - Verify correct namespaces returned

2. `test_analyze_jar_with_cljc_sources()`
   - Test `.cljc` (Clojure/ClojureScript) files

3. `test_analyze_jar_with_aot_compiled_classes()`
   - Test JAR with only `.class` files
   - Verify namespace inference from class paths

4. `test_analyze_jar_with_no_clojure_content()`
   - Test pure Java JAR
   - Should return empty namespace list

5. `test_analyze_jar_with_invalid_namespace()`
   - Test handling of malformed namespace declarations

**Mapping Construction (`test_clojure_symbol_mapping.py`)**

1. `test_load_mapping_from_metadata()`
   - Create mock metadata file
   - Load into `ClojureNamespaceMapping`
   - Verify lookups work

2. `test_handle_missing_metadata()`
   - Test behavior when metadata file doesn't exist

3. `test_staleness_detection()`
   - Test detecting when metadata is out-of-date

4. `test_ambiguous_namespace_multiple_artifacts()`
   - Test when two artifacts provide same namespace
   - Verify both addresses returned

**Dependency Inference (`test_dependency_inference.py` - extend existing)**

1. `test_infer_third_party_clojure_namespace()`
   ```python
   def test_infer_third_party_clojure_namespace(rule_runner: RuleRunner):
       rule_runner.write_files({
           "src/example/BUILD": "clojure_source(name='lib', source='core.clj')",
           "src/example/core.clj": """
           (ns example.core
             (:require [clojure.data.json :as json]))
           """,
           "3rdparty/jvm/BUILD": """
           jvm_artifact(
             name="data-json",
             group="org.clojure",
             artifact="data.json",
             version="2.4.0",
           )
           """,
           "3rdparty/jvm/default_clojure_namespaces.json": """
           {
             "artifacts": {
               "org.clojure:data.json:2.4.0": {
                 "address": "3rdparty/jvm:data-json",
                 "namespaces": ["clojure.data.json"]
               }
             }
           }
           """,
       })

       deps = rule_runner.request(
           Addresses,
           [DependenciesRequest(Address("src/example", target_name="lib"))],
       )

       assert Address("3rdparty/jvm", target_name="data-json") in deps
   ```

2. `test_first_party_precedence_over_third_party()`
   - Create first-party source with same namespace as third-party
   - Verify first-party is chosen

3. `test_ambiguous_third_party_namespace()`
   - Two artifacts provide same namespace
   - Verify warning and disambiguation

4. `test_manual_override_third_party_mapping()`
   - Test `clojure_namespaces` field on `jvm_artifact`
   - Verify manual specification takes precedence

### Integration Tests

**End-to-end flow:**

1. Create test project:
   ```
   test-project/
     BUILD
     src/main.clj
     3rdparty/jvm/
       BUILD
       coursier_lockfile.txt
   ```

2. Dependencies: Use real Clojure libraries (data.json, tools.logging)

3. Run: `pants generate-lockfiles`

4. Verify:
   - Metadata file created: `3rdparty/jvm/coursier_lockfile_clojure_namespaces.json`
   - Contains correct namespace mappings
   - File is valid JSON

5. Run: `pants dependencies src/main.clj`

6. Verify:
   - Third-party dependencies automatically inferred
   - No manual `dependencies` field needed

**Performance test:**
- Project with 50+ third-party JARs
- Measure lock file generation time
- Ensure it's acceptable (<5 min for large projects)

## Open Questions & Decisions

### Q1: What to do about AOT-compiled JARs with no source?

**Decision:** Infer namespaces from `.class` file paths.

**Rationale:** Many production Clojure JARs are AOT-compiled. We need to support them.

**Implementation:** Parse class file paths like `clojure/data/json.class` � `clojure.data.json`

---

### Q2: Should metadata file be committed to version control?

**Options:**

A. **Yes, commit it** (RECOMMENDED)
- Pros: Reproducible builds, faster CI (no regeneration), explicit audit trail
- Cons: Merge conflicts, file size

B. **No, generate on-demand**
- Pros: Smaller repo, fewer conflicts
- Cons: Slower builds, non-reproducible

C. **Make it configurable**
- Let teams decide via `.gitignore`

**Decision:** Recommend **committing it** (like `pants.lock`), but document that teams can gitignore if preferred.

---

### Q3: Should we use heuristics when metadata is missing?

**Heuristic:** Assume namespace prefix matches artifact group/name.
Example: `org.clojure:data.json` likely provides `clojure.data.json`

**Options:**

A. **No heuristics, fail loudly**
- Forces user to regenerate metadata
- Explicit and safe

B. **Use heuristics with warning**
- More convenient
- Risk of incorrect inference

**Decision:** **No heuristics initially**. Fail with clear error message. Can add heuristics later if needed.

---

### Q4: Should this work for ClojureScript (.cljs)?

**Current scope:** Clojure only (`.clj`, `.cljc`)

**Future:** Could extend to ClojureScript with minimal changes (same namespace structure)

**Decision:** **Out of scope for initial implementation**. Design should be extensible.

---

### Q5: How to handle shaded/relocated JARs?

Some JARs use Maven shading to relocate namespaces (rare in Clojure ecosystem).

**Decision:** Not supported initially. If needed, users can use manual override field.

---

### Q6: Should we inspect JARs in parallel?

When generating metadata for 100+ JARs, serial analysis could be slow.

**Decision:** Start with serial, optimize later if needed. Pants' caching should help.

---

### Q7: What about non-standard namespace conventions?

Some libraries use unconventional namespace structures.

**Decision:** Trust the namespace declaration in the code. Our parser handles whatever's in the `(ns ...)` form.

---

### Q8: Should this work for other JVM languages?

User mentioned: "it'd be nice to have this functionality in place for other JVM languages (like Java and Scala) too"

**Analysis:**
- **Java**: Already handled by `SymbolMapping` (class-based, not namespace-based)
- **Scala**: Uses Java-style packages, also handled by `SymbolMapping`
- **Kotlin**: Could benefit from similar namespace analysis (objects, top-level functions)
- **Clojure**: Special case due to namespace-centric design

**Decision:** Design `ClojureNamespaceMapping` as a **pattern that could be replicated** for other languages, but don't try to make it generic initially. Keep it Clojure-focused.

**Future extensibility:**
- Could refactor to `NamespaceMapping` (language-agnostic)
- Could add `KotlinNamespaceMapping`, etc.
- But that's future work

---

## Success Criteria

### MVP (Minimum Viable Product)

 JAR introspection generates namespace metadata during `pants generate-lockfiles`
 Metadata file correctly maps namespaces � artifacts
 Dependency inference uses metadata to infer third-party Clojure namespace dependencies
 Works for standard Clojure libraries (data.json, tools.logging, etc.)
 Handles multiple resolves (java17, java21)
 Test coverage >80%
 Documentation explains the feature

### Stretch Goals

<� Manual override field on `jvm_artifact`
<� Staleness auto-detection and regeneration
<� Support for AOT-compiled JARs
<� Performance optimization (parallel JAR analysis)
<� Integration with existing example projects
<� Heuristic fallback for missing metadata

### Long-Term Vision

=� Extend `SymbolMapping` to be language-agnostic
=� Support ClojureScript namespaces
=� Support Kotlin object/package analysis
=� Auto-regenerate stale metadata in background
=� Contribute back to Pants core (if useful for other languages)

---

## Timeline Estimate

| Phase | Effort | Duration |
|-------|--------|----------|
| Phase 1: Metadata generation | 8-12 hours | 2-3 days |
| Phase 2: Symbol mapping structure | 4-6 hours | 1 day |
| Phase 3: Inference integration | 6-8 hours | 1-2 days |
| Phase 4: Manual override (optional) | 4-6 hours | 1 day |
| Phase 5: Testing & docs | 6-10 hours | 1-2 days |
| **Total** | **28-42 hours** | **6-9 days** |

**Note:** Assumes part-time work. Could be faster with focused full-time effort.

---

## References

### Related Files

- `pants-plugins/clojure_backend/dependency_inference.py` - Current inference logic
- `pants-plugins/clojure_backend/utils/namespace_parser.py` - Namespace parsing utilities
- `/Users/hopper/workspace/python/pants/src/python/pants/jvm/dependency_inference/symbol_mapper.py` - Pants' Java SymbolMapping
- `/Users/hopper/workspace/python/pants/src/python/pants/backend/python/macros/common_fields.py` - Python's module_mapping pattern

### Related Docs

- `docs/plans/20251014_dep_inference_improvements.md` - Original dependency inference plan
- `docs/plans/20251014_phase1_findings.md` - Research on SymbolMapping

### External Resources

- [Pants JVM dependency inference](https://www.pantsbuild.org/docs/jvm-overview#dependency-inference)
- [Python module_mapping](https://www.pantsbuild.org/docs/python-third-party-dependencies#module-mapping)
- [Coursier lockfile format](https://get-coursier.io/docs/cli-resolve#lock-files)

---

## Appendix: Example Scenarios

### Scenario 1: Basic Third-Party Inference

**Code:**
```clojure
(ns myproject.api
  (:require [clojure.data.json :as json]))

(defn parse-response [body]
  (json/read-str body))
```

**BUILD file:**
```python
clojure_source(
    name="api",
    source="api.clj",
    # No manual dependencies!
)
```

**What happens:**
1. Dependency inference runs
2. Sees `(:require [clojure.data.json ...])`
3. Checks `ClojureNamespaceMapping.address_for_namespace("clojure.data.json", "default")`
4. Finds `3rdparty/jvm:data-json`
5. Automatically adds it as a dependency

**Result:**  Automatic inference, no manual work

---

### Scenario 2: Ambiguous Namespace (Multiple Providers)

**Situation:** Two different JARs both provide `com.example.util` namespace

**What happens:**
1. Dependency inference runs
2. Looks up `com.example.util`
3. Finds two addresses: `[":lib-a", ":lib-b"]`
4. Calls `maybe_warn_of_ambiguous_dependency_inference()`
5. Shows warning:
   ```
   WARNING: The target //src:api has ambiguous dependency:
     Namespace 'com.example.util' is provided by:
       - 3rdparty/jvm:lib-a
       - 3rdparty/jvm:lib-b

   Please specify which one explicitly in dependencies=[...].
   ```

**Resolution:** User adds explicit dependency:
```python
clojure_source(
    name="api",
    source="api.clj",
    dependencies=[":lib-a"],  # Explicit choice
)
```

**Result:**  Disambiguation works, clear error message

---

### Scenario 3: First-Party Shadows Third-Party

**Situation:**
- Local code defines namespace `json.parser`
- Third-party JAR also provides `json.parser`

**What happens:**
1. Dependency inference runs
2. First tries `OwnersRequest` for `json/parser.clj`
3. **Finds local file** � uses first-party
4. Never checks third-party mapping

**Result:**  First-party takes precedence (expected behavior)

---

### Scenario 4: Missing Namespace (Not in Metadata)

**Code:**
```clojure
(ns myproject.core
  (:require [unknown.library :as lib]))
```

**What happens:**
1. Dependency inference runs
2. Checks first-party: not found
3. Checks third-party mapping: not found
4. No dependency inferred
5. At runtime: error "Could not locate unknown/library.clj"

**Resolution:** User needs to:
- Add the `jvm_artifact` to BUILD file
- Run `pants generate-lockfiles` (regenerate metadata)
- Or manually add to `dependencies` field

**Result:** � No inference, but clear error at runtime

---

### Scenario 5: AOT-Compiled JAR (No Source)

**JAR contents:**
```
clojure/
  core/
    async.class
    async__init.class
    async$go.class
  ...
```

**What happens:**
1. During `generate-lockfiles`, JAR is analyzed
2. No `.clj` files found
3. Falls back to `.class` file analysis
4. Finds `clojure/core/async.class` � infers `clojure.core.async` namespace
5. Stores in metadata

**Result:**  Works for AOT-compiled JARs

---

## Appendix: Migration Guide

For existing projects that manually specify dependencies:

### Before:
```python
clojure_source(
    name="api",
    source="api.clj",
    dependencies=[
        "3rdparty/jvm:data-json",
        "3rdparty/jvm:tools-logging",
        "//src/utils:core",
    ],
)
```

### After:
```python
clojure_source(
    name="api",
    source="api.clj",
    dependencies=[
        # Third-party deps removed (inferred automatically)
        # First-party deps removed (inferred automatically)
        # Only keep non-Clojure deps (e.g., Java-only libraries)
    ],
)
```

Or even simpler:
```python
clojure_source(
    name="api",
    source="api.clj",
    # No dependencies field needed!
)
```

**Migration steps:**
1. Run `pants generate-lockfiles` (creates namespace metadata)
2. Remove explicit Clojure namespace dependencies from BUILD files
3. Run `pants check` to verify everything still works
4. Commit changes

---

## Summary

This plan proposes **automatic third-party Clojure dependency inference** by:

1. **Analyzing JARs during lock file generation** to extract Clojure namespaces
2. **Storing namespace�artifact mappings** in a metadata file alongside the lockfile
3. **Using the mappings during dependency inference** to automatically resolve third-party namespace requirements
4. **Providing manual overrides** for edge cases via `clojure_namespaces` field

This approach balances **automation** (no manual work for 99% of cases), **performance** (pre-computed metadata), and **flexibility** (manual overrides when needed).

The implementation is estimated at **28-42 hours** of development time, broken into 5 phases, with a clear path to MVP and stretch goals.

**Next steps:**
1. Review and approve this plan
2. Create implementation tasks
3. Begin Phase 1 (metadata generation)
