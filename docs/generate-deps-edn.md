# generate-deps-edn Goal

The `generate-deps-edn` goal creates a `deps.edn` file from your Pants-managed Clojure project, enabling standard Clojure IDE workflows with tools like Cursive and Calva.

## Quick Start

```bash
# Generate deps.edn for the default resolve
pants generate-deps-edn

# Generate for a specific resolve
pants generate-deps-edn --resolve=java21

# Generate with a custom output path
pants generate-deps-edn --resolve=java21 --output-path=deps-java21.edn
```

After generation, you can use standard Clojure tooling:

```bash
# Start nREPL server
clj -M:nrepl -m nrepl.server

# Start Rebel Readline REPL
clj -M:rebel

# Or open project in Cursive/Calva - they auto-discover deps.edn
```

## What's Generated

The generated `deps.edn` file includes:

### 1. Clojure Source Paths (`:paths`)

All Clojure source directories for the specified resolve:

```clojure
{:paths ["projects/example/project-a/src"
         "projects/example/project-b/src"
         "projects/example/project-c/src"]
 ...}
```

### 2. Third-Party Dependencies (`:deps`)

All dependencies from the lock file, with `:exclusions [*/*]` to prevent re-resolution:

```clojure
{:deps {org.clojure/clojure {:mvn/version "1.12.0" :exclusions [*/*]}
        com.google.guava/guava {:mvn/version "33.0.0-jre" :exclusions [*/*]}
        ...}
 ...}
```

**Why `:exclusions [*/*]`?**

Pants lock files are fully resolved with all transitive dependencies flattened. The `:exclusions [*/*]` (a wildcard qualified symbol matching all group/artifact pairs) prevents `clj` from re-resolving transitives, ensuring exact version matching with Pants.

### 3. Maven Repositories (`:mvn/repos`)

Repository URLs from your `[coursier]` configuration are passed through to the generated deps.edn:

```clojure
{:mvn/repos {"clojars" {:url "https://repo.clojars.org/"}
             "central" {:url "https://maven-central.storage-download.googleapis.com/maven2"}
             "central-1" {:url "https://repo1.maven.org/maven2"}}}
```

**How repo names are derived:**
- URLs containing "clojars" → `"clojars"`
- URLs containing "maven-central" or "repo1.maven.org" → `"central"`
- Other URLs → hostname with dots/colons replaced by dashes (e.g., `"my-company-jfrog-io"`)
- Duplicate names get numeric suffixes (e.g., `"central"`, `"central-1"`)

**Configuration:**

Configure repositories in your `pants.toml`:

```toml
[coursier]
repos = [
  "https://repo.clojars.org/",
  "https://maven-central.storage-download.googleapis.com/maven2",
  "https://repo1.maven.org/maven2",
]
```

### 4. Aliases (`:aliases`)

Pre-configured aliases for common workflows:

```clojure
{:aliases {:test {:extra-paths ["projects/example/project-a/test"
                                 "projects/example/project-b/test"]}
           :nrepl {:extra-deps {nrepl/nrepl {:mvn/version "1.4.0" :exclusions [*/*]}}}
           :rebel {:extra-deps {com.bhauman/rebel-readline {:mvn/version "0.1.4" :exclusions [*/*]}}}}}
```

## Options

### `--resolve=<name>`

Specify which JVM resolve to generate deps.edn for. Defaults to `jvm.default_resolve`.

Example:
```bash
pants generate-deps-edn --resolve=java21
```

### `--output-path=<path>`

Specify the output file path (relative to build root). Defaults to `deps.edn`.

Example:
```bash
pants generate-deps-edn --resolve=java21 --output-path=deps-java21.edn
```

## Working with Multiple Resolves

If your project uses multiple JVM resolves (e.g., java17 and java21), generate separate deps.edn files:

```bash
pants generate-deps-edn --resolve=java17 --output-path=deps-java17.edn
pants generate-deps-edn --resolve=java21 --output-path=deps-java21.edn
```

