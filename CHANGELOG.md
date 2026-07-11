# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- The nREPL startup message now reports the port the server actually bound
  instead of echoing the configured `[nrepl].port` value. With `port = 0`
  (OS-assigned ephemeral port) the message previously printed
  "nREPL server started on port 0". The `[nrepl].port` help text now also
  documents the `0` auto-select behavior.

## [0.2.3] - 2026-05-24

### Added

- **Concurrency-slot support for the test runner.** New
  `[clojure-test-runner].execution_slot_var` option (mirroring pytest's
  `[pytest].execution_slot_var`). When set, Pants assigns each concurrent
  Clojure test process a unique integer slot id (0..N-1, where N is the local
  process parallelism) and exposes it to the test JVM via the named env var.
  Tests can use this id to partition shared resources across parallel
  workers — e.g. compute a non-overlapping TCP port window per worker
  (`start = base + slot * size`), pick a private temp directory, or
  namespace a database under test.

  Implementation note: `JvmProcess` does not currently forward
  `execution_slot_variable` to the underlying `Process`, so the test rule
  does the forwarding itself with `dataclasses.replace` after
  `jvm_process(...)`.

### Fixed

- Improved error handling for missing local `jar=` files, with validation
  consolidated to a single failure point so misconfigured local JARs produce
  one clear error.
- The execution slot var is no longer forwarded to the debug-test process.

## [0.2.2] - 2026-04-04

### Added

- Dependency inference now parses both `.clj` and `.cljc` files when resolving
  `require`/`import` forms.
- Added an example-project list to the docs.

## [0.2.1] - 2026-03-28

### Added

- **JVM options for the test runner.** New `[clojure-test-runner].args` option
  lets you pass JVM flags (e.g. `-D` system properties) to test processes
  without resorting to the global `[jvm]` options.
- `file()` targets are now included in the Clojure test sandbox, so tests can
  read data files declared as dependencies.
- Non-Clojure `ClasspathEntryRequest` targets are now included on the
  `clojure_deploy_jar` classpath (#6).

### Fixed

- Tests now run correctly when metadata is present in the `ns` declaration.
- Excluded `META-INF/license` from uberjars to resolve a file/directory
  conflict during packaging (#4).

## [0.2.0] - 2026-03-22

### Added

- Runtime dependency inference and third-party dependency inference from the
  JVM lockfile.
- Miscellaneous dependency-inference and JAR-analysis improvements, with
  expanded integration test coverage (#2).

### Changed

- Faster linting.
- Updated clj-kondo and cljfmt to newer versions.
- Upgraded to Pants 2.30.0 and adopted the call-by-name rule API.

## [0.1.2] - 2026-03-13

### Added

- Documentation for generating `deps.edn`.
- A skill/workflow for updating the release version.

### Changed

- Split into multiple resolves to test the plugin against several Pants
  versions.

## [0.1.1] - 2026-03-13

### Changed

- Trimmed unnecessary dependencies.

## [0.1.0] - 2026-03-11

### Added

- Initial release of pants-backend-clojure
- **Target types**: `clojure_source`, `clojure_sources`, `clojure_test`, `clojure_tests`, `clojure_deploy_jar`
- **Dependency inference**: Automatic discovery from `require` and `import` forms
- **REPL support**: Interactive development with nREPL and rebel-readline
- **Testing**: Run `clojure.test` tests via `pants test`
- **Linting**: Static analysis with clj-kondo
- **Formatting**: Code formatting with cljfmt
- **Compilation checking**: Verify code compiles via `pants check`
- **Uberjar packaging**: Build executable JARs with AOT compilation and direct linking
- **Provided dependencies**: Maven-style provided scope for excluding runtime dependencies
- **JVM integration**: Works with Pants' JVM support for mixed Clojure/Java projects
