# Dependency Inference Improvements Plan

**Date:** October 14, 2025
**Status:** Planning

## Overview

Plan for improving Clojure dependency inference in the Pants backend, focusing on adding `:import` support for Java/JVM class dependencies.

## Current State

 **Implemented:**
- `:require` form parsing for first-party Clojure dependencies
- `:use` form parsing for first-party Clojure dependencies
- Namespace ’ file path resolution
- Resolve filtering (java17 vs java21)
- Automatic dependency inference for Clojure-to-Clojure references

L **Not Implemented:**
- `:import` form parsing for Java class dependencies
- First-party Java class ’ source file resolution
- Third-party Java class ’ Maven artifact resolution
- ClojureScript support
- Pre-built namespace mapping
- Proper EDN parser

## Priority Improvements

### 1. `:import` Support (High Priority)

Enable automatic dependency inference for Java class imports in Clojure code.

#### Use Cases

**First-Party Java:**
```clojure
(ns example.clojure-code
  (:import [com.enragedginger.java_project SomeJava]))

;; Should automatically infer dependency on:
;; projects/example/java-project/src/com/enragedginger/java_project/SomeJava.java
```

**Third-Party Java:**
```clojure
(ns example.json-user
  (:import [com.fasterxml.jackson.databind ObjectMapper]))

;; Should automatically infer dependency on:
;; Maven artifact com.fasterxml.jackson.core:jackson-databind
```

**JDK Classes:**
```clojure
(ns example.file-processor
  (:import [java.util Date ArrayList HashMap]
           [java.io File InputStream]))

;; JDK classes are implicit, no dependency needed
```

---

## Implementation Plan for `:import` Support

### Phase 1: Research Existing Pants JVM Infrastructure

**Goal:** Understand how Pants' Java/Scala backends handle import ’ dependency inference

**Tasks:**
1. Search Pants codebase for Java dependency inference implementation
   - Look in `/Users/hopper/workspace/python/pants/src/python/pants/jvm/`
   - Search for terms: "import inference", "symbol analysis", "class mapping"
2. Find APIs for mapping class names to artifacts
   - `JvmClasspathEntries`
   - `CoursierResolvedLockfile`
   - `SymbolMap` or similar
3. Understand first-party vs third-party handling
4. Identify reusable components

**Key Questions:**
- How does Java backend map `import com.example.Foo` to a dependency?
- Is there a symbol index built from jar files?
- Can we query "what artifact provides class X"?
- How does resolve filtering work for JVM artifacts?
- What APIs are available for querying lockfiles?

**Deliverable:** Document findings in this file with links to relevant Pants code

---

### Phase 2: Parsing `:import` Forms

**Goal:** Extract Java class names from `:import` clauses in Clojure source files

#### Syntax to Handle

**Vector syntax (most common):**
```clojure
(ns example.foo
  (:import [java.util Date ArrayList HashMap]
           [java.io File InputStream OutputStream]
           [com.example.custom MyClass AnotherClass]))
```

**Single-class syntax:**
```clojure
(ns example.bar
  (:import java.util.Date
           java.io.File))
```

**Mixed syntax:**
```clojure
(ns example.baz
  (:import java.util.Date
           [java.io File Reader Writer]))
```

#### Implementation

