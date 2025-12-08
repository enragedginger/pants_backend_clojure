# AOT Compilation in clojure_deploy_jar

This document explains how AOT (Ahead-of-Time) compilation works in the `clojure_deploy_jar` target.

## Overview

When you create a `clojure_deploy_jar`, the plugin delegates AOT compilation and uberjar packaging to **tools.build**, the official Clojure build tool. This provides reliable, battle-tested handling of all AOT compilation complexities.

The main namespace is AOT compiled along with all namespaces it transitively requires, producing an executable JAR that starts quickly without runtime compilation.

## Basic Usage

```python
clojure_deploy_jar(
    name="my-app",
    main="my.app.core",
    dependencies=[":lib"],
)
```

The main namespace must include `(:gen-class)` and define a `-main` function:

```clojure
(ns my.app.core
  (:gen-class))

(defn -main [& args]
  (println "Hello, World!"))
```

**Running the JAR:**
```bash
java -jar my-app.jar
```

## Using Custom Class Names with gen-class

You can specify a custom class name using `:gen-class :name`:

```clojure
(ns my.app.core
  (:gen-class :name com.example.MyApp))

(defn -main [& args]
  (println "Hello from custom class!"))
```

The manifest will use `com.example.MyApp` as the Main-Class, and this class will be correctly included in the uberjar.

**Use cases for custom class names:**
- Matching Java naming conventions (e.g., `com.company.ProductMain`)
- Integration with Java tools that expect specific class names
- Multi-main-class JARs where each entry point needs a distinct class name

**Format requirements:**
- The `:gen-class :name` declaration should be on a single line for proper detection
- Multi-line gen-class forms with complex options are supported, but ensure `:name` appears early

```clojure
;; Supported - :name on same line as :gen-class
(ns my.app.core
  (:gen-class :name com.example.MyApp :implements [java.io.Serializable]))

;; Also supported - :name on its own line immediately after :gen-class
(ns my.app.core
  (:gen-class
    :name com.example.MyApp))
```

## Direct Linking

Compilation uses `:direct-linking true` for optimal performance. This:
- Eliminates var dereferencing overhead at call sites
- Produces faster startup times
- Creates smaller class files

**Trade-off**: With direct linking, runtime var redefinition won't affect already-compiled call sites. If you need dynamic redefinition (e.g., for REPL development patterns in production), mark vars with `^:redef`:

```clojure
(defn ^:redef my-configurable-fn []
  ...)
```

## Source-Only Mode

To avoid AOT compilation entirely, use `clojure.main` as your main namespace:

```python
clojure_deploy_jar(
    name="my-app-source-only",
    main="clojure.main",
    dependencies=[":my-lib"],
)
```

Then run your app by specifying the namespace at runtime:
```bash
java -jar my-app-source-only.jar -m my.actual.namespace
```

**When to use source-only mode:**
- Libraries with known AOT compatibility issues
- Development/testing where startup time is acceptable
- Maximum compatibility with dynamically-loaded code

**Trade-offs:**
- Slower startup (10-30+ seconds for compilation at runtime)
- Must remember the `-m namespace` invocation

## How It Works

### tools.build Integration

The plugin uses Clojure's official **tools.build** library to handle AOT compilation and uberjar creation. This approach:

1. **Delegates complexity**: tools.build handles all the intricacies of AOT compilation
2. **Uses Pants-resolved classpaths**: Dependencies are resolved by Pants/Coursier, not tools.deps
3. **Maintains classpath isolation**: tools.build runs in its own JVM, separate from application compilation

### The Process

1. **Pants resolves dependencies** using Coursier
2. **tools.build compiles** your main namespace (Clojure transitively compiles required namespaces)
3. **tools.build packages** the uberjar with compiled classes and dependency JARs

