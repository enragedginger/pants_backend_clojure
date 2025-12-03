"""Tests for Clojure deploy jar packaging."""

from __future__ import annotations

from textwrap import dedent

import pytest

from clojure_backend.aot_compile import rules as aot_compile_rules
from clojure_backend import compile_clj
from clojure_backend.namespace_analysis import rules as namespace_analysis_rules
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
from pants.core.util_rules import config_files, external_tool, source_files, stripped_source_files, system_binaries
from pants.engine.fs import DigestContents
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.jvm import classpath, jvm_common, non_jvm_dependencies
from pants.jvm.goals import lockfile
from pants.jvm.resolve import coursier_fetch, jvm_tool
from pants.jvm.resolve.coursier_setup import rules as coursier_setup_rules
from pants.jvm.target_types import JvmArtifactTarget
from pants.jvm.util_rules import rules as jdk_util_rules
from pants.testutil.rule_runner import PYTHON_BOOTSTRAP_ENV, RuleRunner

from tests.clojure_test_fixtures import CLOJURE_LOCKFILE, CLOJURE_3RDPARTY_BUILD, CLOJURE_VERSION, LOCKFILE_WITH_JSR305

_JVM_RESOLVES = {
    "java17": "locks/jvm/java17.lock.jsonc",
}


@pytest.fixture
def rule_runner() -> RuleRunner:
    rule_runner = RuleRunner(
        preserve_tmpdirs=True,
        target_types=[ClojureSourceTarget, ClojureDeployJarTarget, JvmArtifactTarget],
        rules=[
            *package_rules(),
            *aot_compile_rules(),
            *namespace_analysis_rules(),
            *provided_dependencies_rules(),
            *classpath.rules(),
            *compile_clj.rules(),
            *config_files.rules(),
            *coursier_fetch.rules(),
            *coursier_setup_rules(),
            *external_tool.rules(),
            *jdk_util_rules(),
            *jvm_common.rules(),
            *jvm_tool.rules(),
            *lockfile.rules(),
            *non_jvm_dependencies.rules(),
            *source_files.rules(),
            *stripped_source_files.rules(),
            *system_binaries.rules(),
            *target_types_rules(),
            QueryRule(BuiltPackage, [ClojureDeployJarFieldSet]),
        ],
    )
    return rule_runner


def setup_rule_runner(rule_runner: RuleRunner) -> None:
    """Configure rule_runner with JVM options."""
    rule_runner.set_options(
        [
            f"--jvm-resolves={repr(_JVM_RESOLVES)}",
            "--jvm-default-resolve=java17",
        ],
        env_inherit=PYTHON_BOOTSTRAP_ENV,
    )


def test_package_simple_deploy_jar(rule_runner: RuleRunner) -> None:
    """Test packaging a simple clojure_deploy_jar."""
    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/hello/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
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
    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/bad/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
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
    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/app/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                clojure_source(
                    name="util",
                    source="util.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
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
    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/myapp/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                clojure_source(
                    name="config",
                    source="config.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
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
    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/custom/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
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
    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
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
    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/lib/BUILD": dedent(
                """\
                clojure_source(
                    name="util",
                    source="util.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
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
                    dependencies=["//src/lib:util", "3rdparty/jvm:org.clojure_clojure"],
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
    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/lib/BUILD": dedent(
                """\
                clojure_source(
                    name="api",
                    source="api.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
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
                    dependencies=["//src/lib:api", "3rdparty/jvm:org.clojure_clojure"],
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

    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/api/BUILD": dedent(
                """\
                clojure_source(
                    name="interface",
                    source="interface.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
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
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
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
                    dependencies=["//src/api:interface", "//src/lib:util", "3rdparty/jvm:org.clojure_clojure"],
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


def test_provided_jvm_artifact_excluded_from_jar(rule_runner: RuleRunner) -> None:
    """Test that provided jvm_artifact (third-party) dependencies are excluded from the final JAR.

    This test specifically verifies that the JAR filename matching logic correctly
    handles Pants/Coursier's naming convention: {group}_{artifact}_{version}.jar
    """
    import io
    import zipfile

    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_JSR305,
            "src/app/BUILD": dedent(
                f"""\
                jvm_artifact(
                    name="jsr305",
                    group="com.google.code.findbugs",
                    artifact="jsr305",
                    version="3.0.2",
                )

                jvm_artifact(
                    name="clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="{CLOJURE_VERSION}",
                )

                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=[":jsr305", ":clojure"],
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


def test_provided_maven_transitives_excluded_from_jar(rule_runner: RuleRunner) -> None:
    """Test that Maven transitive dependencies of provided artifacts are excluded from JAR.

    This is the key integration test for the Maven transitive exclusion feature.
    When org.clojure:clojure is marked as provided, its transitive dependencies
    (spec.alpha, core.specs.alpha) should also be excluded from the final JAR.

    This test also verifies the fix for the scheduler hang issue that occurred when
    a clojure_source depends directly on a jvm_artifact(clojure). Previously this
    caused a deadlock because both the tool classpath and user classpath tried to
    resolve org.clojure:clojure. Now we rely solely on the user's classpath.
    """
    import io
    import zipfile

    setup_rule_runner(rule_runner)
    # Test Maven transitive exclusion by having clojure_source depend on clojure directly
    # This used to cause a Pants scheduler hang, but should now work correctly
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_JSR305,
            "src/app/BUILD": dedent(
                f"""\
                jvm_artifact(
                    name="clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="{CLOJURE_VERSION}",
                )

                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=[":clojure"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="app.core",
                    dependencies=[":core"],
                    provided=[":clojure"],
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

    # The provided jvm_artifact (clojure) classes should NOT be present
    clojure_entries = [entry for entry in jar_entries if entry.startswith('clojure/')]
    assert len(clojure_entries) == 0, \
        f"Provided jvm_artifact org.clojure:clojure should NOT be in JAR, but found: {clojure_entries[:10]}"

    # MOST IMPORTANT: The TRANSITIVE dependencies should also NOT be present!
    # spec.alpha contains clojure/spec/alpha classes
    spec_alpha_entries = [entry for entry in jar_entries if 'clojure/spec/alpha' in entry]
    assert len(spec_alpha_entries) == 0, \
        f"Transitive dep spec.alpha should NOT be in JAR, but found: {spec_alpha_entries[:10]}"

    # core.specs.alpha contains clojure/core/specs/alpha classes
    core_specs_entries = [entry for entry in jar_entries if 'clojure/core/specs/alpha' in entry]
    assert len(core_specs_entries) == 0, \
        f"Transitive dep core.specs.alpha should NOT be in JAR, but found: {core_specs_entries[:10]}"