```python
def parse_clojure_imports(source_content: str) -> set[str]:
    """Extract Java class imports from :import forms.

    Handles both vector and single-class import syntax.

    Example:
        (ns example.foo
          (:import [java.util Date ArrayList]
                   [java.io File]))

        Returns: {"java.util.Date", "java.util.ArrayList", "java.io.File"}

    Args:
        source_content: The Clojure source file content

    Returns:
        Set of fully-qualified Java class names
    """
    imported_classes = set()

    # Find the ns form
    ns_match = re.search(r'\(ns\s+[\w\.\-]+\s*(.*?)(?=\n\(|\Z)', source_content, re.DOTALL)
    if not ns_match:
        return imported_classes

    ns_body = ns_match.group(1)

    # Find :import section
    import_match = re.search(r'\(:import\s+(.*?)(?=\(:|$)', ns_body, re.DOTALL)
    if not import_match:
        return imported_classes

    import_body = import_match.group(1)

    # TODO: Implement parsing logic for:
    # 1. Vector syntax: [java.util Date ArrayList]
    # 2. Single-class syntax: java.util.Date
    # 3. Extract package and class names
    # 4. Build fully-qualified class names

    return imported_classes


def class_to_path(class_name: str) -> str:
    """Convert a Java class name to its expected file path.

    Example:
        "com.example.Foo" -> "com/example/Foo.java"
        "com.example.inner.Bar" -> "com/example/inner/Bar.java"

    Note: Does not handle inner classes (e.g., Map$Entry) - those are
    defined in the outer class file.
    """
    # Handle inner classes by taking only the outer class
    if '$' in class_name:
        class_name = class_name.split('$')[0]

    path = class_name.replace('.', '/')
    return f"{path}.java"
```

#### Edge Cases to Handle

1. **Inner classes:**
   ```clojure
   (:import [java.util Map$Entry])
   ```
   Maps to `java/util/Map.java` (the outer class file)

2. **Nested packages:**
   ```clojure
   (:import [java.util.concurrent.atomic AtomicInteger AtomicLong])
   ```

3. **Arrays (if used):**
   ```clojure
   (:import [java.lang String])  ; String[] is implicit
   ```

4. **Wildcards:** Check if Clojure supports these (likely not)

#### Testing

```python
def test_parse_clojure_imports_vector_syntax():
    source = "(ns foo (:import [java.util Date ArrayList]))"
    assert parse_clojure_imports(source) == {
        "java.util.Date",
        "java.util.ArrayList"
    }

def test_parse_clojure_imports_single_class():
    source = "(ns foo (:import java.util.Date java.io.File))"
    assert parse_clojure_imports(source) == {
        "java.util.Date",
        "java.io.File"
    }

def test_parse_clojure_imports_mixed():
    source = """(ns foo
      (:import java.util.Date
               [java.io File Reader]))"""
    assert parse_clojure_imports(source) == {
        "java.util.Date",
        "java.io.File",
        "java.io.Reader"
    }

def test_class_to_path():
    assert class_to_path("com.example.Foo") == "com/example/Foo.java"
    assert class_to_path("java.util.HashMap") == "java/util/HashMap.java"
    assert class_to_path("java.util.Map$Entry") == "java/util/Map.java"
```

---

### Phase 3: First-Party Java Dependencies

**Goal:** Resolve imported Java classes to first-party Java source files in the repo

#### Approach

Similar to Clojure namespace inference, but for Java files:

1. **Convert class name to file path:**
   - `com.example.Foo` ’ `com/example/Foo.java`
   - Handle inner classes: `Map$Entry` ’ `Map.java`

2. **Use `OwnersRequest` with glob pattern:**
   - Try `**/com/example/Foo.java`
   - Handles unknown source roots automatically

3. **Filter by resolve:**
   - Use same string matching as for Clojure: `f":../../{my_resolve}"`
   - Ensures java17 Clojure code doesn't depend on java21 Java code

4. **Add matched targets as dependencies:**
   - Use `ExplicitlyProvidedDependencies.disambiguated()` if multiple matches

#### Implementation Sketch

