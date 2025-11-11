# Third-Party Clojure Dependency Inference

## Overview

The Clojure Pants backend now supports **automatic dependency inference for third-party Clojure libraries**. When you require a namespace from a third-party JAR (like `[clojure.data.json :as json]`), Pants will automatically infer the dependency on the corresponding `jvm_artifact` target.

This eliminates the need to manually specify `dependencies` in your BUILD files for third-party Clojure libraries!

## Quick Start

### Step 1: Generate Lock Files

First, ensure you have JVM lockfiles generated for your project:

```bash
pants generate-lockfiles ::
```

This creates lockfiles like:
```
3rdparty/jvm/default.lock
3rdparty/jvm/java17.lock
```

### Step 2: Generate Clojure Namespace Metadata

Run the new goal to analyze all JARs and generate namespace mappings:

```bash
pants generate-clojure-lockfile-metadata ::
```

This analyzes each JAR in your lockfiles and creates metadata files:
```
3rdparty/jvm/default_clojure_namespaces.json
3rdparty/jvm/java17_clojure_namespaces.json
```

Example output:
```
Generating Clojure namespace metadata from JVM lockfiles...
  Processing resolve 'default' (3rdparty/jvm/default.lock)...
  ✓ Generated 3rdparty/jvm/default_clojure_namespaces.json: 15 artifacts, 42 namespaces

✓ Successfully generated metadata for 1 resolve(s).
```

### Step 3: Write Clojure Code

Now you can require third-party namespaces without manual dependencies:

```clojure
(ns myproject.api
  (:require [clojure.data.json :as json]       ; Auto-inferred!
            [clojure.tools.logging :as log]))   ; Auto-inferred!

(defn process-request [data]
  (log/info "Processing request")
  (json/write-str data))
```

### Step 4: No BUILD File Changes Needed!

**Before** (manual dependencies required):
```python
clojure_source(
    name="api",
    source="api.clj",
    dependencies=[
        "3rdparty/jvm:data-json",      # Had to specify manually
        "3rdparty/jvm:tools-logging",  # Had to specify manually
    ],
)
```

**After** (automatic inference):
```python
clojure_source(
    name="api",
    source="api.clj",
    # No dependencies field needed!
)
```

Or even simpler with the generator target:
```python
# BUILD file
clojure_sources()  # That's it!
```

### Step 5: Build/Test as Normal

```bash
pants check ::
pants test ::
pants package ::
```

Dependencies are automatically inferred!

## How It Works

### Architecture

1. **JAR Analysis** - The `generate-clojure-lockfile-metadata` goal analyzes each JAR in your lockfiles to extract Clojure namespaces
2. **Metadata Storage** - Namespace→artifact mappings are stored in `*_clojure_namespaces.json` files
3. **Dependency Inference** - When you build/test, Pants loads the metadata and automatically infers third-party dependencies

### Resolution Strategy

When you require a namespace, Pants follows this resolution order:

1. **First-party sources** - Checks if you have a local `.clj` file for that namespace
2. **Third-party libraries** - Falls back to the metadata mappings
3. **Not found** - If neither exists, no dependency is inferred (will error at runtime)

This ensures that **local code always takes precedence** over third-party libraries.

## Metadata File Format

The generated metadata files use this JSON structure:

```json
{
  "version": "1.0",
  "resolve": "default",
  "lockfile": "3rdparty/jvm/default.lock",
  "lockfile_hash": "sha256:abc123...",
  "artifacts": {
    "org.clojure:data.json:2.4.0": {
      "address": "3rdparty/jvm:data-json",
      "namespaces": ["clojure.data.json"],
      "source": "jar-analysis"
    },
    "org.clojure:core.async:1.6.681": {
      "address": "3rdparty/jvm:core-async",
      "namespaces": [
        "clojure.core.async",
        "clojure.core.async.impl.protocols",
        "clojure.core.async.impl.channels"
      ],
      "source": "jar-analysis"
    }
  }
}
```

