"""Tests for Clojure deploy jar packaging."""

from __future__ import annotations

from textwrap import dedent

import pytest

from clojure_backend.aot_compile import rules as aot_compile_rules
from clojure_backend import compile_clj
from clojure_backend.provided_dependencies import rules as provided_dependencies_rules
from clojure_backend.goals.package import (
    ClojureDeployJarFieldSet,
    package_clojure_deploy_jar,
)
from clojure_backend.goals.package import rules as package_rules
from clojure_backend.target_types import (
    ClojureAOTNamespacesField,
    ClojureProvidedDependenciesField,
    ClojureDeployJarTarget,
    ClojureMainNamespaceField,
    ClojureSourceTarget,
)
from clojure_backend.target_types import rules as target_types_rules
from pants.build_graph.address import Address
from pants.core.goals.package import BuiltPackage
from pants.core.util_rules import source_files, stripped_source_files
from pants.engine.fs import DigestContents
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
            *package_rules(),
            *aot_compile_rules(),
            *provided_dependencies_rules(),
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
    assert "provided" in field_aliases
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


def test_provided_field_can_be_parsed(rule_runner: RuleRunner) -> None:
    """Test that provided field can be parsed and accessed."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/lib/BUILD": dedent(
                """\
                clojure_source(
                    name="api",
                    source="api.clj",
                )
                """
            ),
            "src/lib/api.clj": dedent(
                """\
                (ns lib.api)

                (defn api-fn []
                  "provided API")
                """
            ),
            "src/myapp/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/lib:api"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="myapp.core",
                    dependencies=[":core", "//src/lib:api"],
                    provided=["//src/lib:api"],
                )
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:require [lib.api])
                  (:gen-class))

                (defn -main [& args]
                  (println (lib.api/api-fn)))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/myapp", target_name="app"))

    # Verify the field exists and can be accessed
    assert target.has_field(ClojureProvidedDependenciesField)
    provided_field = target[ClojureProvidedDependenciesField]
    assert provided_field.value is not None

    # Create field set
    field_set = ClojureDeployJarFieldSet.create(target)
    assert field_set.provided is not None


def test_provided_dependencies_excluded_from_jar(rule_runner: RuleRunner) -> None:
    """Test that provided dependencies are excluded from the final JAR."""
    import zipfile

    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": "{}",
            "src/api/BUILD": dedent(
                """\
                clojure_source(
                    name="interface",
                    source="interface.clj",
                )
                """
            ),
            "src/api/interface.clj": dedent(
                """\
                (ns api.interface)

                (defn do-something []
                  "API function")
                """
            ),
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
                  "utility function")
                """
            ),
            "src/app/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/api:interface", "//src/lib:util"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="app.core",
                    dependencies=[":core", "//src/api:interface", "//src/lib:util"],
                    provided=["//src/api:interface"],
                )
                """
            ),
            "src/app/core.clj": dedent(
                """\
                (ns app.core
                  (:require [api.interface]
                            [lib.util])
                  (:gen-class))

                (defn -main [& args]
                  (println (lib.util/helper))
                  (println (api.interface/do-something)))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    # Package the JAR
    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    # Read the JAR and check its contents
    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None, f"Could not find JAR file {jar_path} in digest"

    # Parse the JAR and check what classes are included
    import io
    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        jar_entries = set(jar.namelist())

    # The main app classes should be present
    assert any('app/core' in entry for entry in jar_entries), \
        "Main app.core classes should be in JAR"

    # The runtime dependency (lib.util) classes should be present
    assert any('lib/util' in entry for entry in jar_entries), \
        "Runtime dependency lib.util classes should be in JAR"

    # The provided dependency (api.interface) classes should NOT be present
    api_entries = [entry for entry in jar_entries if 'api/interface' in entry]
    assert len(api_entries) == 0, \
        f"Provided dependency api.interface should NOT be in JAR, but found: {api_entries}"