```python
async def infer_clojure_source_dependencies(...):
    # ... existing namespace parsing ...

    # NEW: Parse imports
    imported_classes = parse_clojure_imports(source_content)

    for class_name in imported_classes:
        # Skip JDK classes (implicit in classpath)
        if is_jdk_class(class_name):
            continue

        # Try to find first-party Java source
        java_file_path = class_to_path(class_name)
        owners = await Get(Owners, OwnersRequest((f"**/{java_file_path}",)))

        if owners:
            # Found first-party Java class
            my_resolve = request.field_set.resolve.normalized_value(jvm)

            # Filter by resolve
            matching_owners = []
            for addr in owners:
                if f":../../{my_resolve}" in str(addr) or f":{my_resolve}" in str(addr):
                    matching_owners.append(addr)

            candidates = tuple(matching_owners) if matching_owners else owners

            # Disambiguate if needed
            explicitly_provided_deps.maybe_warn_of_ambiguous_dependency_inference(
                candidates,
                request.field_set.address,
                import_reference="class",
                context=f"The target {request.field_set.address} imports `{class_name}`",
            )
            maybe_disambiguated = explicitly_provided_deps.disambiguated(candidates)
            if maybe_disambiguated:
                dependencies.add(maybe_disambiguated)
        else:
            # Not first-party, will be handled in Phase 4 (third-party)
            pass

    return InferredDependencies(sorted(dependencies))


def is_jdk_class(class_name: str) -> bool:
    """Check if a class is part of the JDK (implicit dependency).

    JDK packages include:
    - java.*
    - javax.*
    - sun.* (though discouraged)
    - jdk.* (JDK 9+ modules)
    """
    jdk_prefixes = ("java.", "javax.", "sun.", "jdk.")
    return any(class_name.startswith(prefix) for prefix in jdk_prefixes)
```

#### Example Test Case

**Setup:**
```clojure
// File: projects/example/java-project/src/com/enragedginger/java_project/Helper.java
package com.enragedginger.java_project;

public class Helper {
    public static String greet() { return "Hello"; }
}
```

```clojure
;; File: projects/example/project-a/src/example/project_a/java_user.clj
(ns example.project-a.java-user
  (:import [com.enragedginger.java_project Helper]))

(defn use-helper []
  (Helper/greet))
```

**Expected Result:**
```bash
$ pants dependencies projects/example/project-a/src/example/project_a/java_user.clj:../../java17
projects/example/java-project/src/com/enragedginger/java_project/Helper.java:../../../java17
```

---

### Phase 4: Third-Party JVM Dependencies

**Goal:** Resolve imported Java classes to Maven artifacts in the lockfile

This is the most complex part, requiring integration with Pants' JVM artifact resolution.

#### Challenge

Given a class name like `com.fasterxml.jackson.databind.ObjectMapper`, we need to:
1. Determine which Maven artifact provides this class
2. Find the corresponding `jvm_artifact` target in our BUILD files
3. Ensure the artifact matches our resolve
4. Add it as a dependency

#### Possible Approaches

**Option A: Use Pants' Existing Symbol Analysis**

Pants likely has infrastructure for this already. Look for:
- `JvmClasspathEntries` - represents classpath with metadata
- `CoursierResolvedLockfile` - parsed lockfile with artifact info
- Symbol tables or class’artifact mappings
- Existing rules in Java/Scala backends

**Advantages:**
- Reuses existing, tested infrastructure
- Handles resolves correctly
- Works with any artifact in the lockfile

**Disadvantages:**
- Need to find and understand the APIs
- May require additional rule plumbing

**Option B: Build Custom Mapping**

Maintain a static mapping of common packages to artifacts:
```python
COMMON_JAVA_PACKAGES = {
    "org.clojure": "org.clojure:clojure",
    "com.fasterxml.jackson.databind": "com.fasterxml.jackson.core:jackson-databind",
    "com.fasterxml.jackson.core": "com.fasterxml.jackson.core:jackson-core",
    "com.google.common": "com.google.guava:guava",
    # ... etc
}
```

**Advantages:**
- Simple to implement
- Fast lookups
- No dependency on Pants internals

**Disadvantages:**
- Incomplete coverage
- Requires manual maintenance
- Doesn't scale to all artifacts
- Doesn't handle custom/private artifacts

**Option C: Hybrid Approach**

1. Check static mapping first (common packages)
2. Fall back to Pants API for unknown packages
3. Provide warning/error for unmapped classes

**Option D: Parse Lockfiles Directly**

Read the `.lock` files and build a package’artifact index:
```python
# Parse 3rdparty/jvm/default.lock
# For each artifact, note what packages it likely provides
# (heuristic: artifact name matches package prefix)
```

**Advantages:**
- Works with any lockfile
- No reliance on Pants internals