Configure your IDE to use the appropriate file:
- **Cursive**: Preferences → Languages & Frameworks → Clojure → Deps → Choose deps file
- **Calva**: Set `calva.projectRootDirectory` to use specific deps.edn

## Java and Scala Interop

**Important**: Java and Scala sources are **intentionally excluded** from generated deps.edn files.

### Why?

1. **deps.edn is for source files**: Java/Scala must be compiled to `.class` files before use
2. **Pants handles compilation**: `pants repl` automatically compiles Java/Scala and includes them on the classpath
3. **IDE limitations**: Most Clojure IDEs don't support Java/Scala compile-on-save anyway

### If You Need Java/Scala in Your REPL

**Option 1: Use `pants repl` (Recommended)**

```bash
pants repl projects/your-clojure-project::
```

This automatically includes compiled Java/Scala classes on the classpath.

**Option 2: Manual Compilation**

1. Compile Java/Scala sources to JARs
2. Add to deps.edn:

```clojure
{:aliases {:jvm-sources {:extra-paths [".pants.d/custom/compiled.jar"]}}}
```

3. Use with: `clj -A:jvm-sources -M:nrepl -m nrepl.server`

## IDE Setup

### Cursive (IntelliJ IDEA)

1. Generate deps.edn: `pants generate-deps-edn --resolve=java21`
2. Open project in IntelliJ
3. Cursive auto-detects `deps.edn` in the project root
4. Start REPL: Tools → REPL → Run nREPL Server

### Calva (VS Code)

1. Generate deps.edn: `pants generate-deps-edn --resolve=java21`
2. Open project in VS Code
3. Cmd+Shift+P → "Calva: Start a Project REPL and Connect"
4. Calva auto-detects `deps.edn` and starts nREPL

### Emacs CIDER

1. Generate deps.edn: `pants generate-deps-edn --resolve=java21`
2. Open project in Emacs
3. `M-x cider-jack-in`
4. CIDER uses `clj` with the generated deps.edn

## Regenerating deps.edn

The generated file becomes stale when dependencies change. Regenerate with:

```bash
pants generate-deps-edn --resolve=java21
```

**Tip**: Add to your workflow:
```bash
# After updating lock files
pants generate-lockfiles --resolve=java21
pants generate-deps-edn --resolve=java21
```

## Comparison: deps.edn vs pants repl

| Feature | `clj` with deps.edn | `pants repl` |
|---------|---------------------|--------------|
| **Setup** | One-time generation | No setup needed |
| **IDE Integration** | ✅ Excellent (native) | ⚠️ Limited |
| **Java/Scala Support** | ❌ Manual setup | ✅ Automatic |
| **Live Reload** | ✅ tools.namespace | ✅ Workspace mode |
| **Hermeticity** | ⚠️ Can diverge | ✅ Always hermetic |
| **Best For** | Clojure-only IDE development | Mixed JVM codebases, CI/CD |

## Troubleshooting

### "Could not read lock file"

**Error**: `Error: Could not read lock file: locks/jvm/java21.lock.jsonc`

**Solution**: Ensure lock file exists:
```bash
pants generate-lockfiles --resolve=java21
```

### "Unrecognized resolve name"

**Error**: `Unrecognized resolve name: java21`

**Solution**: Check `pants.toml` has the resolve defined:
```toml
[jvm.resolves]
java21 = "locks/jvm/java21.lock.jsonc"
```

### IDE doesn't see Clojure namespaces

**Symptom**: IDE shows "cannot resolve namespace" errors

**Solutions**:
1. Verify source paths are correct in generated deps.edn
2. Restart IDE after regenerating deps.edn
3. Check IDE is using the correct deps.edn file

### Dependency version mismatch

**Symptom**: Different version at runtime than expected

**Cause**: `clj` re-resolved transitive dependencies (`:exclusions [*/*]` was removed)

**Solution**: Regenerate deps.edn - don't manually edit dependency declarations

## See Also

- [Clojure deps.edn Reference](https://clojure.org/reference/deps_edn)
- [Design Document](../docs/plans/20251015_repl_redesign.md)
- [Pants REPL Goal](https://www.pantsbuild.org/docs/goals/repl)
