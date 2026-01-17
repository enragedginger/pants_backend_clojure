# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