**Disadvantages:**
- Heuristic-based (not always accurate)
- Need to parse lockfile format
- Doesn't know actual classes in jars

#### Recommended Approach

**Start with Option A (Pants APIs)** - most robust if available
**Fall back to Option B (static mapping)** - for common cases if APIs are insufficient
**Document gaps** - identify where manual dependencies are needed

#### Research Questions for Phase 1

1. Does Pants build a symbol index from jar files?
2. Is there an API to query "what artifact provides class X"?
3. How do Java/Scala backends handle third-party imports?
4. Can we access the resolved classpath with artifact metadata?
5. Is there a way to query lockfiles for artifact contents?

#### Implementation Sketch (pending research)

```python
async def resolve_third_party_java_class(
    class_name: str,
    resolve: str,
) -> Address | None:
    """Resolve a Java class name to a third-party artifact.

    Args:
        class_name: Fully-qualified class name (e.g., "com.example.Foo")
        resolve: The JVM resolve name (e.g., "java17")

    Returns:
        Address of the jvm_artifact target, or None if not found
    """
    # TODO: Implement based on Phase 1 research
    #
    # Possible approaches:
    # 1. Query Pants symbol table
    # 2. Check static mapping
    # 3. Parse lockfile
    # 4. Use Coursier API

    pass
```

---

### Phase 5: Integration and Testing

#### Updated Inference Flow

```python
@rule(desc="Infer Clojure source dependencies", level=LogLevel.DEBUG)
async def infer_clojure_source_dependencies(
    request: InferClojureSourceDependencies,
    jvm: JvmSubsystem,
) -> InferredDependencies:
    """Infer dependencies for a Clojure source file.

    Analyzes:
    - :require forms -> first-party Clojure namespaces
    - :use forms -> first-party Clojure namespaces
    - :import forms -> first-party Java classes and third-party JVM artifacts
    """

    explicitly_provided_deps, source_files = await MultiGet(
        Get(ExplicitlyProvidedDependencies, DependenciesRequest(request.field_set.dependencies)),
        Get(SourceFiles, SourceFilesRequest([request.field_set.source])),
    )

    digest_contents = await Get(DigestContents, Digest, source_files.snapshot.digest)
    if not digest_contents:
        return InferredDependencies([])

    source_content = digest_contents[0].content.decode('utf-8')
    dependencies: OrderedSet[Address] = OrderedSet()

    # 1. Handle Clojure namespaces (:require, :use)
    required_namespaces = parse_clojure_requires(source_content)
    for namespace in required_namespaces:
        # ... existing logic ...
        pass

    # 2. Handle Java imports (:import)
    imported_classes = parse_clojure_imports(source_content)
    my_resolve = request.field_set.resolve.normalized_value(jvm)

    for class_name in imported_classes:
        # Skip JDK classes
        if is_jdk_class(class_name):
            continue

        # Try first-party Java
        java_file_path = class_to_path(class_name)
        owners = await Get(Owners, OwnersRequest((f"**/{java_file_path}",)))

        if owners:
            # First-party Java class found
            matching_owners = [
                addr for addr in owners
                if f":../../{my_resolve}" in str(addr) or f":{my_resolve}" in str(addr)
            ]
            candidates = tuple(matching_owners) if matching_owners else owners

            explicitly_provided_deps.maybe_warn_of_ambiguous_dependency_inference(
                candidates,
                request.field_set.address,
                import_reference="class",
                context=f"The target {request.field_set.address} imports `{class_name}`",
            )
            maybe_disambiguated = explicitly_provided_deps.disambiguated(candidates)
            if maybe_disambiguated:
                dependencies.add(maybe_disambiguated)
        else:
            # Try third-party artifact
            artifact_addr = await resolve_third_party_java_class(class_name, my_resolve)
            if artifact_addr:
                dependencies.add(artifact_addr)
            # else: Class not found anywhere (user needs explicit dependency)

    return InferredDependencies(sorted(dependencies))
```

#### Test Cases

