"""Tests for Clojure deploy jar packaging."""

from __future__ import annotations

from textwrap import dedent

import pytest

from clojure_backend.aot_compile import rules as aot_compile_rules
from clojure_backend import compile_clj
from clojure_backend.package_clojure_deploy_jar import (
    ClojureDeployJarFieldSet,
    package_clojure_deploy_jar,
)
from clojure_backend.package_clojure_deploy_jar import rules as package_rules
from clojure_backend.target_types import (
    ClojureAOTNamespacesField,
    ClojureDeployJarTarget,
    ClojureMainNamespaceField,
    ClojureSourceTarget,
)
from clojure_backend.target_types import rules as target_types_rules
from pants.build_graph.address import Address
from pants.core.goals.package import BuiltPackage
from pants.core.util_rules import source_files, stripped_source_files
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.jvm import classpath, jvm_common
from pants.jvm.resolve import coursier_fetch, jvm_tool
from pants.testutil.rule_runner import RuleRunner


@pytest.fixture
def rule_runner() -> RuleRunner:
    rule_runner = RuleRunner(
        target_types=[ClojureSourceTarget, ClojureDeployJarTarget],
        rules=[
            *package_rules(),
            *aot_compile_rules(),
            *classpath.rules(),
            *compile_clj.rules(),
            *target_types_rules(),
            *jvm_common.rules(),
            *source_files.rules(),
            *stripped_source_files.rules(),
            *coursier_fetch.rules(),
            *jvm_tool.rules(),
            QueryRule(BuiltPackage, [ClojureDeployJarFieldSet]),
        ],
    )
    rule_runner.set_options(
        [
            "--jvm-resolves={'java17': 'locks/jvm/java17.lock.jsonc'}",
            "--jvm-default-resolve=java17",
        ]
    )
    return rule_runner


def test_package_simple_deploy_jar(rule_runner: RuleRunner) -> None:
    """Test packaging a simple clojure_deploy_jar."""
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
            "src/hello/core.clj": dedent(
                """\
                (ns hello.core
                  (:gen-class))

                (defn -main
                  [& args]
                  (println "Hello, World!"))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/hello", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])

    # Should produce a JAR artifact
    assert len(result.artifacts) == 1
    assert result.artifacts[0].relpath.endswith(".jar")


def test_package_deploy_jar_validates_gen_class(rule_runner: RuleRunner) -> None:
    """Test that packaging fails if main namespace doesn't have gen-class."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/bad/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                )

                clojure_deploy_jar(
                    name="app",
                    main="bad.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/bad/core.clj": dedent(
                """\
                (ns bad.core)

                (defn -main
                  [& args]
                  (println "Missing gen-class!"))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/bad", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    # Should raise an error about missing gen-class
    with pytest.raises(ExecutionError) as exc_info:
        rule_runner.request(BuiltPackage, [field_set])

    # Verify the wrapped exception is a ValueError with the right message
    assert len(exc_info.value.wrapped_exceptions) == 1
    wrapped_exc = exc_info.value.wrapped_exceptions[0]
    assert isinstance(wrapped_exc, ValueError)
    assert "must include" in str(wrapped_exc)
    assert "gen-class" in str(wrapped_exc)


def test_package_deploy_jar_with_aot_all(rule_runner: RuleRunner) -> None:
    """Test packaging with aot=':all' compiles all namespaces."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/app/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                )
                clojure_source(
                    name="util",
                    source="util.clj",
                )

                clojure_deploy_jar(
                    name="app",
                    main="app.core",
                    aot=[":all"],
                    dependencies=[":core", ":util"],
                )
                """
            ),
            "src/app/core.clj": dedent(
                """\
                (ns app.core
                  (:require [app.util])
                  (:gen-class))

                (defn -main [& args]
                  (println "App"))
                """
            ),
            "src/app/util.clj": dedent(
                """\
                (ns app.util)

                (defn helper []
                  "helper")
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])

    # Should produce a JAR
    assert len(result.artifacts) == 1


def test_package_deploy_jar_with_selective_aot(rule_runner: RuleRunner) -> None:
    """Test packaging with selective AOT compilation."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/myapp/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                )
                clojure_source(
                    name="config",
                    source="config.clj",
                )

                clojure_deploy_jar(
                    name="app",
                    main="myapp.core",
                    aot=["myapp.core", "myapp.config"],
                    dependencies=[":core", ":config"],
                )
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:gen-class))

                (defn -main [& args]
                  (println "MyApp"))
                """
            ),
            "src/myapp/config.clj": dedent(
                """\
                (ns myapp.config)

                (def config {:port 8080})
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/myapp", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])

    # Should produce a JAR
    assert len(result.artifacts) == 1


def test_clojure_main_namespace_field_required() -> None:
    """Test that ClojureMainNamespaceField is required."""
    assert ClojureMainNamespaceField.required is True


def test_clojure_aot_namespaces_field_default() -> None:
    """Test that ClojureAOTNamespacesField has empty default."""
    assert ClojureAOTNamespacesField.default == ()


def test_clojure_deploy_jar_target_has_required_fields() -> None:
    """Test that ClojureDeployJarTarget has the expected core fields."""
    # Check that main field is in core_fields
    field_aliases = {field.alias for field in ClojureDeployJarTarget.core_fields}
    assert "main" in field_aliases
    assert "aot" in field_aliases
    assert "dependencies" in field_aliases
    assert "resolve" in field_aliases


def test_package_deploy_jar_with_custom_gen_class_name(rule_runner: RuleRunner) -> None:
    """Test packaging with a custom gen-class :name."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/custom/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                )

                clojure_deploy_jar(
                    name="app",
                    main="custom.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/custom/core.clj": dedent(
                """\
                (ns custom.core
                  (:gen-class
                    :name custom.MyMainClass))

                (defn -main
                  [& args]
                  (println "Custom class name!"))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/custom", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    # Should not raise an error and should package successfully
    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1


def test_package_deploy_jar_missing_main_namespace(rule_runner: RuleRunner) -> None:
    """Test that packaging fails if main namespace source is not found."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/missing/BUILD": dedent(
                """\
                clojure_deploy_jar(
                    name="app",
                    main="missing.nonexistent",
                )
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/missing", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    # Should raise an error about missing namespace
    with pytest.raises(ExecutionError) as exc_info:
        rule_runner.request(BuiltPackage, [field_set])

    # Verify the wrapped exception is a ValueError with the right message
    assert len(exc_info.value.wrapped_exceptions) == 1
    wrapped_exc = exc_info.value.wrapped_exceptions[0]
    assert isinstance(wrapped_exc, ValueError)
    assert "Could not find source file" in str(wrapped_exc)


def test_package_deploy_jar_with_transitive_dependencies(rule_runner: RuleRunner) -> None:
    """Test packaging with transitive dependencies."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/lib/BUILD": dedent(
                """\
                clojure_source(
                    name="util",
                    source="util.clj",
                )
                """
            ),
            "src/lib/util.clj": dedent(
                """\
                (ns lib.util)

                (defn helper []
                  "utility")
                """
            ),
            "src/app/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/lib:util"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="app.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/app/core.clj": dedent(
                """\
                (ns app.core
                  (:require [lib.util])
                  (:gen-class))

                (defn -main [& args]
                  (println (lib.util/helper)))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    # Should compile successfully with transitive dependencies
    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1
