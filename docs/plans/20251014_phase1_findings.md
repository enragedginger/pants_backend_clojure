# Phase 1 Research Findings: Pants JVM Infrastructure

**Date:** October 14, 2025
**Status:** ✅ COMPLETE

## Summary

**Pants has a complete symbol mapping infrastructure we can reuse!** We don't need to build our own third-party artifact resolution - it's already done.

## Core APIs We Can Use

### 1. `SymbolMapping`

**Location:** `/Users/hopper/workspace/python/pants/src/python/pants/jvm/dependency_inference/symbol_mapper.py`

**Key API:**
```python
symbol_mapping.addresses_for_symbol(symbol: str, resolve: str) -> FrozenDict[SymbolNamespace, FrozenOrderedSet[Address]]
```

**What it does:**
- Takes a class name (e.g., `"com.fasterxml.jackson.databind.ObjectMapper"`)
- Takes a resolve (e.g., `"java17"`)
- Returns ALL addresses that provide that class (both first-party and third-party)
- Uses trie-based lookup for efficient prefix matching
- Automatically merges first-party and third-party mappings

### 2. `ThirdPartySymbolMapping`

**Location:** `/Users/hopper/workspace/python/pants/src/python/pants/jvm/dependency_inference/artifact_mapper.py`

**What it does:**
- Built automatically from `jvm_artifact` targets in BUILD files
- Uses `JvmArtifactPackagesField` - artifacts declare which packages they provide
- Handles Maven artifacts and lockfiles automatically
- Respects resolves (java17 vs java21)
- No manual configuration needed!

**Example:**
```python
jvm_artifact(
    name="jackson-databind",
    group="com.fasterxml.jackson.core",
    artifact="jackson-databind",
    version="2.12.4",
    resolve="java17",
    # Optionally: packages=["com.fasterxml.jackson.databind.**"]
)
```

The symbol mapping will automatically know that `com.fasterxml.jackson.databind.ObjectMapper` is provided by this artifact.

### 3. `FirstPartySymbolMapping`

**Location:** Also in `artifact_mapper.py`

**What it does:**
- Built from targets with `experimental_provides_types` field
- Also analyzes source files to determine what types they provide
- Maps packages/types to file addresses
- Works for Java, Scala, Kotlin sources

## How Java Does It

From `/Users/hopper/workspace/python/pants/src/python/pants/backend/java/dependency_inference/rules.py`:

```python
@rule(desc="Inferring Java dependencies by source analysis")
async def infer_java_dependencies_via_source_analysis(
    request: InferJavaSourceDependencies,
    symbol_mapping: SymbolMapping,  # ← Injected by Pants!
    jvm: JvmSubsystem,
) -> InferredDependencies:
    # 1. Get the target and parse source
    tgt = await resolve_target(...)
    analysis = await parse_java_source(...)  # Returns JavaSourceDependencyAnalysis

    # 2. Get the target's resolve
    resolve = tgt[JvmResolveField].normalized_value(jvm)

    # 3. For each imported type
    dependencies = OrderedSet()
    for typ in analysis.imports:
        # Query symbol mapping - returns ALL providers (first + third party)
        for matches in symbol_mapping.addresses_for_symbol(typ, resolve).values():
            # Handle ambiguity
            explicitly_provided_deps.maybe_warn_of_ambiguous_dependency_inference(
                matches,
                address,
                import_reference="type",
                context=f"The target {address} imports `{typ}`",
            )

            # Disambiguate if needed
            maybe_disambiguated = explicitly_provided_deps.disambiguated(matches)
            if maybe_disambiguated:
                dependencies.add(maybe_disambiguated)

    return InferredDependencies(dependencies)
```

## Integration Plan for Clojure

### Step 1: Update Inference Rule Signature

Add `SymbolMapping` parameter to our rule:

```python
from pants.jvm.dependency_inference.symbol_mapper import SymbolMapping

@rule(desc="Infer Clojure source dependencies", level=LogLevel.DEBUG)
async def infer_clojure_source_dependencies(
    request: InferClojureSourceDependencies,
    jvm: JvmSubsystem,
    symbol_mapping: SymbolMapping,  # ← ADD THIS
) -> InferredDependencies:
    # ... existing code ...
```

### Step 2: Parse `:import` Forms

```python
# Parse imports from Clojure source
imported_classes = parse_clojure_imports(source_content)

# e.g., returns: {"java.util.Date", "com.example.Helper", "com.fasterxml.jackson.databind.ObjectMapper"}
```

### Step 3: Query Symbol Mapping

```python
my_resolve = request.field_set.resolve.normalized_value(jvm)

for class_name in imported_classes:
    # Skip JDK classes (implicit)
    if is_jdk_class(class_name):
        continue

    # Query symbol mapping - returns ALL providers
    symbol_matches = symbol_mapping.addresses_for_symbol(class_name, my_resolve)

    # Flatten matches from all namespaces
    for matches in symbol_matches.values():
        # Disambiguate if multiple
        explicitly_provided_deps.maybe_warn_of_ambiguous_dependency_inference(
            matches,
            request.field_set.address,
            import_reference="class",
            context=f"The target {request.field_set.address} imports `{class_name}`",
        )

        maybe_disambiguated = explicitly_provided_deps.disambiguated(matches)
        if maybe_disambiguated:
            dependencies.add(maybe_disambiguated)
```

That's it!

## What We DON'T Need to Do

- ❌ Manually parse lockfiles
- ❌ Build our own artifact mappings
- ❌ Distinguish first-party from third-party (SymbolMapping does it)
- ❌ Handle resolve filtering manually (SymbolMapping handles it)
- ❌ Query Coursier or Maven
- ❌ Maintain package→artifact mappings

## Key Benefits

1. **Reuses existing infrastructure** - battle-tested code
2. **Works with any JVM language** - Java, Scala, Kotlin interop for free
3. **Automatic third-party resolution** - just works with lockfiles
4. **Respects resolves** - java17/java21 handled correctly
5. **Handles ambiguity** - uses Pants' standard disambiguation
6. **No configuration needed** - artifacts declare their packages

## Test Strategy

We can test both first-party and third-party imports:

### First-Party Java Import
```clojure
(ns example.clj-code
  (:import [com.enragedginger.java_project SomeJava]))
```

Should resolve to: `projects/example/java-project/src/com/enragedginger/java_project/SomeJava.java:../../../java17`

### Third-Party Import
```clojure
(ns example.json-user
  (:import [com.fasterxml.jackson.databind ObjectMapper]))
```

Should resolve to the `jvm_artifact` target for jackson-databind.

### JDK Import (filtered)
```clojure
(ns example.dates
  (:import [java.util Date ArrayList]))
```

Should NOT add any dependencies (JDK is implicit).

## Next Steps

1. ✅ Phase 1 complete - understand APIs
2. → Phase 2 - implement `parse_clojure_imports()`
3. → Phase 3 - integrate with `SymbolMapping`
4. → Phase 4 - test and polish

## References

- **Symbol mapping:** `/Users/hopper/workspace/python/pants/src/python/pants/jvm/dependency_inference/symbol_mapper.py`
- **Artifact mapping:** `/Users/hopper/workspace/python/pants/src/python/pants/jvm/dependency_inference/artifact_mapper.py`
- **Java inference:** `/Users/hopper/workspace/python/pants/src/python/pants/backend/java/dependency_inference/rules.py`
- **Scala inference:** `/Users/hopper/workspace/python/pants/src/python/pants/backend/scala/dependency_inference/rules.py`
- **JVM target types:** `/Users/hopper/workspace/python/pants/src/python/pants/jvm/target_types.py`
