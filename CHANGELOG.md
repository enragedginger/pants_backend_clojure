# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

## [0.1.0] - 2026-01-17

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