**Test 1: First-party Java import**
```python
def test_infer_first_party_java_import(rule_runner: RuleRunner):
    rule_runner.write_files({
        "java/BUILD": "java_sources(name='java', sources=['**/*.java'])",
        "java/com/example/Helper.java": "package com.example; public class Helper {}",
        "clj/BUILD": "clojure_sources(name='clj', sources=['**/*.clj'])",
        "clj/user.clj": "(ns user (:import [com.example Helper]))",
    })

    deps = rule_runner.request(
        InferredDependencies,
        [InferClojureSourceDependencies(...)]
    )

    assert "java/com/example/Helper.java" in [str(d) for d in deps]
```

**Test 2: Third-party import (if supported)**
```python
def test_infer_third_party_java_import(rule_runner: RuleRunner):
    rule_runner.write_files({
        "3rdparty/jvm/BUILD": """
            jvm_artifact(
                name="jackson",
                group="com.fasterxml.jackson.core",
                artifact="jackson-databind",
                version="2.12.4",
            )
        """,
        "clj/BUILD": "clojure_sources(name='clj', sources=['**/*.clj'])",
        "clj/json.clj": "(ns json (:import [com.fasterxml.jackson.databind ObjectMapper]))",
    })

    deps = rule_runner.request(
        InferredDependencies,
        [InferClojureSourceDependencies(...)]
    )

    assert "3rdparty/jvm:jackson" in [str(d) for d in deps]
```

**Test 3: JDK classes (should be filtered)**
```python
def test_jdk_imports_not_added(rule_runner: RuleRunner):
    rule_runner.write_files({
        "clj/BUILD": "clojure_sources(name='clj', sources=['**/*.clj'])",
        "clj/dates.clj": "(ns dates (:import [java.util Date ArrayList]))",
    })

    deps = rule_runner.request(
        InferredDependencies,
        [InferClojureSourceDependencies(...)]
    )

    # JDK classes should not appear in dependencies
    assert len(deps) == 0
```

**Test 4: Mixed requires and imports**
```python
def test_mixed_require_and_import(rule_runner: RuleRunner):
    rule_runner.write_files({
        "clj/BUILD": "clojure_sources(name='clj', sources=['**/*.clj'])",
        "clj/foo.clj": "(ns foo)",
        "clj/bar.clj": "(ns bar (:require [foo]) (:import [java.io File]))",
    })

    deps = rule_runner.request(
        InferredDependencies,
        [InferClojureSourceDependencies(...)]
    )

    # Should infer foo.clj but not java.io.File
    assert "clj/foo.clj" in [str(d) for d in deps]
    assert len(deps) == 1
```

---

### Phase 6: Special Cases and Polish

#### JDK Class Handling

**Standard packages to filter:**
- `java.*` (java.lang, java.util, java.io, etc.)
- `javax.*` (javax.swing, javax.sql, etc.)
- `sun.*` (internal, discouraged but sometimes used)
- `jdk.*` (JDK 9+ modules)

**Implementation:**
```python
JDK_PACKAGE_PREFIXES = frozenset([
    "java.",
    "javax.",
    "sun.",
    "jdk.",
])

def is_jdk_class(class_name: str) -> bool:
    """Check if a class is part of the JDK."""
    return any(class_name.startswith(prefix) for prefix in JDK_PACKAGE_PREFIXES)
```

#### Clojure Standard Library Classes

Classes in `clojure.lang.*` are part of the Clojure runtime:
```clojure
(:import [clojure.lang IFn Keyword Symbol])
```

These are provided by `org.clojure:clojure`, which should already be in dependencies via lockfile. Consider:
1. Treating as implicit (like JDK)
2. Or mapping to the Clojure artifact explicitly

**Recommendation:** Treat as implicit since Clojure sources always depend on the Clojure runtime.

#### Inner Classes

```clojure
(:import [java.util Map$Entry])
```

Inner classes are defined in the outer class file:
- `Map$Entry` ’ `Map.java`
- Strip everything after `$` when converting to file path

#### Resolve Conflicts

Must respect JVM resolves:
- java17 Clojure code should only depend on java17 Java code/artifacts
- java21 Clojure code should only depend on java21 Java code/artifacts

