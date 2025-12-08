# Uberjar Creation: Comparison with Leiningen and tools.build

This document explains how the pants-clojure plugin creates uberjars and how our approach compares to Leiningen and standalone tools.build usage.

## Overview

The pants-clojure plugin **delegates to tools.build** for AOT compilation and uberjar creation. This means we benefit from the same battle-tested code that the Clojure community uses.

All tools follow a similar high-level process:

1. AOT compile Clojure namespaces to `.class` files
2. Combine compiled classes with dependency JARs
3. Package everything into a single executable JAR

The key differences are in how each tool resolves dependencies and sets up the build environment.

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

Our plugin **delegates to tools.build** for AOT compilation and uberjar creation, with Pants handling dependency resolution:

1. **Pants resolves dependencies** using Coursier (no tools.deps resolution needed)
2. **tools.build compiles** the main namespace via `b/compile-clj`
3. **tools.build packages** the uberjar via `b/uber`

```
┌─────────────────────────────────────────────────────┐
│                     Uberjar                         │
├─────────────────────────────────────────────────────┤
│  Your AOT classes     │  From tools.build compile   │
│  (my/app/core.class)  │  (transitively compiled)    │
├───────────────────────┼─────────────────────────────┤
│  Third-party classes  │  From tools.build uber      │
│  (clojure/core.class) │  (from dependency JARs)     │
└─────────────────────────────────────────────────────┘
```

**Key behavior**: By using tools.build, we get the same AOT and uberjar behavior that the Clojure community relies on. Pants provides the classpaths; tools.build does the compilation and packaging.

**Two-classpath approach**: We maintain separate classpaths for compilation vs packaging:
- **Compile classpath**: All dependencies including `provided` (needed for type resolution)
- **Uber classpath**: Runtime dependencies excluding `provided` (what goes in the JAR)

Custom gen-class names work correctly because tools.build handles them natively:

```clojure
(ns my.app
  (:gen-class :name com.example.CustomMain))  ; Correctly included!
```

## Comparison Table

| Aspect | Leiningen | tools.build (standalone) | pants-clojure |
|--------|-----------|--------------------------|---------------|
| AOT compiles transitively | Yes | Yes | Yes (via tools.build) |
| Third-party in final JAR | Yes | Yes | Yes |
| Dependency resolution | Leiningen/Maven | tools.deps | Pants/Coursier |
| AOT/uberjar creation | Leiningen internals | tools.build | tools.build |
| Conflict resolution | Last wins (JAR merge order) | Configurable strategies | tools.build default |
| Custom gen-class :name | Broken if filtering enabled | Works | Works |
| Provided dependencies | Profile-based | Manual alias | Native `provided` field |
| Macro-generated classes | Works | Works | Works |

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

Since we delegate to tools.build, source file handling follows tools.build's behavior. For AOT-compiled JARs, source files are typically not included since the compiled classes are used instead.

For source-only JARs (`main="clojure.main"`), source files ARE included since there's no AOT compilation.

## Macro-Generated Classes

Some Clojure macros generate classes in the **macro's namespace** rather than the calling namespace. For example:

- **Specter's `declarepath`** generates `com.rpl.specter.impl$local_declarepath`
- **core.async's `go`** generates state machine classes in `clojure.core.async.impl`
- **core.match** generates pattern matching classes in its impl namespace

tools.build handles these correctly - the generated classes are included in the uberjar.

## Protocol Identity

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

### How tools.build Handles This

tools.build handles protocol identity correctly by extracting dependency JARs into the uberjar. By delegating to tools.build, we get this behavior automatically.

## AOT Modes

### Standard Mode (AOT from main)

```python
clojure_deploy_jar(
    name="my-app",
    main="my.app.core",  # AOT compiled via tools.build
    dependencies=[...],
)
```

- Main namespace must have `(:gen-class)`
- tools.build compiles main namespace (Clojure transitively compiles required namespaces)
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

Our uberjar approach delegates to **tools.build** for reliability:

1. **Battle-tested**: Uses the same tools.build code that the Clojure community relies on
2. **Simple mental model**: Pants resolves dependencies, tools.build handles AOT and packaging
3. **Native provided support**: First-class `provided` field for compile-only dependencies
4. **Compatible with ecosystem**: Works with all Clojure libraries

This makes pants-clojure uberjars reliable for production use, leveraging the official Clojure tooling.
