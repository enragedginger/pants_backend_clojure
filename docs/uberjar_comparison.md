# Uberjar Creation: Comparison with Leiningen and tools.build

This document explains how the pants-clojure plugin creates uberjars and how our approach compares to Leiningen and tools.build.

## Overview

All three tools follow a similar high-level process:

1. AOT compile Clojure namespaces to `.class` files
2. Combine compiled classes with dependency JARs
3. Package everything into a single executable JAR

The key differences are in how each tool handles **third-party classes** generated during AOT compilation.

## The AOT Compilation Challenge

When you AOT compile a Clojure namespace, the compiler **transitively compiles all required namespaces**. This is unavoidable - it's how Clojure's compiler works.

For example, compiling `my.app.core` will also compile:
- `clojure.core`
- `clojure.string`
- Any third-party libraries you require
- All their transitive dependencies

This creates a problem: you now have two copies of third-party classes:
1. **AOT-generated copies** in your compile output
2. **Original copies** in the dependency JARs

Using the wrong copy can cause **protocol identity issues** - the dreaded "No implementation of method" errors that occur when protocol classes don't match between definition and extension sites.

## How Each Tool Handles This

### Leiningen

Leiningen builds uberjars in two steps:

1. **Create project JAR**: Contains only your project's AOT-compiled classes (from `:compile-path`)
2. **Merge with dependency JARs**: Extracts all dependency JARs into the uberjar

```
┌─────────────────────────────────────────────────────┐
│                     Uberjar                         │
├─────────────────────────────────────────────────────┤
│  Your AOT classes     │  From project JAR           │
│  (my/app/core.class)  │  (your compile-path)        │
├───────────────────────┼─────────────────────────────┤
│  Third-party classes  │  From dependency JARs       │
│  (clojure/core.class) │  (extracted and merged)     │
└─────────────────────────────────────────────────────┘
```

**Key behavior**: By default, Leiningen includes **all** AOT output in the project JAR - it does NOT filter first-party vs third-party classes. Third-party AOT classes are only discarded because the dependency JARs are merged afterward, and the merge order means JAR contents overwrite AOT output.

**Optional filtering**: Leiningen has a `:clean-non-project-classes` option (off by default) that deletes non-project classes after AOT compilation. However, this filtering uses **directory structure matching**, not source analysis:

```clojure
;; From compile.clj - checks if package directory exists in source-paths
(defn- package-in-project?
  [found-path compile-path source-path]
  (.isDirectory (io/file (.replace found-path compile-path source-path))))
```

**Important limitation**: This approach breaks for custom `(:gen-class :name)` declarations. If you have:

```clojure
(ns my.app (:gen-class :name com.example.CustomMain))
```

And `:clean-non-project-classes` is enabled, Leiningen checks if `com/example/` exists in your source paths. If it doesn't (because your namespace is `my.app`, not `com.example`), **the class gets incorrectly deleted**.

### tools.build

tools.build's `uber` function combines:
- Sources from `:paths`
- Compiled classes from `:class-dir`
- All dependency JARs from the basis

```clojure
(b/uber {:class-dir "target/classes"
         :uber-file "target/app.jar"
         :basis basis})
```

**Key behavior**: When there are conflicts (same file in class-dir and a JAR), the default strategy is `:ignore` - first one wins. This means:
- If class-dir is processed first, AOT'd third-party classes override JAR classes
- This can cause protocol identity issues if not careful

tools.build provides conflict handlers (`:ignore`, `:overwrite`, `:warn`, `:error`) but the default behavior can be problematic.

### pants-clojure

Our plugin takes a more conservative approach:

1. **AOT compile** the main namespace (transitively compiles everything)
2. **Filter AOT output** to keep only first-party classes
3. **Extract dependency JARs** for all third-party content

```
┌─────────────────────────────────────────────────────┐
│                     Uberjar                         │
├─────────────────────────────────────────────────────┤
│  Your AOT classes     │  From AOT output            │
│  (my/app/core.class)  │  (filtered to first-party)  │
├───────────────────────┼─────────────────────────────┤
│  Third-party classes  │  From dependency JARs       │
│  (clojure/core.class) │  (AOT copies discarded)     │
└─────────────────────────────────────────────────────┘
```

**Key behavior**: We explicitly discard third-party AOT classes and always use JAR contents. This guarantees protocol identity consistency.

**First-party detection**: Unlike Leiningen's directory-based approach, we use **source analysis** to determine first-party classes:

1. **Namespace paths**: Classes matching namespaces from `clojure_source` targets (e.g., `my/app/core__init.class`)
2. **gen-class :name detection**: We parse source files for `(:gen-class :name X)` patterns and include those custom-named classes