**Fields:**
- `version` - Metadata format version (for future evolution)
- `resolve` - JVM resolve name (e.g., "default", "java17")
- `lockfile` - Path to the corresponding lockfile
- `lockfile_hash` - SHA256 hash for staleness detection
- `artifacts` - Map of Maven coordinate to namespace info
  - `address` - Pants address of the `jvm_artifact` target
  - `namespaces` - List of Clojure namespaces provided by this artifact
  - `source` - How namespaces were determined ("jar-analysis", "manual")

## When to Regenerate Metadata

You should regenerate the metadata files when:

1. **Adding new dependencies** - Run `pants generate-lockfiles` then `pants generate-clojure-lockfile-metadata`
2. **Updating dependency versions** - Same as above
3. **Changing resolves** - If you modify your `jvm.resolves` configuration

**Recommended workflow:**
```bash
# 1. Update your jvm_artifact targets in 3rdparty/jvm/BUILD
# 2. Regenerate lockfiles
pants generate-lockfiles ::

# 3. Regenerate Clojure namespace metadata
pants generate-clojure-lockfile-metadata ::

# 4. Commit both lockfiles and metadata files
git add 3rdparty/jvm/*.lock 3rdparty/jvm/*_clojure_namespaces.json
git commit -m "Update dependencies"
```

## Version Control

**Should you commit the metadata files?**

**Yes, recommended!** The metadata files should be committed alongside your lockfiles because:

✅ **Reproducible builds** - Everyone gets the same dependency inference
✅ **Faster CI** - No need to regenerate metadata on every build
✅ **Auditable** - You can see exactly what namespaces map to which artifacts
✅ **Consistent with Pants patterns** - Similar to committing lockfiles

Add to your `.gitignore` only if your team prefers to regenerate on-demand:
```gitignore
# Optional: Don't commit metadata (regenerate on-demand)
*_clojure_namespaces.json
```

## Advanced Features

### Multiple Resolves

If you use multiple JVM resolves (e.g., for different Java versions), the system handles them automatically:

```python
# pants.toml
[jvm]
resolves = { default = "3rdparty/jvm/default.lock", java17 = "3rdparty/jvm/java17.lock" }
```

Running `pants generate-clojure-lockfile-metadata ::` generates metadata for all resolves:
```
3rdparty/jvm/default_clojure_namespaces.json
3rdparty/jvm/java17_clojure_namespaces.json
```

Each resolve gets its own namespace mappings, so you can have different versions of the same library in different resolves.

### AOT-Compiled JARs

The system supports both source JARs and AOT-compiled JARs:

- **Source JARs** (`.clj`, `.cljc`, `.clje`) - Namespaces are extracted by parsing the `(ns ...)` declarations
- **AOT-compiled JARs** (only `.class` files) - Namespaces are inferred from class file paths

This means it works with any Clojure library, whether it ships with source or only compiled bytecode.

### Ambiguous Namespaces

If multiple artifacts provide the same namespace (rare but possible), Pants will warn you:

```
WARNING: The target //src:api has ambiguous dependency:
  Namespace 'com.example.util' is provided by:
    - 3rdparty/jvm:lib-a
    - 3rdparty/jvm:lib-b

Please specify which one explicitly in dependencies=[...].
```

**Resolution:** Add an explicit dependency in your BUILD file:
```python
clojure_source(
    name="api",
    source="api.clj",
    dependencies=["3rdparty/jvm:lib-a"],  # Explicit choice
)
```

## Troubleshooting

### Metadata file is stale

**Problem:** You updated dependencies but forgot to regenerate metadata.

**Solution:**
```bash
pants generate-clojure-lockfile-metadata ::
```

### Namespace not being inferred

**Checklist:**
1. Did you run `generate-clojure-lockfile-metadata`?
2. Is the metadata file present? Check for `*_clojure_namespaces.json`
3. Is the artifact in your lockfile? Check `3rdparty/jvm/*.lock`
4. Is there a first-party file with the same namespace? (First-party takes precedence)
5. Check the metadata file - is the namespace listed?

**Debug:**
```bash
# Check if metadata exists
ls -la 3rdparty/jvm/*_clojure_namespaces.json

# View metadata contents
cat 3rdparty/jvm/default_clojure_namespaces.json | jq '.artifacts'

# Regenerate if needed
pants generate-clojure-lockfile-metadata ::
```