Use the same string matching approach as for namespaces:
```python
if f":../../{my_resolve}" in str(addr) or f":{my_resolve}" in str(addr):
    matching_owners.append(addr)
```

**Future improvement:** Use proper target field inspection instead of string matching.

#### Error Handling

When a class cannot be resolved:
1. **First-party not found:** Check if it's a typo or missing source file
2. **Third-party not found:** May need explicit dependency or artifact not in lockfile
3. **Provide helpful error message:**
   ```
   Could not infer dependency for imported class 'com.example.Missing'

   If this is a first-party Java class, ensure the source file exists.
   If this is a third-party class, add an explicit dependency in your BUILD file:
       dependencies=["//3rdparty/jvm:artifact-name"]
   ```

---

## Implementation Order

### Step 1: Parse `:import` forms
- Implement `parse_clojure_imports()`
- Unit tests for various syntaxes
- **Estimated effort:** 2-3 hours

### Step 2: Research Pants Java infrastructure
- Search Pants codebase for Java dependency inference
- Document APIs and approaches
- **Estimated effort:** 2-4 hours

### Step 3: Implement first-party Java support
- Add import parsing to inference rules
- Implement class’file path conversion
- Handle resolve filtering
- Integration tests
- **Estimated effort:** 3-4 hours

### Step 4: Implement third-party artifact support (optional)
- Based on Phase 1 research findings
- May use static mapping or Pants APIs
- **Estimated effort:** 4-8 hours (depends on approach)

### Step 5: Testing and polish
- Edge cases (inner classes, etc.)
- Error messages
- Documentation
- **Estimated effort:** 2-3 hours

**Total estimated effort:** 13-22 hours (depending on third-party complexity)

---

## Future Improvements (Beyond `:import`)

### 2. ClojureScript Support
- Handle `.cljs` and `.cljc` files
- Different require conventions
- Google Closure Library imports
- Node.js/browser-specific dependencies

### 3. Pre-built Mapping
- Build namespace’address map upfront for large codebases
- Cache the mapping
- Faster lookups

### 4. Proper EDN Parser
- Replace regex with real parser library
- More robust handling
- Better error messages

### 5. Third-Party Clojure Library Inference
- Map common namespaces to Clojars/Maven artifacts
- `clojure.data.json` ’ `org.clojure:data.json`
- `clojure.tools.logging` ’ `org.clojure:tools.logging`

### 6. Better Resolve Filtering
- Replace string matching with proper target field inspection
- Use Pants APIs to check resolve field directly

### 7. Performance Optimizations
- Cache parsed files
- Batch OwnersRequest calls
- Profile and optimize hot paths

---

## Success Criteria

### Phase 3 Complete (First-Party Java)
-  Clojure files can import first-party Java classes
-  Dependencies are automatically inferred
-  Resolve filtering works correctly
-  Tests pass
-  Example projects demonstrate the feature

### Phase 4 Complete (Third-Party JVM)
-  Clojure files can import third-party Java classes
-  Dependencies on jvm_artifact targets are inferred
-  Works with lockfiles and resolves
-  Tests pass

### Full Success
-  Both first-party and third-party `:import` support
-  Comprehensive test coverage
-  Documentation updated
-  Example projects using Java interop
-  No manual dependency specifications needed for imports

---

## Open Questions

1. **Does Pants have a symbol’artifact API?** Need to research in Phase 1
2. **How do we handle class name collisions?** (e.g., same class in multiple artifacts)
3. **Should we support inner classes explicitly?** Or just outer class file?
4. **What about package-private classes?** Can they be imported?
5. **Do we need to handle Java generics in import parsing?** Probably not (erased at runtime)
6. **Should Clojure classes in `clojure.lang.*` be filtered like JDK classes?**

---

## References

- Current implementation: `pants-plugins/clojure_backend/dependency_inference.py`
- Clojure namespace/import docs: https://clojure.org/reference/namespaces
- Pants JVM backend: `/Users/hopper/workspace/python/pants/src/python/pants/jvm/`
- Java dependency inference (research target): TBD from Phase 1
