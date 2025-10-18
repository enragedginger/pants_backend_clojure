"""Clojure backend for Pants."""

from clojure_backend import (
    aot_compile,
    clj_fmt,
    clj_lint,
    clj_repl,
    clj_test_runner,
    compile_clj,
    dependency_inference,
    generate_deps_edn,
    package_clojure_deploy_jar,
)
from clojure_backend.target_types import (
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
        *aot_compile.rules(),
        *package_clojure_deploy_jar.rules(),
        *clj_fmt.rules(),
        *clj_lint.rules(),
        *clj_test_runner.rules(),
        *clj_repl.rules(),
        *dependency_inference.rules(),
        *generate_deps_edn.rules(),
    ]