### Build is slow after adding metadata

The metadata files are loaded once and cached, so they should have minimal performance impact. The analysis only happens during `generate-clojure-lockfile-metadata`, not during normal builds.

If builds are slow, it's likely unrelated to the metadata system. Check:
- Are you using `--no-pantsd`? (Pantsd provides caching)
- Are lockfiles very large? (>1000 dependencies)

## Comparison with deps.edn

If you're coming from a `deps.edn` workflow:

| Feature | deps.edn | Pants with Metadata |
|---------|----------|---------------------|
| Third-party deps | Automatic | Automatic ✅ |
| First-party deps | Manual (aliases) | Automatic ✅ |
| Dependency management | git deps, Maven | Coursier lockfiles |
| Multi-project | Requires configuration | Built-in monorepo support |
| Incremental builds | Limited | Full dependency graph |

The main difference is that Pants requires an extra step (`generate-clojure-lockfile-metadata`) after updating dependencies, but in exchange you get:
- Reproducible builds with lockfiles
- Automatic first-party dependency inference
- Monorepo-scale performance
- Integration with Java, Scala, and other JVM languages

## Examples

### Example 1: Simple API with JSON

```clojure
(ns myproject.api
  (:require [clojure.data.json :as json]))

(defn handler [request]
  {:status 200
   :body (json/write-str {:result "success"})})
```

**No BUILD file needed** (with `clojure_sources()` generator target)

### Example 2: Mixed First-Party and Third-Party

```clojure
(ns myproject.service
  (:require [myproject.util :as util]           ; First-party - inferred
            [clojure.data.json :as json]         ; Third-party - inferred
            [clojure.tools.logging :as log]))    ; Third-party - inferred

(defn process [data]
  (log/info "Processing" data)
  (util/validate data)
  (json/write-str data))
```

**BUILD file:**
```python
clojure_sources()  # All dependencies inferred automatically!
```

### Example 3: Multiple Namespaces from One Artifact

```clojure
(ns myproject.async
  (:require [clojure.core.async :as async]
            [clojure.core.async.impl.protocols :as protocols]))

(defn setup []
  (async/chan 10))
```

Both `clojure.core.async` and `clojure.core.async.impl.protocols` map to the same `core-async` artifact, so only one dependency is inferred.

## Migration Guide

If you have existing projects with manual third-party dependencies:

### Before
```python
clojure_source(
    name="api",
    source="api.clj",
    dependencies=[
        "//src/util:core",           # First-party
        "3rdparty/jvm:data-json",    # Third-party
        "3rdparty/jvm:tools-logging", # Third-party
    ],
)
```

### After
```python
clojure_source(
    name="api",
    source="api.clj",
    # All dependencies removed - automatic inference!
)
```

Or use the generator:
```python
# Just this!
clojure_sources()
```

**Migration steps:**
1. Run `pants generate-clojure-lockfile-metadata ::`
2. Remove third-party dependencies from BUILD files
3. Keep explicit dependencies only for:
   - Java-only libraries (no Clojure namespaces)
   - Disambiguating ambiguous namespaces
   - Overriding automatic inference
4. Run `pants check ::` to verify everything works
5. Commit changes

## Future Enhancements

Planned features:

- **Manual namespace override** - Add `clojure_namespaces` field to `jvm_artifact` for manual specification
- **Staleness auto-detection** - Automatic warnings when metadata is out of sync with lockfiles
- **ClojureScript support** - Extend to `.cljs` files
- **Integration with generate-lockfiles** - Automatically generate metadata when lockfiles are generated

## Summary

Third-party Clojure dependency inference provides:

✅ **Automatic inference** - No manual `dependencies` for third-party Clojure libraries
✅ **Fast builds** - Pre-computed metadata, no runtime overhead
✅ **Reproducible** - Version-controlled metadata ensures consistency
✅ **Precedence** - First-party sources always take priority
✅ **Multi-resolve support** - Works with multiple Java versions
✅ **AOT support** - Handles both source and compiled JARs

**Get started:**
```bash
pants generate-lockfiles ::
pants generate-clojure-lockfile-metadata ::
# Now all third-party Clojure dependencies are automatically inferred!
```
