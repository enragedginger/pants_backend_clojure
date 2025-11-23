"""Tests for compile dependency resolution."""

from __future__ import annotations

from textwrap import dedent

import pytest

from clojure_backend.compile_dependencies import (
    CompileOnlyDependencies,
    resolve_compile_only_dependencies,
)
from clojure_backend.compile_dependencies import rules as compile_dependencies_rules
from clojure_backend.target_types import (
    ClojureCompileDependenciesField,
    ClojureDeployJarTarget,
    ClojureSourceTarget,
)
from clojure_backend.target_types import rules as target_types_rules
from pants.build_graph.address import Address
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.jvm import classpath, jvm_common
from pants.jvm.resolve import coursier_fetch, jvm_tool
from pants.jvm.target_types import JvmArtifactTarget
from pants.testutil.rule_runner import RuleRunner


@pytest.fixture
def rule_runner() -> RuleRunner:
    rule_runner = RuleRunner(
        target_types=[ClojureSourceTarget, ClojureDeployJarTarget, JvmArtifactTarget],
        rules=[
            *compile_dependencies_rules(),
            *target_types_rules(),
            *classpath.rules(),
            *jvm_common.rules(),
            *coursier_fetch.rules(),
            *jvm_tool.rules(),
            QueryRule(CompileOnlyDependencies, [ClojureCompileDependenciesField]),
        ],
    )
    rule_runner.set_options(
        [
            "--jvm-resolves={'java17': 'locks/jvm/java17.lock.jsonc'}",
            "--jvm-default-resolve=java17",
        ]
    )
    return rule_runner


def test_empty_compile_dependencies(rule_runner: RuleRunner) -> None:
    """Test that empty compile_dependencies field returns empty set."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/hello/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                )

                clojure_deploy_jar(
                    name="app",
                    main="hello.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/hello/core.clj": "(ns hello.core (:gen-class))\n\n(defn -main [& args] (println \"Hello\"))",
        }
    )

    target = rule_runner.get_target(Address("src/hello", target_name="app"))
    field = target[ClojureCompileDependenciesField]

    result = rule_runner.request(CompileOnlyDependencies, [field])

    assert len(result.addresses) == 0


def test_single_compile_dependency_no_transitives(rule_runner: RuleRunner) -> None:
    """Test compile dependency with no transitive dependencies."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/lib/BUILD": dedent(
                """\
                clojure_source(
                    name="api",
                    source="api.clj",
                )

                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=[":api"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="lib.core",
                    dependencies=[":core", ":api"],
                    compile_dependencies=[":api"],
                )
                """
            ),
            "src/lib/api.clj": "(ns lib.api)",
            "src/lib/core.clj": "(ns lib.core (:require [lib.api]) (:gen-class))\n\n(defn -main [& args])",
        }
    )

    target = rule_runner.get_target(Address("src/lib", target_name="app"))
    field = target[ClojureCompileDependenciesField]

    result = rule_runner.request(CompileOnlyDependencies, [field])

    # Should include just the api target
    assert len(result.addresses) == 1
    assert Address("src/lib", target_name="api") in result.addresses


def test_compile_dependency_with_transitives(rule_runner: RuleRunner) -> None:
    """Test compile dependency with transitive dependencies."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/base/BUILD": dedent(
                """\
                clojure_source(
                    name="util",
                    source="util.clj",
                )
                """
            ),
            "src/base/util.clj": "(ns base.util)",
            "src/api/BUILD": dedent(
                """\
                clojure_source(
                    name="interface",
                    source="interface.clj",
                    dependencies=["//src/base:util"],
                )
                """
            ),
            "src/api/interface.clj": "(ns api.interface (:require [base.util]))",
            "src/app/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/api:interface"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="app.core",
                    dependencies=[":core", "//src/api:interface"],
                    compile_dependencies=["//src/api:interface"],
                )
                """
            ),
            "src/app/core.clj": "(ns app.core (:require [api.interface]) (:gen-class))\n\n(defn -main [& args])",
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field = target[ClojureCompileDependenciesField]

    result = rule_runner.request(CompileOnlyDependencies, [field])

    # Should include both api:interface and its transitive dependency base:util
    assert len(result.addresses) == 2
    assert Address("src/api", target_name="interface") in result.addresses
    assert Address("src/base", target_name="util") in result.addresses