# Lockfile content for third-party JAR test
# Contains jsr305 (small JAR, good for testing) and its dependencies
LOCKFILE_WITH_JSR305 = """\
# This lockfile was autogenerated by Pants. To regenerate, run:
#
#    pants generate-lockfiles
#
# --- BEGIN PANTS LOCKFILE METADATA: DO NOT EDIT OR REMOVE ---
# {
#   "version": 1,
#   "generated_with_requirements": [
#     "com.google.code.findbugs:jsr305:3.0.2,url=not_provided,jar=not_provided",
#     "org.clojure:clojure:1.11.0,url=not_provided,jar=not_provided"
#   ]
# }
# --- END PANTS LOCKFILE METADATA ---

[[entries]]
directDependencies = []
dependencies = []
file_name = "com.google.code.findbugs_jsr305_3.0.2.jar"

[entries.coord]
group = "com.google.code.findbugs"
artifact = "jsr305"
version = "3.0.2"
packaging = "jar"
[entries.file_digest]
fingerprint = "766ad2a0783f2687962c8ad74ceecc38a28b9f72a2d085ee438b7813e928d0c7"
serialized_bytes_length = 19936
[[entries]]
file_name = "org.clojure_clojure_1.11.0.jar"
[[entries.directDependencies]]
group = "org.clojure"
artifact = "core.specs.alpha"
version = "0.2.62"
packaging = "jar"

[[entries.directDependencies]]
group = "org.clojure"
artifact = "spec.alpha"
version = "0.3.218"
packaging = "jar"

[[entries.dependencies]]
group = "org.clojure"
artifact = "core.specs.alpha"
version = "0.2.62"
packaging = "jar"

[[entries.dependencies]]
group = "org.clojure"
artifact = "spec.alpha"
version = "0.3.218"
packaging = "jar"


[entries.coord]
group = "org.clojure"
artifact = "clojure"
version = "1.11.0"
packaging = "jar"
[entries.file_digest]
fingerprint = "3e21fa75a07ec9ddbbf1b2b50356cf180710d0398deaa4f44e91cd6304555947"
serialized_bytes_length = 4105010
[[entries]]
file_name = "org.clojure_core.specs.alpha_0.2.62.jar"
[[entries.directDependencies]]
group = "org.clojure"
artifact = "clojure"
version = "1.11.0"
packaging = "jar"

[[entries.dependencies]]
group = "org.clojure"
artifact = "clojure"
version = "1.11.0"
packaging = "jar"


[entries.coord]
group = "org.clojure"
artifact = "core.specs.alpha"
version = "0.2.62"
packaging = "jar"
[entries.file_digest]
fingerprint = "06eea8c070bbe45c158567e443439681bc8c46e9123414f81bfa32ba42d6cbc8"
serialized_bytes_length = 4325
[[entries]]
file_name = "org.clojure_spec.alpha_0.3.218.jar"
[[entries.directDependencies]]
group = "org.clojure"
artifact = "clojure"
version = "1.11.0"
packaging = "jar"

[[entries.dependencies]]
group = "org.clojure"
artifact = "clojure"
version = "1.11.0"
packaging = "jar"


[entries.coord]
group = "org.clojure"
artifact = "spec.alpha"
version = "0.3.218"
packaging = "jar"
[entries.file_digest]
fingerprint = "67ec898eb55c66a957a55279dd85d1376bb994bd87668b2b0de1eb3b97e8aae0"
serialized_bytes_length = 635617
"""


def test_provided_jvm_artifact_excluded_from_jar(rule_runner: RuleRunner) -> None:
    """Test that provided jvm_artifact (third-party) dependencies are excluded from the final JAR.

    This test specifically verifies that the JAR filename matching logic correctly
    handles Pants/Coursier's naming convention: {group}_{artifact}_{version}.jar
    """
    import io
    import zipfile

    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_JSR305,
            "src/app/BUILD": dedent(
                """\
                jvm_artifact(
                    name="jsr305",
                    group="com.google.code.findbugs",
                    artifact="jsr305",
                    version="3.0.2",
                )

                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=[":jsr305"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="app.core",
                    dependencies=[":core", ":jsr305"],
                    provided=[":jsr305"],
                )
                """
            ),
            "src/app/core.clj": dedent(
                """\
                (ns app.core
                  (:gen-class))

                (defn -main [& args]
                  (println "Hello"))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    # Package the JAR
    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    # Read the JAR and check its contents
    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None, f"Could not find JAR file {jar_path} in digest"

    # Parse the JAR and check what classes are included
    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        jar_entries = set(jar.namelist())

    # The main app classes should be present
    assert any('app/core' in entry for entry in jar_entries), \
        "Main app.core classes should be in JAR"

    # The provided jvm_artifact (jsr305) classes should NOT be present
    # jsr305 contains javax/annotation classes
    jsr305_entries = [entry for entry in jar_entries if 'javax/annotation' in entry]
    assert len(jsr305_entries) == 0, \
        f"Provided jvm_artifact jsr305 should NOT be in JAR, but found: {jsr305_entries}"
