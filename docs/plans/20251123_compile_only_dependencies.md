# Implementation Plan: Compile-Only Dependencies for Clojure Deploy JAR

**Date**: 2025-11-23
**Status**: Reviewed and Revised
**Last Updated**: 2025-11-23

## Plan Overview

This plan implements Maven "provided scope" equivalent for the Pants Clojure plugin, allowing dependencies to be available during compilation but excluded from the final JAR.

**Key Approach**:
1. Add `compile_dependencies` field to `clojure_deploy_jar` target
2. Use `TransitiveTargets` to compute full transitive closure of compile-only dependencies
3. Filter addresses BEFORE computing classpath (not after - this was the critical insight from review)
4. AOT compilation gets FULL classpath; JAR packaging gets FILTERED classpath
5. Dependencies must appear in BOTH `dependencies` and `compile_dependencies` fields (by design)

## Problem Statement

Currently, the Clojure plugin includes ALL transitive dependencies in the final `clojure_deploy_jar` artifact. We need a mechanism similar to Maven's "provided" scope to mark certain dependencies as compile-only:

- **Compile-only dependencies** should be available during AOT compilation
- **Compile-only dependencies AND their transitive dependencies** should be excluded from the final JAR
- This is different from `shading_rules` which requires specifying package/class patterns - we need to exclude entire dependency trees

### Current Behavior

When packaging a `clojure_deploy_jar`:
1. Dependencies are resolved via `classpath_get()` which fetches all transitive dependencies
2. AOT compilation runs with full classpath including all dependencies
3. All dependency JARs are extracted and merged into the final uberjar (in `package.py` lines 235-252)

**Problem**: No way to exclude dependencies that are only needed at compile time.

**Use Case**: Servlet APIs, application server libraries, or platform-provided dependencies that will be available in the runtime environment but shouldn't be bundled.

---

## Research Findings

### Maven's Approach
- **Provided scope**: Dependencies available during compilation but excluded from runtime packaging
- **Not transitive**: Provided-scoped dependencies don't propagate to consumers
- **Full tree exclusion**: Both the dependency and ALL its transitive dependencies are excluded

### Pants JVM Backend Patterns
- **JvmArtifactExclusion**: Pattern for excluding specific artifacts by group/artifact coordinates
- **Transitive excludes**: Python PEX implements `supports_transitive_excludes = True` with `!!` syntax
- **RuntimePackageDependenciesField**: Special-case dependency field for test-only runtime dependencies
- **No existing compile scope**: JVM backend has a TODO comment about classpath scope awareness

### Current Clojure Plugin Architecture
- No shading rules implementation exists (contrary to initial assumption)
- Dependencies fetched THREE times: compile_clj.py, aot_compile.py, package.py
- Best injection point: Filter in `package.py` before extracting JARs into uberjar

---

## Proposed Solution

### Option A: Dedicated Field for Compile-Only Dependencies (Recommended)

Add a new field `compile_dependencies` to `clojure_deploy_jar` target that explicitly lists dependencies needed only at compile time.

**Advantages**:
- Clear, explicit intent - separates compile-time from runtime dependencies
- Follows Pants pattern of multiple dependency fields (like `RuntimePackageDependenciesField`)
- Easy to implement - simple address list filtering
- No ambiguity about which dependencies are excluded

**Disadvantages**:
- Requires listing compile-only addresses in BOTH `dependencies` and `compile_dependencies` fields
  - This is necessary because compile-only deps ARE real dependencies (needed for compilation)
  - The `compile_dependencies` field just marks them for exclusion from the final artifact
- More verbose BUILD files (but this explicitness makes intent clear)

### Option B: Dependency Annotation with Metadata

Extend the dependency syntax to support metadata tags (e.g., `"path/to:target#compile-only"`).

**Advantages**:
- Single dependency list
- Inline declaration

**Disadvantages**:
- Requires changes to Pants core dependency parsing
- Non-standard syntax
- More complex implementation

### Option C: Special Target Type for Compile-Only Wrappers

Create a new target type `clojure_compile_dependency` that wraps `jvm_artifact` targets.

**Advantages**:
- Type-safe
- Can be queried via target graph

**Disadvantages**:
- Most complex implementation
- Adds another target type to learn
- Overkill for this use case

**Decision**: Proceed with **Option A** - dedicated field.

---

## Implementation Plan

### Phase 1: Add Compile Dependencies Field ✅ COMPLETE

**Goal**: Add new field to `clojure_deploy_jar` target and validate it works.

**Implementation Note**: Uses `SpecialCasedDependencies` as base class (superior to generic `Dependencies`) to prevent interference with normal transitive dependency resolution.

**Files to modify**:
1. `pants-plugins/clojure_backend/target_types.py`
2. `pants-plugins/clojure_backend/goals/package.py`

