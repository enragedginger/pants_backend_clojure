# AOT Compilation in clojure_deploy_jar

This document explains how AOT (Ahead-of-Time) compilation works in the `clojure_deploy_jar` target and how the plugin handles transitively compiled third-party classes.

## Overview

When you create a `clojure_deploy_jar`, the main namespace is AOT compiled along with all namespaces it transitively requires. This produces an executable JAR that starts quickly without runtime compilation.

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

### The Challenge

When AOT-compiling `my.app.core`, Clojure will also compile:
- `clojure.core`
- `clojure.string`
- Any third-party library namespaces your code requires
- All transitive dependencies

If these third-party `.class` files were included in your uberjar WITHOUT the correct handling, they could cause problems with protocol extensions (class identity mismatches).

### Protocol Extension Issues

Protocol extensions in Clojure rely on runtime evaluation order - the protocol must be defined before extensions are added. When AOT-compiled classes for protocols are mixed with non-AOT code:

1. The AOT-compiled class references the protocol interface directly
2. If the protocol was compiled separately (different compile run), class identities don't match
3. This causes "No implementation of method" errors

Example error:
```
java.lang.IllegalArgumentException: No implementation of method: :spec of protocol:
#'rpl.schema.core/Schema found for class: rpl.rama.util.schema.Volatile
```

### Our Solution: First-Party AOT Only

The `clojure_deploy_jar` packaging rule uses a targeted approach:

1. **First pass: Add only first-party AOT-compiled classes**
   - Classes from AOT compilation are filtered by namespace
   - Only classes belonging to your project's namespaces (from `clojure_source` targets) are added
   - Third-party classes from AOT are discarded

2. **Second pass: Extract dependency JARs**
   - All dependency JAR contents are extracted into the uberjar
   - Third-party classes come from the original JARs (correct protocol identity)
   - Source-only libraries have their source files included

### Why This Works

| Scenario | AOT Classes | JAR Contents | Result |
|----------|-------------|--------------|--------|
| Pre-compiled library | Discarded | `lib/Protocol.class` (correct) | JAR provides correct classes |
| Source-only library | Discarded | `lib/source_only.clj` | Source included for runtime compilation |
| First-party code | Included | N/A | Project classes from AOT |

This approach ensures that:
- First-party code gets the performance benefits of AOT compilation
- Third-party libraries use their original packaged classes (protocol safety)
- Source-only libraries work correctly at runtime

## Troubleshooting

### "No implementation of method" Errors

If you see errors like:
```
java.lang.IllegalArgumentException: No implementation of method: :foo of protocol:
#'some.lib/Protocol found for class: some.lib.SomeRecord
```

This typically indicates a protocol/class identity mismatch. The first-party-only AOT approach should resolve this issue. If you still see this error:

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

# Check for third-party classes (should be from JARs)
jar tf dist/my-app.jar | grep '^clojure/core'
```

### Debug Logging

To see packaging details, run Pants with debug logging:

```bash
pants --level=debug package //path/to:my_deploy_jar
```

You'll see messages about:
- How many first-party classes were included from AOT
- How many third-party classes were skipped

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