This means custom gen-class names work correctly:

```clojure
(ns my.app
  (:gen-class :name com.example.CustomMain))  ; Correctly included!
```

The class `com/example/CustomMain.class` is included even though `com.example` doesn't exist as a source directory.

## Comparison Table

| Aspect | Leiningen | tools.build | pants-clojure |
|--------|-----------|-------------|---------------|
| AOT compiles transitively | Yes | Yes | Yes |
| Third-party in final JAR | Yes | Yes | Yes |
| Source of third-party classes | Dependency JARs | Class-dir or JARs (configurable) | Dependency JARs only |
| Third-party AOT classes | Implicitly discarded | Kept (may override JARs) | Explicitly discarded (if in JAR) |
| Protocol safety | Safe | Depends on config | Safe by default |
| Conflict resolution | Last wins (JAR merge order) | Configurable strategies | First-party AOT, then JARs |
| First-party detection | Directory structure | N/A (includes all) | Source analysis |
| Custom gen-class :name | Broken if filtering enabled | Works (no filtering) | Works (source analysis) |
| Macro-generated classes | Works (merge order side-effect) | Works if class-dir first | Works (JAR contents check) |
| Source files included | Yes (`:omit-source` to exclude) | Yes (user decides) | Conditional (excluded for AOT'd source-only libs) |
| Source-only lib handling | No special handling | No special handling | Auto-detects, excludes source to prevent class identity issues |

## Source File Handling

A key difference between the tools is how they handle `.clj`/`.cljc` source files in uberjars.

### Leiningen

**Default: Include source files**

By default, Leiningen includes both compiled `.class` files AND source `.clj` files in uberjars:

```clojure
;; From jar.clj - adds both compile-path and source-paths
[{:type :path :path (:compile-path project)}
 {:type :paths :paths (distinct (:resource-paths project))}]
 (if-not (:omit-source project)
   [{:type :paths :paths (distinct (concat (:source-paths project) ...))}])
```

**Optional exclusion**: Projects can set `:omit-source true` to exclude source files:

```clojure
;; project.clj
{:omit-source true}  ; Leave source files out of JARs (for AOT projects)
```

**Documentation warning**: The sample.project.clj warns: "Putting :all here will AOT-compile everything, but this can cause issues with certain uses of protocols and records."

### tools.build

**Default: Include everything**

tools.build takes a "simple, inclusive approach" - both compiled classes and source files are included. The typical workflow:

```clojure
(b/compile-clj {:basis basis :class-dir "target/classes" ...})
(b/copy-dir {:target-dir "target/classes" :src-dirs ["src"]})  ; Source copied!
(b/uber {:class-dir "target/classes" :uber-file "app.jar" :basis basis})
```

Tests explicitly show both sources and classes in the final JAR:
```clojure
;; Expected contents
#{"META-INF/MANIFEST.MF" "foo/" "foo/bar.clj" "foo/Demo2.class" "foo/Demo1.class"}
```

**User responsibility**: The build script author decides what to include/exclude. There's no built-in logic to handle source-only libraries or protocol issues.

### pants-clojure

**Default: Conditional based on library type**

We take a more nuanced approach:

| Library Type | Classes | Source Files |
|--------------|---------|--------------|
| Pre-compiled (has .class in JAR) | From JAR | From JAR |
| Source-only (no .class in JAR) | From AOT | **Excluded** |
| First-party | From AOT | Excluded (compiled) |

**Why exclude source for source-only libraries?**

When both AOT-compiled classes AND source files are present, Clojure might reload source at runtime (e.g., via `require :reload` or certain macro patterns). This creates fresh class instances that don't match the class identities baked into first-party AOT code, causing errors like:

```
No implementation of method: :implicit-nav of protocol:
#'com.rpl.specter.protocols/ImplicitNav found for class: com.rpl.specter.impl.DynamicPath
```

By excluding source files for source-only libraries when we have AOT classes, we ensure consistent class identities throughout the application.

### Comparison Table: Source File Handling

| Aspect | Leiningen | tools.build | pants-clojure |
|--------|-----------|-------------|---------------|
| Include source by default | Yes | Yes | Conditional |
| `:omit-source` option | Yes | Manual (don't copy) | Automatic for source-only libs |
| Protocol issue handling | Documentation warning | User responsibility | Automatic exclusion |
| Source-only lib detection | No | No | Yes |

## Macro-Generated Classes

Some Clojure macros generate classes in the **macro's namespace** rather than the calling namespace. For example:

- **Specter's `declarepath`** generates `com.rpl.specter.impl$local_declarepath`
- **core.async's `go`** generates state machine classes in `clojure.core.async.impl`
- **core.match** generates pattern matching classes in its impl namespace

These classes don't exist in the original library JAR - they're only created during AOT compilation of YOUR code that uses the macro.

### How Each Tool Handles This

| Tool | Behavior | Why It Works (or Doesn't) |
|------|----------|---------------------------|
| **Leiningen** | Works | AOT classes are added first, JARs merged after. Since no JAR contains the macro-generated class, nothing overwrites it. Works by accident of merge order. |
| **tools.build** | Usually works | With default `:ignore` conflict strategy and class-dir processed first, AOT classes survive. But behavior depends on configuration. |
| **pants-clojure** | Works | We explicitly check if each AOT class exists in any dependency JAR. If not found in any JAR, we keep it from AOT output. Intentional, not accidental. |

### Our Approach

```
For each AOT-generated class:
  1. Is it first-party (matches our source namespaces)? → Keep from AOT
  2. Does it exist in any dependency JAR? → Discard, use JAR version
  3. Not in any JAR? → Keep from AOT (it's macro-generated)
```

This gives us the best of both worlds:
- **Protocol safety**: Third-party classes that exist in JARs come from JARs
- **Macro support**: Classes that don't exist anywhere except AOT output are kept

## Why Our Approach is Safer

### Protocol Identity Issues

Clojure protocols rely on JVM class identity. When you extend a protocol:

```clojure
(extend-protocol MyProtocol
  SomeRecord
  (my-method [x] ...))
```

The `SomeRecord` class must be the exact same class object at both the protocol definition site and the extension site. If you have two different `SomeRecord.class` files (one from AOT, one from a JAR), they're different classes to the JVM, and the protocol extension won't work.

**Example error:**
```
java.lang.IllegalArgumentException: No implementation of method: :my-method
of protocol: #'some.lib/MyProtocol found for class: some.lib.SomeRecord
```

### Our Solution

By always using JAR classes for third-party code, we ensure:

1. **Single source of truth**: Each third-party class comes from exactly one place (its JAR)
2. **Consistent identity**: Protocol classes match between definition and extension
3. **No ordering issues**: Unlike tools.build's conflict resolution, we don't depend on processing order

## AOT Modes

### Standard Mode (AOT from main)

```python
clojure_deploy_jar(
    name="my-app",
    main="my.app.core",  # AOT compiled transitively
    dependencies=[...],
)
```

- Main namespace must have `(:gen-class)`
- All required namespaces are compiled transitively
- First-party classes included from AOT
- Third-party classes included from JARs
- JAR is directly executable: `java -jar app.jar`

### Source-Only Mode (no AOT)

```python
clojure_deploy_jar(
    name="my-app",
    main="clojure.main",  # Special: skip AOT
    dependencies=[...],
)
```

- No AOT compilation performed
- All code compiles at runtime
- Slower startup (10-30+ seconds)
- Run with: `java -jar app.jar -m my.actual.namespace`
- Useful for libraries with AOT compatibility issues

## Provided Dependencies

All three tools support "provided" dependencies (available at compile time, excluded from JAR):

| Tool | Mechanism |
|------|-----------|
| Leiningen | Profile with `^{:pom-scope :provided}` metadata |
| tools.build | Separate basis with alias that excludes deps |
| pants-clojure | `provided` field with coordinate-based matching |

Our implementation:

```python
clojure_deploy_jar(
    name="my-app",
    main="my.app.core",
    dependencies=[":handler", "//3rdparty:servlet-api"],
    provided=["//3rdparty:servlet-api"],  # Excluded from JAR
)
```

- Provided deps are available during AOT compilation
- Provided deps (and their Maven transitives) are excluded from the JAR
- Matching is based on Maven coordinates (groupId:artifactId), version ignored

## Direct Linking

We enable Clojure's direct linking during AOT compilation:

```clojure
(binding [*compiler-options* {:direct-linking true}]
  (compile 'my.app.core))
```

This matches tools.build best practices and provides:
- Faster startup times
- Reduced var dereferencing overhead
- Smaller class files

**Trade-off**: Runtime var redefinition won't affect already-compiled call sites. Use `^:redef` metadata for vars that need runtime redefinition.

## Summary

Our uberjar approach prioritizes **correctness and safety** over flexibility:

1. **Protocol-safe by default**: Third-party classes always come from JARs
2. **Simple mental model**: First-party = AOT, third-party = JARs
3. **No configuration needed**: Safe behavior without conflict resolution tuning
4. **Compatible with ecosystem**: Works with all Clojure libraries, including those with protocol extensions

This makes pants-clojure uberjars reliable for production use without needing to understand the nuances of AOT compilation and class identity.
