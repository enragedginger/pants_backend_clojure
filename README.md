# pants-backend-clojure

A Clojure backend for the [Pants build system](https://www.pantsbuild.org/).

This plugin brings first-class Clojure support to Pants, enabling REPL-driven development, automatic dependency inference, linting, formatting, testing, and uberjar packaging within a monorepo-friendly build system.

## Features

- **Dependency Inference**: Automatically discovers dependencies from `require` and `import` forms
- **REPL**: Interactive development with nREPL support and rebel-readline
- **Testing**: Run tests with `clojure.test` via `pants test`
- **Linting**: Static analysis with [clj-kondo](https://github.com/clj-kondo/clj-kondo)
- **Formatting**: Code formatting with [cljfmt](https://github.com/weavejester/cljfmt)
- **Packaging**: Build uberjars with AOT compilation and direct linking
- **JVM Integration**: Works with Pants' JVM support for mixed Clojure/Java projects
- **Provided Dependencies**: Maven-style provided scope for excluding runtime dependencies

## Installation

Add the plugin to your `pants.toml`:

```toml
[GLOBAL]
pants_version = "2.29.0"
plugins = ["pants-backend-clojure==0.1.0"]
backend_packages = [
    "pants.backend.experimental.java",
    "pants_backend_clojure",
]

[coursier]
repos = [
    "https://repo.clojars.org/",
    "https://repo1.maven.org/maven2",
]

[jvm.resolves]
jvm-default = "locks/jvm-default.lock"
```

## Quick Start

### 1. Define your dependencies

Create a `BUILD` file with your Clojure dependencies:

```python
jvm_artifact(
    name="clojure",
    group="org.clojure",
    artifact="clojure",
    version="1.12.0",
)

jvm_artifact(
    name="core-async",
    group="org.clojure",
    artifact="core.async",
    version="1.7.701",
)
```

### 2. Add source targets

```python
clojure_sources(
    name="lib",
    dependencies=["//:clojure"],
)

clojure_tests(
    name="tests",
    dependencies=["//:clojure"],
)
```

### 3. Generate the lockfile

```bash
pants generate-lockfiles
```

### 4. Run common commands

```bash
# Start a REPL
pants repl src/myapp:lib

# Run tests
pants test ::

# Lint code
pants lint ::

# Format code
pants fmt ::

# Check for errors
pants check ::

# Build an uberjar
pants package src/myapp:deploy
```

## Target Types

### `clojure_source` / `clojure_sources`

Source files containing application or library code.

```python
clojure_sources(
    name="lib",
    sources=["*.clj", "*.cljc"],
    dependencies=["//:clojure"],
    resolve="jvm-default",
)
```

### `clojure_test` / `clojure_tests`

Test files using `clojure.test`.

```python
clojure_tests(
    name="tests",
    sources=["*_test.clj"],
    dependencies=[":lib", "//:clojure"],
    timeout=120,
)
```

### `clojure_deploy_jar`

An executable uberjar with AOT compilation.

```python
clojure_deploy_jar(
    name="deploy",
    main="myapp.core",  # Namespace with -main and (:gen-class)
    dependencies=[":lib", "//:clojure"],
    provided=[":servlet-api"],  # Excluded from JAR
)
```

The main namespace must include `(:gen-class)` and define a `-main` function:

```clojure
(ns myapp.core
  (:gen-class))

(defn -main [& args]
  (println "Hello, World!"))
```

## Goals

| Goal | Command | Description |
|------|---------|-------------|
| REPL | `pants repl path/to:target` | Start an interactive nREPL session |
| Test | `pants test ::` | Run clojure.test tests |
| Lint | `pants lint ::` | Static analysis with clj-kondo |
| Format | `pants fmt ::` | Format code with cljfmt |
| Check | `pants check ::` | Verify code compiles |
| Package | `pants package path/to:jar` | Build an uberjar |

## Configuration

### Tool versions

Override default tool versions in `pants.toml`:

```toml
[clj-kondo]
version = "2025.10.23"

[cljfmt]
version = "0.14.0"

[nrepl]
version = "1.4.0"
```

### Skip linting/formatting per target

```python
clojure_source(
    name="generated",
    source="generated.clj",
    skip_cljfmt=True,
    skip_clj_kondo=True,
)
```

## System Requirements

- **Python**: 3.9+
- **Pants**: 2.20.0+
- **JDK**: 11+ (17 or 21 recommended)
- **zip/unzip**: Required for clj-kondo
  - macOS: Pre-installed
  - Debian/Ubuntu: `apt-get install zip unzip`
  - RHEL/Fedora: `dnf install zip unzip`
  - Alpine: `apk add zip unzip`

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.
