# AOT Compilation in clojure_deploy_jar

This document explains how AOT (Ahead-of-Time) compilation works in the `clojure_deploy_jar` target and how the plugin handles transitively compiled third-party classes.

## Overview

When you create a `clojure_deploy_jar`, the plugin AOT-compiles your Clojure namespaces to produce an executable JAR. However, Clojure's `compile` function is inherently transitive - it compiles not just the namespace you specify, but ALL namespaces that it requires, including third-party libraries.

This transitive compilation is a well-known behavior in the Clojure ecosystem, and all major build tools (tools.build, Leiningen, Boot, depstar) have mechanisms to handle it.

## How It Works

### The Problem

When AOT-compiling `my.app.core`, Clojure will also compile:
- `clojure.core`
- `clojure.string`
- Any third-party library namespaces your code requires
- All transitive dependencies

If these third-party `.class` files were included in your uberjar, they could override the original classes from the library JARs. This can cause serious problems, especially with protocol extensions.

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

### Our Solution

The `clojure_deploy_jar` packaging rule filters AOT-compiled classes:

1. **Project namespaces are included**: Classes from your project's Clojure source files are included in the JAR
2. **Third-party classes are excluded**: Transitively compiled third-party classes are filtered out
3. **Original JARs are used**: Third-party library classes come from their original dependency JARs

This approach mirrors what `tools.build` does with its `:filter-nses` parameter and what Leiningen does with `:clean-non-project-classes`.

## Class File Filtering

The plugin identifies project classes by:

1. Analyzing all Clojure source files in your project to extract namespaces
2. Converting namespace names to class file paths (e.g., `my.app.core` → `my/app/core`)
3. Matching AOT-compiled class files against these paths

### Handled Class Types

The filtering correctly handles all Clojure-generated class file patterns:

| Pattern | Example | Description |
|---------|---------|-------------|
| Direct namespace | `my/app/core.class` | Main namespace class |
| Inner classes | `my/app/core$fn__123.class` | Anonymous functions |
| Method implementations | `my/app/core$_main.class` | gen-class methods |
| Init classes | `my/app/core__init.class` | Namespace initialization |
| Records | `my/app/core$MyRecord.class` | defrecord classes |
| Subpackages | `my/app/core/impl.class` | Nested namespaces |

### Hyphenated Namespaces

Clojure namespaces with hyphens are converted to underscores in class files:
- `my-app.core` → `my_app/core.class`

The plugin handles this conversion automatically.

## Troubleshooting

### "No implementation of method" Errors

If you see errors like:
```
java.lang.IllegalArgumentException: No implementation of method: :foo of protocol:
#'some.lib/Protocol found for class: some.lib.SomeRecord
```

This typically indicates a protocol/class identity mismatch. The fix ensures third-party classes come from their original JARs, which should resolve this issue.

### Verifying JAR Contents

To inspect what classes are in your JAR:

```bash
# List all classes
jar tf target/my-app.jar | grep '\.class$'

# Check for specific third-party classes (should NOT be present from AOT)
jar tf target/my-app.jar | grep 'clojure/core'
```

Third-party classes like `clojure/core.class` should be present (from the Clojure JAR), but you should NOT see duplicate entries from AOT compilation.

### Debug Logging

To see which classes are being filtered, run Pants with debug logging:

```bash
pants --level=debug package //path/to:my_deploy_jar
```

You'll see messages like:
```
Excluding transitively AOT-compiled third-party class: clojure/core$fn__123.class
Excluded 1234 transitively AOT-compiled third-party classes from //path/to:my_deploy_jar
```

### No Project Namespaces Warning

If you see:
```
No project namespaces detected for //path/to:my_deploy_jar. All AOT-compiled classes
will be excluded from the JAR.
```

This indicates a configuration issue. Ensure:
1. Your `clojure_source` targets are properly defined
2. They are listed as dependencies of the `clojure_deploy_jar`
3. The source files contain valid `(ns ...)` declarations

## Provided Dependencies

When using `provided` dependencies (dependencies available at runtime but not bundled in the JAR), those namespaces are also excluded from the project namespace set. This prevents AOT-compiled classes for provided dependencies from being included.

See [Provided Dependencies](./provided_dependencies.md) for more information.

## Comparison with Other Build Tools

| Tool | Filtering Mechanism |
|------|---------------------|
| **pants-backend-clojure** | Filters AOT output by project namespace paths |
| **tools.build** | `:filter-nses` parameter on `compile-clj` |
| **Leiningen** | `:clean-non-project-classes` option |
| **depstar** | `:exclude` with regex patterns |
| **Boot** | Manual namespace specification |

## References

- [Clojure - Ahead-of-time Compilation](https://clojure.org/reference/compilation)
- [tools.build compile-clj documentation](https://clojure.github.io/tools.build/clojure.tools.build.api.html#var-compile-clj)
- [Leiningen Issue #679](https://github.com/technomancy/leiningen/issues/679) - Protocol reload semantics with AOT