**Tasks**:
1. Define `ClojureCompileDependenciesField` in `target_types.py`
   - Extends `Dependencies` base class
   - Add to `ClojureDeployJarTarget.core_fields`
   - Document the field's purpose and behavior

2. Update `ClojureDeployJarFieldSet` in `package.py`
   - Add `compile_dependencies: ClojureCompileDependenciesField` attribute
   - Ensure field is accessible in packaging rule

3. Add integration test
   - Create test BUILD file with `compile_dependencies` field
   - Verify field is parsed correctly
   - Test that target can be instantiated

**Validation**: Run `pants test pants-plugins/tests/test_package_clojure_deploy_jar.py` to ensure no regressions.

---

### Phase 2: Implement Transitive Dependency Resolution ✅ COMPLETE

**Goal**: Build mechanism to resolve transitive closure of compile-only dependencies.

**Files to create/modify**:
1. Create new file: `pants-plugins/clojure_backend/compile_dependencies.py`
2. Modify: `pants-plugins/clojure_backend/goals/package.py`

**Tasks**:
1. Create `CompileOnlyDependencies` dataclass
   - Contains `FrozenOrderedSet[Address]` of all addresses to exclude
   - Computed from `compile_dependencies` field

2. Implement `resolve_compile_only_dependencies()` rule
   - Input: `ClojureCompileDependenciesField`
   - Process:
     a. Parse addresses from field using `.to_unparsed_address_inputs()`
     b. Use `Get(Targets, UnparsedAddressInputs)` to resolve addresses to targets
     c. Use `Get(TransitiveTargets, TransitiveTargetsRequest)` for EACH compile-only dependency
     d. Collect all addresses from each `TransitiveTargets.dependencies` (includes transitives)
     e. Union all address sets together
   - Output: `CompileOnlyDependencies` with full transitive address set

**Technical Details**:

**Why TransitiveTargets works for both first-party and third-party:**
- `TransitiveTargets` handles ALL target types uniformly
- For first-party targets (`clojure_source`, etc.), it traverses `dependencies` field recursively
- For third-party targets (`jvm_artifact`), it resolves through Coursier and expands the dependency graph automatically
- No special-casing needed - Pants handles the complexity internally

**Example code structure**:
```python
@dataclass(frozen=True)
class CompileOnlyDependencies:
    addresses: FrozenOrderedSet[Address]

@rule
async def resolve_compile_only_dependencies(
    field: ClojureCompileDependenciesField,
) -> CompileOnlyDependencies:
    if not field.value:
        return CompileOnlyDependencies(FrozenOrderedSet())

    # Get compile-only target objects
    unparsed = field.to_unparsed_address_inputs()
    targets = await Get(Targets, UnparsedAddressInputs, unparsed)

    # Get transitive closure for each compile-only dependency
    all_transitive = await MultiGet(
        Get(TransitiveTargets, TransitiveTargetsRequest([t.address]))
        for t in targets
    )

    # Collect all addresses (including the root compile-only deps themselves)
    all_addresses = set()
    for transitive in all_transitive:
        all_addresses.add(transitive.roots[0].address)
        all_addresses.update(t.address for t in transitive.dependencies)

    return CompileOnlyDependencies(FrozenOrderedSet(all_addresses))
```

**Validation**: Write unit tests that verify transitive closure calculation for:
- First-party Clojure targets with nested dependencies
- Third-party `jvm_artifact` targets with known transitives
- Mixed first-party and third-party dependencies
- Empty compile_dependencies field (should return empty set)

---

### Phase 3: Filter Dependencies During Packaging ✅ COMPLETE

**Goal**: Exclude compile-only dependencies from the final JAR while keeping them available for AOT compilation.

**Files to modify**:
1. `pants-plugins/clojure_backend/goals/package.py`

**CRITICAL UNDERSTANDING**: The key insight is that we must filter addresses BEFORE computing the classpath, not after. The `Classpath` object doesn't expose address information per entry, so we can't filter it post-computation.

**Tasks**:

1. **Get transitive targets for the deploy jar** (around line 81-84 in current code)
   - Current code already does this:
     ```python
     transitive_targets = await Get(
         TransitiveTargets,
         TransitiveTargetsRequest([field_set.address]),
     )
     ```

2. **Compute compile-only exclusions** (new code after getting transitive targets)
   - Add: `Get(CompileOnlyDependencies, ClojureCompileDependenciesField, field_set.compile_dependencies)`
   - This gives us the set of all addresses to exclude