```
┌─────────────────────────────────────────────────────────────────────┐
│                     JVM Process (started by Pants)                  │
│  Classpath: tools.build + Clojure + tools.deps + tools.namespace   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  build.clj script                                            │   │
│  │                                                              │   │
│  │  (b/compile-clj {:basis compile-basis ...})                  │   │
│  │        │                                                     │   │
│  │        ▼  forks new JVM                                      │   │
│  │  ┌──────────────────────────────────────────────────────┐   │   │
│  │  │  Compilation subprocess                               │   │   │
│  │  │  Classpath: src/ + compile-libs/*.jar                 │   │   │
│  │  │  (includes provided deps for type resolution)         │   │   │
│  │  └──────────────────────────────────────────────────────┘   │   │
│  │                                                              │   │
│  │  (b/uber {:basis uber-basis ...})                            │   │
│  │        │                                                     │   │
│  │        ▼  reads JARs from uber-basis classpath               │   │
│  │  ┌──────────────────────────────────────────────────────┐   │   │
│  │  │  Packages: classes/ + uber-libs/*.jar → app.jar       │   │   │
│  │  │  (excludes provided deps from final JAR)              │   │   │
│  │  └──────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Classpath Isolation

tools.build maintains strict classpath isolation:

- **tools.build classpath**: Contains tools.build, Clojure, tools.deps, etc.
- **Compile classpath**: Your sources + ALL dependencies (including provided)
- **Uber classpath**: Your sources + runtime dependencies (excluding provided)

When `b/compile-clj` runs, it forks a NEW JVM with only the application's classpath. This ensures your application is compiled with its own Clojure version, not the tools.build's version.

## Macro-Generated Classes

tools.build correctly handles macro-generated classes. When macros like `defrecord`, `deftype`, or library-specific macros (Specter's `declarepath`, core.async's `go`) generate classes, they are properly included in the final JAR.

### Libraries with this pattern

- **Specter** - `declarepath`, `providepath` macros
- **core.async** - `go` macro generates state machine classes
- **core.match** - pattern compilation generates internal classes

## Troubleshooting

### "No implementation of method" Errors

If you see errors like:
```
java.lang.IllegalArgumentException: No implementation of method: :foo of protocol:
#'some.lib/Protocol found for class: some.lib.SomeRecord
```

This typically indicates a protocol/class identity mismatch. The tools.build integration should handle most cases correctly. If you still see this error:

1. Ensure you're using the latest version of pants-backend-clojure
2. Check if the library has any special packaging requirements
3. Try using source-only mode: `main="clojure.main"`
4. Try marking the problematic library as `provided` if it should be supplied at runtime

### Verifying JAR Contents

To inspect what classes are in your JAR:

```bash
# List all classes
jar tf dist/my-app.jar | grep '\.class$'

# Check for first-party classes
jar tf dist/my-app.jar | grep '^myapp/'

# Check for third-party classes
jar tf dist/my-app.jar | grep '^clojure/core'
```

### Debug Logging

To see packaging details, run Pants with debug logging:

```bash
pants --level=debug package //path/to:my_deploy_jar
```

You'll see messages from tools.build about compilation and packaging progress.

## Provided Dependencies

When using `provided` dependencies (dependencies available at runtime but not bundled in the JAR):

1. AOT-compiled classes for provided namespaces are excluded from the JAR
2. Provided library JARs are not extracted into the uberjar

See [Provided Dependencies](./provided_dependencies.md) for more information.

## Migration Guide

### From Old API (with `aot` field)

If you were using the old `aot` field, here's how to migrate:

| Old Mode | New Equivalent | Notes |
|----------|----------------|-------|
| `aot=()` (default) | Just specify `main` | Unchanged behavior |
| `aot=[":none"]` | `main="clojure.main"` | Run with `java -jar app.jar -m namespace` |
| `aot=[":all"]` | Just specify `main` | Transitive compilation covers needed namespaces |
| `aot=["ns1", "ns2"]` | Just specify `main` | If ns1/ns2 are required by main, they'll be compiled |

## References

- [Clojure - Ahead-of-time Compilation](https://clojure.org/reference/compilation)
- [Direct Linking in Clojure](https://clojure.org/reference/compilation#directlinking)
- [tools.build documentation](https://clojure.org/guides/tools_build)
