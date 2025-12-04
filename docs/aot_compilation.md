# AOT Compilation in clojure_deploy_jar

This document explains how AOT (Ahead-of-Time) compilation works in the `clojure_deploy_jar` target and how the plugin handles transitively compiled third-party classes.

## Overview

When you create a `clojure_deploy_jar`, the plugin AOT-compiles your Clojure namespaces to produce an executable JAR. However, Clojure's `compile` function is inherently transitive - it compiles not just the namespace you specify, but ALL namespaces that it requires, including third-party libraries.

This transitive compilation is a well-known behavior in the Clojure ecosystem, and all major build tools (tools.build, Leiningen, Boot, depstar) have mechanisms to handle it.

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

### Our Solution: AOT First, JAR Override

The `clojure_deploy_jar` packaging rule uses a two-pass approach:

1. **First pass: Add all AOT-compiled classes**
   - All classes from AOT compilation are added to the JAR (project + third-party transitives)
   - This ensures source-only libraries (libraries that ship without pre-compiled .class files) work correctly

2. **Second pass: Extract dependency JARs, overriding existing entries**
   - Dependency JAR contents are extracted into the uberjar
   - When a JAR contains a class that was already added from AOT, the JAR version wins
   - This ensures pre-compiled libraries use their original classes (protocol safety)

### Why This Works

| Scenario | AOT Classes | JAR Contents | Result |
|----------|-------------|--------------|--------|
| Pre-compiled library | `lib/Protocol.class` (wrong identity) | `lib/Protocol.class` (correct) | JAR overwrites → CORRECT |
| Source-only library | `lib/SourceOnly.class` | `lib/source_only.clj` (no class) | AOT class kept → CORRECT |
| Partial library | `lib/A.class`, `lib/B.class` | `lib/A.class`, `lib/b.clj` | JAR overwrites A, AOT B kept → CORRECT |

This approach aligns with the standard behavior in the Clojure ecosystem where "last write wins" during JAR packaging.

## Source-Only Libraries

Many Clojure libraries are distributed as source-only (containing only `.clj` files, no pre-compiled `.class` files). The AOT-first approach ensures these libraries work correctly:

1. During AOT compilation, these libraries are transitively compiled
2. Their `.class` files are added to the uberjar in the first pass
3. When extracting their JARs, only source files are present (no conflict)
4. At runtime, the JVM uses the compiled `.class` files

Without the AOT-first approach, source-only libraries would fail at runtime with `ClassNotFoundException` because their required classes wouldn't be present.

## Troubleshooting

### "No implementation of method" Errors

If you see errors like:
```
java.lang.IllegalArgumentException: No implementation of method: :foo of protocol:
#'some.lib/Protocol found for class: some.lib.SomeRecord
```

This typically indicates a protocol/class identity mismatch. The AOT-first, JAR-override approach should resolve this issue. If you still see this error:

1. Ensure you're using the latest version of pants-backend-clojure
2. Check if the library has any special packaging requirements
3. Try marking the problematic library as `provided` if it should be supplied at runtime

### Verifying JAR Contents

To inspect what classes are in your JAR:

```bash
# List all classes
jar tf target/my-app.jar | grep '\.class$'

# Check for specific third-party classes
jar tf target/my-app.jar | grep 'clojure/core'
```

Third-party classes like `clojure/core.class` should be present (from the Clojure JAR, which overwrites the AOT version).

### Debug Logging

To see which AOT classes are being overridden by JAR contents, run Pants with debug logging:

```bash
pants --level=debug package //path/to:my_deploy_jar
```

You'll see messages like:
```
JAR class overrides AOT: clojure/core$fn__123.class
Dependency JARs overrode 1234 AOT-compiled classes for //path/to:my_deploy_jar
```

## Provided Dependencies

When using `provided` dependencies (dependencies available at runtime but not bundled in the JAR):

1. AOT-compiled classes for provided namespaces are excluded from the JAR
2. Provided library JARs are not extracted into the uberjar

See [Provided Dependencies](./provided_dependencies.md) for more information.

## Comparison with Other Build Tools

| Tool | Approach |
|------|----------|
| **pants-backend-clojure** | AOT first, JAR override (last write wins) |
| **tools.build** | Uses `:filter-nses` to exclude third-party AOT |
| **Leiningen** | Uses `:clean-non-project-classes` option |
| **depstar** | JAR contents merged, last wins |

Our approach aligns with the ecosystem standard where dependency JARs are processed after AOT classes, ensuring pre-compiled library classes are used.

## References

- [Clojure - Ahead-of-time Compilation](https://clojure.org/reference/compilation)
- [tools.build compile-clj documentation](https://clojure.github.io/tools.build/clojure.tools.build.api.html#var-compile-clj)
- [Leiningen Issue #679](https://github.com/technomancy/leiningen/issues/679) - Protocol reload semantics with AOT