3. **Create two separate address sets**:
   - **Full address set** (for AOT compilation): All transitive target addresses
   - **Runtime address set** (for JAR packaging): Full set minus compile-only addresses

   ```python
   # Get compile-only exclusions
   compile_only_deps = await Get(
       CompileOnlyDependencies,
       ClojureCompileDependenciesField,
       field_set.compile_dependencies,
   )

   # Build full address set for AOT compilation
   all_source_addresses = Addresses(
       t.address for t in transitive_targets.dependencies
       if t.has_field(ClojureSourceField)
   )

   # Build runtime address set for JAR packaging (excludes compile-only)
   runtime_addresses = Addresses(
       addr for addr in all_source_addresses
       if addr not in compile_only_deps.addresses
   )
   ```

4. **Use separate classpaths for AOT vs packaging**:
   - AOT compilation uses FULL classpath (including compile-only deps)
   - JAR packaging uses FILTERED classpath (excluding compile-only deps)

   **Current code structure** (lines 194-206):
   ```python
   jdk_env, classpath, compiled_classes = await MultiGet(
       Get(JdkEnvironment, JdkRequest, jdk_request),
       Get(Classpath, Addresses, source_addresses),  # <- This is the issue
       Get(CompiledClojureClasses, CompileClojureAOTRequest(...)),
   )
   ```

   **New code structure**:
   ```python
   # First, get transitive targets and compute exclusions
   transitive_targets, compile_only_deps = await MultiGet(
       Get(TransitiveTargets, TransitiveTargetsRequest([field_set.address])),
       Get(CompileOnlyDependencies, ClojureCompileDependenciesField,
           field_set.compile_dependencies),
   )

   # Build address sets
   all_addresses = Addresses(...)  # All dependencies
   runtime_addresses = Addresses(  # Filtered for runtime
       addr for addr in all_addresses
       if addr not in compile_only_deps.addresses
   )

   # AOT compilation uses FULL addresses (in CompileClojureAOTRequest)
   # Packaging uses RUNTIME addresses (for classpath)
   jdk_env, runtime_classpath, compiled_classes = await MultiGet(
       Get(JdkEnvironment, JdkRequest, jdk_request),
       Get(Classpath, Addresses, runtime_addresses),  # Filtered!
       Get(CompiledClojureClasses, CompileClojureAOTRequest(
           source_addresses=all_addresses,  # Full classpath for compilation!
           ...
       )),
   )
   ```

5. **Update JAR packaging to use filtered classpath**:
   - The rest of the code (lines 221+ for JAR creation) uses `runtime_classpath`
   - No changes needed to JAR extraction logic
   - The filtering is already done at the address level

**Technical Notes**:
- **Why this works**: `CompileClojureAOTRequest` internally calls `classpath_get()` with its `source_addresses` parameter, giving it access to compile-only dependencies during compilation
- **Why this is clean**: No post-processing of `Classpath` objects needed - we filter at the source
- **Performance**: Pants' caching ensures we don't recompute classpaths unnecessarily
- **Correctness**: AOT compilation sees everything it needs; runtime JAR only includes runtime deps

**Validation**:
- Integration test that creates deploy JAR with compile dependencies
- Verify JAR doesn't contain classes from compile-only deps
- Verify JAR DOES contain classes from runtime deps
- Verify AOT compilation succeeds (compile-only deps were available)
- Use `zipfile` to inspect JAR contents in test and assert expected contents

---

### Phase 4: Handle Edge Cases

**Goal**: Ensure robustness and handle special cases.

**Files to modify**:
1. `pants-plugins/clojure_backend/goals/package.py`
2. `pants-plugins/clojure_backend/compile_dependencies.py`

**Tasks**:
1. **Overlapping dependencies**: **IMPORTANT CLARIFICATION** - Dependencies MUST appear in BOTH fields
   - Compile-only dependencies must be listed in regular `dependencies` field (so dependency inference and compilation can find them)
   - AND also listed in `compile_dependencies` field (to mark them for exclusion from JAR)
   - This is NOT a bug or redundancy - it's by design
   - Example:
     ```python
     clojure_deploy_jar(
         name="app",
         dependencies=[":servlet-api", ":my-lib"],  # All deps needed for compilation
         compile_dependencies=[":servlet-api"],      # Subset to exclude from JAR
     )
     ```
   - Do NOT warn about this - it's the expected usage pattern
   - Only warn if `compile_dependencies` contains an address NOT in `dependencies` (that would be a user error)

2. **Dependency inference**: Ensure inferred dependencies aren't accidentally excluded
   - Compile-only should only apply to explicitly listed addresses
   - Inferred deps should stay in runtime classpath

3. **Test targets**: Consider if `clojure_test` should have similar field
   - For now, skip - tests don't produce deploy JARs
   - Can add later if requested

4. **Empty compile_dependencies field**: Handle gracefully
   - If field is empty or not provided, behave exactly as before
   - No filtering applied

5. **Invalid addresses**: Error handling for addresses that don't resolve
   - Let Pants' standard address validation handle it
   - Will naturally error when trying to `Get(Targets, Addresses)`

