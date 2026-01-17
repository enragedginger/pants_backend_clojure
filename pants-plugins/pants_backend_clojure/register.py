"""Clojure backend for Pants."""

from pants_backend_clojure import (
    clojure_symbol_mapping,
    compile_clj,
    dependency_inference,
    namespace_analysis,
    provided_dependencies,
    tools_build_uberjar,
)
from pants_backend_clojure.subsystems import tools_build
from pants_backend_clojure.goals import (
    check,
    fmt,
    generate_clojure_lockfile_metadata,
    generate_deps,
    lint,
    package,
    repl,
    test,
)
from pants_backend_clojure.target_types import (
    ClojureDeployJarTarget,
    ClojureSourceTarget,
    ClojureSourcesGeneratorTarget,
    ClojureTestTarget,
    ClojureTestsGeneratorTarget,
    rules as target_type_rules,
)


def target_types():
    """Register target types with Pants."""
    return [
        ClojureSourceTarget,
        ClojureSourcesGeneratorTarget,
        ClojureTestTarget,
        ClojureTestsGeneratorTarget,
        ClojureDeployJarTarget,
    ]


def rules():
    """Register rules with Pants."""
    return [
        *target_type_rules(),
        *compile_clj.rules(),
        *provided_dependencies.rules(),
        *package.rules(),
        *fmt.rules(),
        *lint.rules(),
        *test.rules(),
        *repl.rules(),
        *dependency_inference.rules(),
        *generate_deps.rules(),
        *check.rules(),
        *clojure_symbol_mapping.rules(),
        *generate_clojure_lockfile_metadata.rules(),
        *namespace_analysis.rules(),
        *tools_build.rules(),
        *tools_build_uberjar.rules(),
    ]