def test_multiple_compile_dependencies(rule_runner: RuleRunner) -> None:
    """Test multiple compile-only dependencies."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/api1/BUILD": dedent(
                """\
                clojure_source(
                    name="lib",
                    source="lib.clj",
                )
                """
            ),
            "src/api1/lib.clj": "(ns api1.lib)",
            "src/api2/BUILD": dedent(
                """\
                clojure_source(
                    name="lib",
                    source="lib.clj",
                )
                """
            ),
            "src/api2/lib.clj": "(ns api2.lib)",
            "src/app/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/api1:lib", "//src/api2:lib"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="app.core",
                    dependencies=[":core", "//src/api1:lib", "//src/api2:lib"],
                    compile_dependencies=["//src/api1:lib", "//src/api2:lib"],
                )
                """
            ),
            "src/app/core.clj": "(ns app.core (:require [api1.lib] [api2.lib]) (:gen-class))\n\n(defn -main [& args])",
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field = target[ClojureCompileDependenciesField]

    result = rule_runner.request(CompileOnlyDependencies, [field])

    # Should include both api libraries
    assert len(result.addresses) == 2
    assert Address("src/api1", target_name="lib") in result.addresses
    assert Address("src/api2", target_name="lib") in result.addresses


def test_compile_dependency_with_shared_transitive(rule_runner: RuleRunner) -> None:
    """Test compile dependencies that share a common transitive dependency."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/common/BUILD": dedent(
                """\
                clojure_source(
                    name="util",
                    source="util.clj",
                )
                """
            ),
            "src/common/util.clj": "(ns common.util)",
            "src/api1/BUILD": dedent(
                """\
                clojure_source(
                    name="lib",
                    source="lib.clj",
                    dependencies=["//src/common:util"],
                )
                """
            ),
            "src/api1/lib.clj": "(ns api1.lib (:require [common.util]))",
            "src/api2/BUILD": dedent(
                """\
                clojure_source(
                    name="lib",
                    source="lib.clj",
                    dependencies=["//src/common:util"],
                )
                """
            ),
            "src/api2/lib.clj": "(ns api2.lib (:require [common.util]))",
            "src/app/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/api1:lib", "//src/api2:lib", "//src/common:util"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="app.core",
                    dependencies=[":core", "//src/api1:lib", "//src/api2:lib", "//src/common:util"],
                    compile_dependencies=["//src/api1:lib", "//src/api2:lib"],
                )
                """
            ),
            "src/app/core.clj": "(ns app.core (:require [api1.lib] [api2.lib] [common.util]) (:gen-class))\n\n(defn -main [& args])",
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field = target[ClojureCompileDependenciesField]

    result = rule_runner.request(CompileOnlyDependencies, [field])

    # Should include api1, api2, and their shared common.util dependency
    assert len(result.addresses) == 3
    assert Address("src/api1", target_name="lib") in result.addresses
    assert Address("src/api2", target_name="lib") in result.addresses
    assert Address("src/common", target_name="util") in result.addresses


def test_deep_transitive_chain(rule_runner: RuleRunner) -> None:
    """Test compile dependency with deep transitive chain."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/a/BUILD": "clojure_source(name='lib', source='lib.clj')",
            "src/a/lib.clj": "(ns a.lib)",
            "src/b/BUILD": dedent(
                """\
                clojure_source(
                    name='lib',
                    source='lib.clj',
                    dependencies=['//src/a:lib'],
                )
                """
            ),
            "src/b/lib.clj": "(ns b.lib (:require [a.lib]))",
            "src/c/BUILD": dedent(
                """\
                clojure_source(
                    name='lib',
                    source='lib.clj',
                    dependencies=['//src/b:lib'],
                )
                """
            ),
            "src/c/lib.clj": "(ns c.lib (:require [b.lib]))",
            "src/app/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/c:lib"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="app.core",
                    dependencies=[":core", "//src/c:lib"],
                    compile_dependencies=["//src/c:lib"],
                )
                """
            ),
            "src/app/core.clj": "(ns app.core (:require [c.lib]) (:gen-class))\n\n(defn -main [& args])",
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field = target[ClojureCompileDependenciesField]

    result = rule_runner.request(CompileOnlyDependencies, [field])

    # Should include the entire transitive chain: c -> b -> a
    assert len(result.addresses) == 3
    assert Address("src/c", target_name="lib") in result.addresses
    assert Address("src/b", target_name="lib") in result.addresses
    assert Address("src/a", target_name="lib") in result.addresses


def test_compile_dependencies_field_not_set(rule_runner: RuleRunner) -> None:
    """Test that targets without compile_dependencies field return empty set."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/hello/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                )

                clojure_deploy_jar(
                    name="app",
                    main="hello.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/hello/core.clj": "(ns hello.core (:gen-class))\n\n(defn -main [& args])",
        }
    )

    target = rule_runner.get_target(Address("src/hello", target_name="app"))
    # Get the field even though it wasn't set (should have empty value)
    field = target.get(ClojureCompileDependenciesField)

    result = rule_runner.request(CompileOnlyDependencies, [field])

    assert len(result.addresses) == 0
