# Dependency Inference Implementation for Pants Clojure Plugin

**Date:** October 14, 2025
**Status:**  Complete and Working

## Overview

Implemented first-party dependency inference for Clojure source and test files. The system automatically detects dependencies between Clojure files by parsing `(:require ...)` forms and finding which targets own the required namespaces.

## Implementation Summary

### Core Features

1. **Namespace Parsing**: Extract required namespaces from Clojure source files
2. **Owner Discovery**: Use `OwnersRequest` to find targets that own files defining required namespaces
3. **Resolve Filtering**: Filter candidate dependencies to match the requesting target's JVM resolve
4. **Disambiguation**: Handle cases where multiple targets could satisfy a dependency

### Files Created/Modified

- **Created**: `pants-plugins/clojure_backend/dependency_inference.py` (334 lines)
- **Modified**: `pants-plugins/clojure_backend/register.py` - Added dependency_inference to imports and rules
- **Modified**: Example projects (project-a, project-b, project-c) - Added cross-project dependencies for testing

### Key Implementation Details

```python
# Main inference rules
@rule(desc="Infer Clojure source dependencies")
async def infer_clojure_source_dependencies(
    request: InferClojureSourceDependencies,
    jvm: JvmSubsystem,
) -> InferredDependencies

@rule(desc="Infer Clojure test dependencies")
async def infer_clojure_test_dependencies(
    request: InferClojureTestDependencies,
    jvm: JvmSubsystem,
) -> InferredDependencies
```

**Parsing Strategy**: Regex-based extraction of namespace declarations and `:require` forms
- `parse_clojure_namespace()`: Extracts the file's own namespace
- `parse_clojure_requires()`: Extracts all required namespaces
- `namespace_to_path()`: Converts namespace to file path (e.g., `example.project-a.core` � `example/project_a/core.clj`)

**Discovery Strategy**: On-demand lookup using `OwnersRequest`
- Try multiple path variations: direct path and glob pattern (`**/path`)
- Glob pattern handles files in different source roots without knowing exact location

**Resolve Filtering**: String matching on addresses (hacky but functional)
```python
my_resolve = request.field_set.resolve.normalized_value(jvm)
matching_owners = []
for addr in owners:
    if f":../../{my_resolve}" in str(addr) or f":{my_resolve}" in str(addr):
        matching_owners.append(addr)
```

## Comparison to Python/Scala Implementations

| Aspect | Python | Scala | Clojure (Ours) |
|--------|--------|-------|----------------|
| **Parser** | Native Rust parser | External JVM tool (Scalafix) | Regex-based |
| **Mapping** | Pre-built map | Pre-built map | On-demand OwnersRequest |
| **Resolve Filtering** | Built into mapping | Built into mapping | String matching on addresses |
| **Third-Party Inference** | Extensive (stdlib, common packages) | Via JVM artifacts | Not implemented |
| **Performance** | Excellent (pre-built map) | Good (external tool) | Decent (on-demand) |
| **Sophistication** | High | High | Moderate |

### Strengths
-  Simpler implementation, easier to understand
-  No external dependencies (pure Python/Pants)
-  Works well for current use case
-  Handles complex nested `:require` forms

### Areas for Improvement
- � Regex parser is fragile compared to proper EDN parsing
- � Resolve filtering uses string matching instead of proper field inspection
- � On-demand lookup may be slower for large codebases
- � No third-party dependency inference yet

## Problems Solved

### 1. Nested Bracket Parsing
**Problem**: Initial regex only matched first `[...]` block in `:require` forms
**Solution**: Multi-stage parsing: find ns form � extract `:require` section � find all `[namespace ...]` forms

### 2. Owners API Usage
**Problem**: Tried to access `owners.addresses` but `Owners` is a collection, not an object
**Solution**: Iterate directly over `owners` (it's a FrozenOrderedSet[Address])

### 3. File Discovery
**Problem**: Files not found because we don't know source roots
**Solution**: Use glob pattern `**/path` to find files anywhere in project

### 4. Resolve Conflicts (Critical Issue)
**Problem**: Finding both java17 and java21 targets for same file, causing NoCompatibleResolve errors

**Attempted Solutions**:
- L `resolve_targets(**implicitly(tuple(owners)))` - IntrinsicError at rule compile time
- L `Get(WrappedTarget, Address, addr)` - Missing rules in rule graph

**Working Solution**: String matching on addresses to filter by resolve
```python
if f":../../{my_resolve}" in str(addr) or f":{my_resolve}" in str(addr):
    matching_owners.append(addr)
```

### 5. Disambiguation
**Problem**: Multiple targets could satisfy a dependency
**Solution**: Use `ExplicitlyProvidedDependencies.disambiguated()` with warning for ambiguous cases

## Test Results

All tests passing with automatic dependency inference:

```
 projects/example/project-a/test/example/project_a/core_test.clj:../../java17 (tests: 1, failures: 0)
 projects/example/project-a/test/example/project_a/core_test.clj:../../java21 (tests: 1, failures: 0)
 projects/example/project-b/test/example/project_b/core_test.clj:../../java17 (tests: 2, failures: 0)
 projects/example/project-b/test/example/project_b/core_test.clj:../../java21 (tests: 2, failures: 0)
```

**Key Achievement**: No manual dependency specifications needed in BUILD files for first-party dependencies!

## Example

### Source File (project-b/src/core.clj)
```clojure
(ns example.project-b.core
  (:require [example.project-a.core :as project-a]))

(defn use-project-a []
  (str "Project B using: " project-a/thing))
```

### BUILD File (project-b/src/BUILD)
```python
clojure_sources(
    name="java17",
    resolve="java17",
    sources=["**/*.clj"],
    # No dependencies specified! Automatically inferred.
)
```

### How It Works
1. Parse `core.clj` and extract `example.project-a.core` from `:require`
2. Convert namespace to path: `example/project_a/core.clj`
3. Find owners of `**/example/project_a/core.clj`
4. Filter to java17 resolve: `projects/example/project-a/src/example/project_a/core.clj:../../java17`
5. Add inferred dependency automatically

## Future Improvements

### High Priority
1. **Proper Resolve Filtering**: Use actual target field inspection instead of string matching
2. **Better Parser**: Consider using proper EDN parser library (e.g., `edn_format`)

### Medium Priority
3. **Pre-built Mapping**: Build namespace�address map for better performance at scale
4. **Third-Party Inference**: Map common Clojure namespaces to Maven artifacts
   - `clojure.test` � `org.clojure:clojure`
   - `clojure.string` � `org.clojure:clojure`
   - etc.

### Low Priority
5. **Caching**: Cache parsed namespaces to avoid re-parsing
6. **Better error messages**: More helpful diagnostics when inference fails

## Lessons Learned

1. **Pants patterns are consistent**: InferDependenciesRequest pattern works the same across languages
2. **Resolve handling is tricky**: JVM resolve conflicts require careful filtering
3. **String matching on addresses works but is brittle**: Should be improved with proper introspection
4. **On-demand lookup is simpler**: Pre-built mapping adds complexity that may not be needed yet
5. **Regex parsing is "good enough"**: More sophisticated parsing can wait until it becomes a problem
