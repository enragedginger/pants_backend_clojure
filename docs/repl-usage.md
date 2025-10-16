# Clojure REPL Usage Guide

This document provides correct usage examples for the Clojure REPL functionality with Pants.

## Basic Usage

The Clojure REPL requires specifying the `--shell` parameter and a target address.

### Target Address Format

Clojure targets in this project use a resolve-specific naming convention:
- Directory target: `projects/example/project-a/src:java17`
- File target: `projects/example/project-a/src/example/project_a/core.clj:../../java17`

Use `pants list <directory>::` to see available targets.

## REPL Variants

### Standard Clojure REPL

```bash
pants repl --shell=clojure projects/example/project-a/src:java17
```

Features:
- Basic Clojure REPL with clojure.main
- Supports live code reloading
- All sources in java17 resolve are available by default

### nREPL Server (for IDE integration)

```bash
pants repl --shell=nrepl projects/example/project-a/src:java17
```

Features:
- Starts nREPL server on port 7888 (configurable)
- For editor integration (Calva, CIDER, Cursive, etc.)
- Configure port: `--nrepl-port=<port>`

### Rebel Readline REPL (Enhanced)

```bash
pants repl --shell=rebel projects/example/project-a/src:java17
```

Features:
- Enhanced REPL with syntax highlighting
- Better readline support
- Multi-line editing

## Load Resolve Sources Feature

By default, all Clojure sources in the same resolve are loaded, allowing you to require any namespace without explicit BUILD dependencies.

### Enable (Default)

```bash
pants repl --shell=clojure projects/example/project-a/src:java17
```

In the REPL, you can require any namespace from project-a, project-b, or project-c (all in java17 resolve):

```clojure
user=> (require '[example.project-a.core :as a])
nil
user=> a/thing
"example common value"
user=> (require '[example.project-b.core :as b])
nil
user=> (b/use-project-a)
"Project B using: example common value"
```

### Disable (Hermetic Mode)

```bash
pants repl --shell=clojure --no-clojure-repl-load-resolve-sources projects/example/project-a/src:java17
```

In hermetic mode, only transitive dependencies from the BUILD file are loaded (faster but requires explicit BUILD dependencies). Namespaces not in the dependency graph will fail to load:

```clojure
user=> (require '[example.project-c.core :as c])
Execution error (FileNotFoundException) at user/eval1 (REPL:1).
Could not locate example/project_c/core__init.class...
```

## Configuration Options

### Clojure REPL

```bash
# View all options
pants help clojure-repl
```

- `--[no-]clojure-repl-load-resolve-sources`: Load all sources in resolve (default: True)

### nREPL

```bash
# View all options
pants help nrepl
```

- `--nrepl-port=<port>`: Port to bind to (default: 7888)
- `--nrepl-host=<host>`: Host to bind to (default: 127.0.0.1)
- `--nrepl-version=<version>`: nREPL version (default: 1.4.0)

### Rebel Readline

```bash
# View all options
pants help rebel-repl
```

- `--rebel-repl-version=<version>`: Rebel Readline version (default: 0.1.4)

## Troubleshooting

### "KeyError: 'python-default'"

**Cause**: Missing `--shell` parameter (defaults to Python REPL)

**Solution**: Add `--shell=clojure` (or `nrepl`/`rebel`)

```bash
pants repl --shell=clojure projects/example/project-a/src:java17
```

### "Address already in use" (nREPL)

**Cause**: Port 7888 is already in use

**Solution**: Specify a different port:

```bash
pants repl --shell=nrepl --nrepl-port=7889 projects/example/project-a/src:java17
```

### "Could not find or load main class"

**Cause**: Incorrect Java classpath or missing dependencies

**Solution**: Try clearing Pants cache:

```bash
pants clean-all
pants repl --shell=clojure projects/example/project-a/src:java17
```

## Working with Multiple Resolves

If your project uses multiple JVM resolves (e.g., java17 and java21), specify the correct target:

```bash
# Use java17 resolve
pants repl --shell=clojure projects/example/project-a/src:java17

# Use java21 resolve
pants repl --shell=clojure projects/example/project-a/src:java21
```

The REPL will only load sources from the same resolve, ensuring isolation between different JVM versions.

## See Also

- [generate-deps-edn Goal](./generate-deps-edn.md) - Generate deps.edn for IDE integration
- [REPL Redesign Plan](./plans/20251015_repl_redesign.md) - Design document for REPL improvements