**Validation**: Add test cases for each edge case.

---

### Phase 5: Documentation and Examples

**Goal**: Provide clear documentation and working examples.

**Files to create/modify**:
1. Create: `docs/compile_dependencies.md`
2. Update: `README.md` (if it exists)
3. Create: Example BUILD file in test fixtures

**Tasks**:
1. Write comprehensive documentation
   - Explain what compile dependencies are
   - When to use them (servlet APIs, platform libraries, etc.)
   - How they differ from regular dependencies
   - How they differ from shading rules
   - Include Maven comparison for users familiar with that ecosystem

2. Create example BUILD files
   - Realistic example with servlet API
   - Show both first-party and third-party compile dependencies
   - Demonstrate the full transitive exclusion behavior

3. Add inline docstrings
   - Update `ClojureCompileDependenciesField.help` with detailed explanation
   - Add code comments in filtering logic

**Example BUILD file**:
```python
jvm_artifact(
    name="servlet-api",
    group="javax.servlet",
    artifact="servlet-api",
    version="2.5",
)

clojure_source(
    name="handler",
    source="handler.clj",
    dependencies=[":servlet-api"],  # Needed for compilation
)

clojure_deploy_jar(
    name="app",
    main="my.app.core",
    dependencies=[":handler", ":servlet-api"],     # All deps for compilation
    compile_dependencies=[":servlet-api"],          # Mark for JAR exclusion
)
```

---

## Testing Strategy

### Unit Tests
- Field parsing and validation
- Transitive dependency resolution algorithm
- Filtering logic in isolation

### Integration Tests
- End-to-end packaging with compile dependencies
- JAR content verification (classes excluded)
- Verify AOT compilation still works
- Test with mixed first-party and third-party deps

### Manual Testing
- Create sample Clojure project
- Build deploy JAR with compile dependencies
- Verify JAR size reduction
- Verify JAR still runs correctly
- Test against real servlet container

---

## Potential Issues and Mitigations

### ~~Issue 1: Classpath API Limitations~~ [RESOLVED]
**Problem**: ~~`Classpath` object may not expose address information needed for filtering.~~

**Resolution**:
- This was a fundamental misunderstanding in the original plan
- We don't filter the `Classpath` object - we filter addresses BEFORE creating the classpath
- Use `TransitiveTargets` to get all dependency addresses, filter them, then request `Classpath` with the filtered set
- No changes to Pants core needed

### ~~Issue 2: Coursier Lock File Complexity~~ [RESOLVED]
**Problem**: ~~Traversing transitive dependencies in Coursier lock files is complex.~~

**Resolution**:
- No need to manually traverse Coursier lock files
- `TransitiveTargets` handles this automatically for `jvm_artifact` targets
- Pants' dependency resolution already expands third-party dependencies
- We just collect the resulting addresses - Pants does the heavy lifting

### Issue 3: Performance Impact
**Problem**: Resolving transitive closure for every compile dependency could be slow.

**Mitigation**:
- Use Pants' caching - resolution is memoized
- Profile to measure impact
- Consider lazy evaluation if needed

### Issue 4: Interaction with Dependency Inference
**Problem**: Inferred dependencies might conflict with explicit compile dependencies.

**Mitigation**:
- Document that compile_dependencies must be explicit
- Consider adding validation that warns if inferred dep overlaps
- Clear error messages to guide users

---

## Success Criteria

1. ✅ User can specify `compile_dependencies` field in `clojure_deploy_jar` target
2. ✅ Compile-only dependencies are available during AOT compilation
3. ✅ Compile-only dependencies (and ALL transitives) are excluded from final JAR
4. ✅ Regular dependencies continue to work unchanged
5. ✅ Clear documentation with examples
6. ✅ Comprehensive test coverage
7. ✅ No performance regression for projects not using the feature

---

## Future Enhancements

### Not in Initial Implementation

1. **Shading Rules**: Could add actual shading/relocation support separately
2. **Global Compile Dependencies**: Could add resolve-level defaults
3. **Validation Warnings**: Warn if compile dependency is never actually imported
4. **IDE Integration**: Update `generate_deps.py` to mark compile deps differently in deps.edn
5. **Alternative Syntax**: Consider `dependency(address, scope="compile")` wrapper function

---

## References

- Maven dependency scopes: https://maven.apache.org/guides/introduction/introduction-to-dependency-mechanism.html
- Pants JVM backend: `/Users/hopper/workspace/python/pants/src/python/pants/jvm/`
- Current Clojure plugin: `/Users/hopper/workspace/clojure/pants-backend-clojure/pants-plugins/clojure_backend/`
- Coursier resolution: Pants' `jvm/resolve/coursier_fetch.py`
