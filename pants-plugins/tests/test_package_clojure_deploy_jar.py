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


def test_clojure_main_namespace_field_required() -> None:
    """Test that ClojureMainNamespaceField is required."""
    assert ClojureMainNamespaceField.required is True


def test_clojure_deploy_jar_target_has_required_fields() -> None:
    """Test that ClojureDeployJarTarget has the expected core fields."""
    # Check that main field is in core_fields
    field_aliases = {field.alias for field in ClojureDeployJarTarget.core_fields}
    assert "main" in field_aliases
    assert "dependencies" in field_aliases
    assert "provided" in field_aliases
    assert "resolve" in field_aliases
    # aot field should NOT be present (removed in simplification)
    assert "aot" not in field_aliases


def test_package_deploy_jar_with_custom_gen_class_name(rule_runner: RuleRunner) -> None:
    """Test that (:gen-class :name X) generates X.class in JAR."""
    import io
    import zipfile

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
                  (:gen-class :name custom.MyMainClass))

                (defn -main
                  [& args]
                  (println "Custom class name!"))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/custom", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    # Build the JAR
    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    # Extract and verify JAR contents
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_path = result.artifacts[0].relpath
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None, f"Could not find JAR file {jar_path} in digest"

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        entries = set(jar.namelist())

        # Verify namespace init class is present
        assert 'custom/core__init.class' in entries, \
            f"Namespace init class not found. Entries: {sorted(entries)}"

        # Verify custom gen-class :name class is present
        assert 'custom/MyMainClass.class' in entries, \
            f"Custom gen-class class not found. Entries: {sorted(entries)}"

        # Verify manifest has correct Main-Class
        manifest = jar.read('META-INF/MANIFEST.MF').decode()
        assert 'Main-Class: custom.MyMainClass' in manifest, \
            f"Wrong Main-Class in manifest: {manifest}"


def test_package_deploy_jar_multiple_gen_class_names(rule_runner: RuleRunner) -> None:
    """Test that multiple (:gen-class :name) declarations all get included."""
    import io
    import zipfile

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
                    name="helper",
                    source="helper.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="app.core",
                    dependencies=[":core", ":helper"],
                )
                """
            ),
            "src/app/core.clj": dedent(
                """\
                (ns app.core
                  (:require [app.helper])
                  (:gen-class :name com.example.Main))

                (defn -main [& args]
                  (app.helper/help))
                """
            ),
            "src/app/helper.clj": dedent(
                """\
                (ns app.helper
                  (:gen-class :name com.example.Helper))

                (defn help [] nil)
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    # Build and verify both custom classes are present
    result = rule_runner.request(BuiltPackage, [field_set])

    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_path = result.artifacts[0].relpath
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        entries = set(jar.namelist())
        assert 'com/example/Main.class' in entries, \
            f"Main gen-class not found. Entries: {sorted(entries)}"
        assert 'com/example/Helper.class' in entries, \
            f"Helper gen-class not found. Entries: {sorted(entries)}"


def test_package_deploy_jar_gen_class_without_name(rule_runner: RuleRunner) -> None:
    """Test that standard (:gen-class) without :name works correctly."""
    import io
    import zipfile

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
                  (:gen-class))

                (defn -main [& args]
                  (println "Hello"))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)
    result = rule_runner.request(BuiltPackage, [field_set])

    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_path = result.artifacts[0].relpath
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        entries = set(jar.namelist())

        # Standard gen-class generates namespace-named class
        assert 'app/core.class' in entries, \
            f"Standard gen-class class not found. Entries: {sorted(entries)}"
        assert 'app/core__init.class' in entries

        manifest = jar.read('META-INF/MANIFEST.MF').decode()
        assert 'Main-Class: app.core' in manifest


def test_package_deploy_jar_gen_class_name_after_other_options(rule_runner: RuleRunner) -> None:
    """Test that :name is detected even when it appears after other gen-class options."""
    import io
    import zipfile

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
                  (:gen-class
                    :init init
                    :state state
                    :name com.example.ComplexApp
                    :methods [[getValue [] String]]))

                (defn -init []
                  [[] (atom "hello")])

                (defn -getValue [this]
                  @(.state this))

                (defn -main [& args]
                  (println "Complex gen-class"))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)
    result = rule_runner.request(BuiltPackage, [field_set])

    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_path = result.artifacts[0].relpath
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        entries = set(jar.namelist())

        # Custom gen-class with :name after other options should be detected
        assert 'com/example/ComplexApp.class' in entries, \
            f"Complex gen-class class not found. Entries: {sorted(entries)}"

        manifest = jar.read('META-INF/MANIFEST.MF').decode()
        assert 'Main-Class: com.example.ComplexApp' in manifest


def test_package_deploy_jar_with_defrecord_deftype(rule_runner: RuleRunner) -> None:
    """Test that defrecord/deftype/defprotocol classes are included in JAR.

    These generate classes in subdirectories (e.g., my/app/core/MyRecord.class)
    rather than using the $ convention for inner classes.
    """
    import io
    import zipfile

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
                  (:gen-class))

                (defrecord MyRecord [field1 field2])

                (deftype MyType [state])

                (defprotocol MyProtocol
                  (do-something [this]))

                (defn -main [& args]
                  (println (->MyRecord 1 2)))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)
    result = rule_runner.request(BuiltPackage, [field_set])

    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_path = result.artifacts[0].relpath
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        entries = set(jar.namelist())

        # Namespace init class
        assert 'app/core__init.class' in entries, \
            f"Namespace init class not found. Entries: {sorted(e for e in entries if e.startswith('app/'))}"

        # defrecord generates class in subdirectory
        assert 'app/core/MyRecord.class' in entries, \
            f"defrecord class not found. Entries: {sorted(e for e in entries if e.startswith('app/'))}"

        # deftype generates class in subdirectory
        assert 'app/core/MyType.class' in entries, \
            f"deftype class not found. Entries: {sorted(e for e in entries if e.startswith('app/'))}"

        # defprotocol generates interface in subdirectory
        assert 'app/core/MyProtocol.class' in entries, \
            f"defprotocol class not found. Entries: {sorted(e for e in entries if e.startswith('app/'))}"


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


def test_transitive_maven_deps_included_in_jar(rule_runner: RuleRunner) -> None:
    """Test that transitive Maven dependencies ARE included in the final JAR.

    This is a critical test to verify that the full transitive closure of Maven
    dependencies is bundled into the uberjar. When app depends on org.clojure:clojure,
    its transitive dependencies (spec.alpha, core.specs.alpha) should be included.

    This test is the positive counterpart to test_provided_maven_transitives_excluded_from_jar.
    """
    import io
    import zipfile

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
                  (:require [clojure.spec.alpha :as s])
                  (:gen-class))

                (defn -main [& args]
                  (println "Using spec:" (s/valid? int? 42)))
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

    # Direct dependency: org.clojure:clojure classes should be present
    clojure_core_entries = [entry for entry in jar_entries if entry.startswith('clojure/core')]
    assert len(clojure_core_entries) > 0, \
        "Direct dependency org.clojure:clojure classes should be in JAR"

    # CRITICAL: Transitive dependencies should ALSO be present!
    # spec.alpha is a transitive dep of clojure - contains clojure/spec/alpha classes
    spec_alpha_entries = [entry for entry in jar_entries if 'clojure/spec/alpha' in entry]
    assert len(spec_alpha_entries) > 0, \
        "Transitive dep spec.alpha classes should be in JAR (transitive of org.clojure:clojure)"

    # core.specs.alpha is also a transitive dep - contains clojure/core/specs/alpha classes
    core_specs_entries = [entry for entry in jar_entries if 'clojure/core/specs/alpha' in entry]
    assert len(core_specs_entries) > 0, \
        "Transitive dep core.specs.alpha classes should be in JAR (transitive of org.clojure:clojure)"


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


# =============================================================================
# Tests for AOT-first, JAR-override packaging behavior
# =============================================================================
# These tests verify that AOT-compiled classes are added first, then dependency
# JAR contents override them. This ensures:
# 1. Source-only third-party libraries work (their AOT classes are included)
# 2. Pre-compiled libraries work (their JAR classes override AOT for protocol safety)
#
# NOTE ON TEST COVERAGE:
# We cannot easily test with real source-only Maven libraries because:
# 1. All Maven artifacts in our test lockfiles have pre-compiled classes
# 2. Creating synthetic source-only JARs in tests is complex
#
# Instead, we use first-party clojure_source targets as proxies for source-only
# libraries - they behave identically (no JAR with pre-compiled classes).
# The test_transitive_first_party_classes_included test specifically verifies
# that classes for transitive dependencies without JARs are included.


def test_aot_classes_included_then_jar_overrides(rule_runner: RuleRunner) -> None:
    """Integration test: verify AOT classes are added first, then JAR contents override.

    This tests the core behavior of the packaging logic:
    1. All AOT-compiled classes are added first (project + third-party transitives)
    2. Dependency JAR contents are extracted and override existing entries
    3. For pre-compiled libraries, JAR classes win (protocol safety)
    4. For source-only libraries, AOT classes remain (they're needed at runtime)
    """
    import io
    import zipfile

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

                clojure_deploy_jar(
                    name="app",
                    main="myapp.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:require [clojure.string :as str])
                  (:gen-class))

                (defn -main [& args]
                  (println (str/upper-case "hello")))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/myapp", target_name="app"))
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

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        jar_entries = set(jar.namelist())

    # Project classes should be present (from AOT, first pass)
    myapp_classes = [e for e in jar_entries if e.startswith('myapp/')]
    assert len(myapp_classes) > 0, "Project myapp classes should be in JAR"

    # Clojure core classes should be present
    # They come from the Clojure JAR (second pass, overrides AOT versions)
    # This ensures correct protocol identity for pre-compiled libraries
    clojure_classes = [e for e in jar_entries if e.startswith('clojure/')]
    assert len(clojure_classes) > 0, "Clojure classes should be in JAR (from dependency JARs)"


def test_transitive_first_party_classes_included(rule_runner: RuleRunner) -> None:
    """Verify that transitive first-party dependencies have their AOT classes included.

    This test is critical for catching regressions like the source-only library bug.
    First-party clojure_source targets behave like source-only third-party libraries:
    they have no JAR with pre-compiled classes, so their AOT classes MUST be included.

    The previous bug filtered out ALL non-project-namespace AOT classes, which would
    break this scenario. This test ensures transitive first-party deps work correctly.
    """
    import io
    import zipfile

    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            # A "library" namespace that will be transitively compiled
            "src/mylib/BUILD": dedent(
                """\
                clojure_source(
                    name="utils",
                    source="utils.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/mylib/utils.clj": dedent(
                """\
                (ns mylib.utils)

                (defn format-greeting [name]
                  (str "Hello, " name "!"))
                """
            ),
            # The main app that depends on the library
            "src/myapp/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/mylib:utils", "3rdparty/jvm:org.clojure_clojure"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="myapp.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:require [mylib.utils :as utils])
                  (:gen-class))

                (defn -main [& args]
                  (println (utils/format-greeting "World")))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/myapp", target_name="app"))
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

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        jar_entries = set(jar.namelist())

    # Main app classes should be present
    myapp_classes = [e for e in jar_entries if e.startswith('myapp/')]
    assert len(myapp_classes) > 0, "Main app myapp classes should be in JAR"

    # CRITICAL: The transitive library classes MUST be present
    # These come from AOT compilation only (no JAR to extract from)
    # This is the scenario that broke with source-only third-party libraries
    mylib_classes = [e for e in jar_entries if e.startswith('mylib/')]
    assert len(mylib_classes) > 0, (
        "Transitive first-party library mylib classes should be in JAR. "
        "This simulates source-only third-party libraries which have no pre-compiled JAR classes."
    )

    # Verify specific class patterns exist for the library
    assert any('mylib/utils' in e and e.endswith('.class') for e in mylib_classes), \
        "mylib.utils namespace classes should be present"


def test_deeply_nested_transitive_deps_included(rule_runner: RuleRunner) -> None:
    """Verify that deeply nested transitive dependencies have their AOT classes included.

    Tests a chain: app -> lib-a -> lib-b -> lib-c
    All intermediate library classes must be in the final JAR.
    """
    import io
    import zipfile

    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            # Deepest dependency
            "src/lib_c/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/lib_c/core.clj": dedent(
                """\
                (ns lib-c.core)
                (def value-c "from-lib-c")
                """
            ),
            # Middle dependency
            "src/lib_b/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/lib_c:core", "3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/lib_b/core.clj": dedent(
                """\
                (ns lib-b.core
                  (:require [lib-c.core :as c]))
                (def value-b (str "from-lib-b+" c/value-c))
                """
            ),
            # Direct dependency
            "src/lib_a/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/lib_b:core", "3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/lib_a/core.clj": dedent(
                """\
                (ns lib-a.core
                  (:require [lib-b.core :as b]))
                (def value-a (str "from-lib-a+" b/value-b))
                """
            ),
            # Main app
            "src/app/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/lib_a:core", "3rdparty/jvm:org.clojure_clojure"],
                )

                clojure_deploy_jar(
                    name="myapp",
                    main="app.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/app/core.clj": dedent(
                """\
                (ns app.core
                  (:require [lib-a.core :as a])
                  (:gen-class))

                (defn -main [& args]
                  (println a/value-a))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="myapp"))
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

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        jar_entries = set(jar.namelist())

    # All namespaces in the chain must have their classes present
    app_classes = [e for e in jar_entries if e.startswith('app/')]
    lib_a_classes = [e for e in jar_entries if e.startswith('lib_a/')]
    lib_b_classes = [e for e in jar_entries if e.startswith('lib_b/')]
    lib_c_classes = [e for e in jar_entries if e.startswith('lib_c/')]

    assert len(app_classes) > 0, "app namespace classes should be in JAR"
    assert len(lib_a_classes) > 0, "lib-a namespace classes should be in JAR (direct dep)"
    assert len(lib_b_classes) > 0, "lib-b namespace classes should be in JAR (transitive dep)"
    assert len(lib_c_classes) > 0, "lib-c namespace classes should be in JAR (deep transitive dep)"


def test_no_duplicate_entries_in_jar(rule_runner: RuleRunner) -> None:
    """Verify that the final JAR has no duplicate entries.

    Duplicate entries in JAR files have undefined behavior across different
    JVM implementations and tools. This test ensures:
    1. AOT classes that exist in dependency JARs are skipped (not duplicated)
    2. Each class appears exactly once in the final JAR
    3. JAR entries from dependency JARs are used instead of AOT-compiled versions
       for protocol safety

    The pre-scan approach identifies classes in dependency JARs before writing
    any AOT classes, allowing us to skip AOT classes that would be overwritten.
    """
    import io
    import zipfile

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

                clojure_deploy_jar(
                    name="app",
                    main="myapp.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:require [clojure.string :as str])
                  (:gen-class))

                (defn -main [& args]
                  (println (str/upper-case "hello")))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/myapp", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    # Package the JAR
    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    # Read the JAR
    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    # Parse the JAR and check for duplicate entries
    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        entries = jar.namelist()
        unique_entries = set(entries)

        # Check for duplicates
        if len(entries) != len(unique_entries):
            # Find and report the duplicates
            from collections import Counter
            entry_counts = Counter(entries)
            duplicates = [entry for entry, count in entry_counts.items() if count > 1]
            pytest.fail(
                f"JAR has {len(entries) - len(unique_entries)} duplicate entries: "
                f"{duplicates[:10]}{'...' if len(duplicates) > 10 else ''}"
            )

    # Verify both AOT and JAR classes are present (no missing coverage)
    # Project classes from AOT (not in any JAR)
    myapp_classes = [e for e in unique_entries if e.startswith('myapp/')]
    assert len(myapp_classes) > 0, "Project myapp classes should be in JAR"

    # Clojure classes from dependency JAR (not from AOT)
    clojure_classes = [e for e in unique_entries if e.startswith('clojure/')]
    assert len(clojure_classes) > 0, "Clojure classes should be in JAR"


# =============================================================================
# Tests for first-party-only AOT filtering
# =============================================================================
# These tests verify the new behavior where only first-party AOT classes are
# included, and all third-party content comes from dependency JARs.


def test_only_first_party_aot_classes_included(rule_runner: RuleRunner) -> None:
    """Verify that only first-party namespace classes are included from AOT.

    This tests the core filtering logic:
    1. First-party classes (from clojure_source targets) are included from AOT
    2. Inner classes ($) for first-party namespaces are handled correctly
    3. __init classes for first-party namespaces are handled correctly
    """
    import io
    import zipfile

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

                clojure_deploy_jar(
                    name="app",
                    main="myapp.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:require [clojure.string :as str])
                  (:gen-class))

                (defn -main [& args]
                  (println (str/upper-case "hello")))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/myapp", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        jar_entries = set(jar.namelist())

    # First-party classes should be present
    myapp_classes = [e for e in jar_entries if e.startswith('myapp/')]
    assert len(myapp_classes) > 0, "First-party myapp classes should be in JAR"

    # Verify specific class patterns for first-party namespace
    assert any('myapp/core' in e and e.endswith('.class') for e in myapp_classes), \
        "myapp.core namespace classes should be present"
    # __init class should be present
    assert any('myapp/core__init.class' in e for e in myapp_classes), \
        "myapp.core __init class should be present"
    # Inner classes ($) should be handled correctly if present
    # (gen-class often creates inner classes for interfaces)
    inner_classes = [e for e in myapp_classes if '$' in e]
    # Note: We don't require inner classes since gen-class may not create them,
    # but if they exist, they should match our namespace
    for entry in inner_classes:
        assert entry.startswith('myapp/'), \
            f"Inner class {entry} should be under myapp/ namespace"


def test_third_party_classes_not_from_aot(rule_runner: RuleRunner) -> None:
    """Verify that third-party classes (e.g., clojure/core*.class) are NOT from AOT.

    The third-party classes should come from the dependency JARs, not from AOT.
    This is critical for protocol safety - AOT-compiled third-party classes have
    different protocol identities than the JAR versions.

    We verify this by checking that clojure classes exist in the JAR (from the
    Clojure dependency JAR) but we don't have a way to directly check "not from AOT"
    without timestamps. Instead, we verify the expected behavior that third-party
    classes ARE present (they come from JARs, not discarded).
    """
    import io
    import zipfile

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

                clojure_deploy_jar(
                    name="app",
                    main="myapp.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:require [clojure.string :as str])
                  (:gen-class))

                (defn -main [& args]
                  (println (str/upper-case "hello")))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/myapp", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        jar_entries = set(jar.namelist())

    # Third-party Clojure classes should be present (from JAR, not AOT)
    clojure_core_classes = [e for e in jar_entries if e.startswith('clojure/core')]
    assert len(clojure_core_classes) > 0, \
        "Third-party clojure.core classes should be in JAR (from dependency JAR)"

    # clojure.string classes should be present
    clojure_string_classes = [e for e in jar_entries if e.startswith('clojure/string')]
    assert len(clojure_string_classes) > 0, \
        "Third-party clojure.string classes should be in JAR (from dependency JAR)"


def test_third_party_content_extracted_from_jars(rule_runner: RuleRunner) -> None:
    """Verify that all third-party content (.class, .clj, resources) comes from JARs.

    This test ensures that source-only libraries have their .clj files included
    from the JAR. We check for various types of content from the Clojure JAR.
    """
    import io
    import zipfile

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

                clojure_deploy_jar(
                    name="app",
                    main="myapp.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:gen-class))

                (defn -main [& args]
                  (println "Hello"))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/myapp", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        jar_entries = set(jar.namelist())

    # Check for .class files from Clojure JAR
    clojure_class_files = [e for e in jar_entries if e.startswith('clojure/') and e.endswith('.class')]
    assert len(clojure_class_files) > 0, "Clojure .class files should be extracted from JAR"

    # Check for .clj files from Clojure JAR (Clojure includes source in its JAR)
    clojure_clj_files = [e for e in jar_entries if e.startswith('clojure/') and e.endswith('.clj')]
    assert len(clojure_clj_files) > 0, "Clojure .clj files should be extracted from JAR"


def test_hyphenated_namespace_classes_included(rule_runner: RuleRunner) -> None:
    """Verify that first-party namespaces with hyphens are handled correctly.

    Clojure converts hyphens to underscores in class file names:
    - Namespace: my-lib.core
    - Class file: my_lib/core.class

    This test ensures the hyphen-to-underscore conversion works correctly
    for first-party namespace detection.
    """
    import io
    import zipfile

    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/my_lib/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/my_lib/core.clj": dedent(
                """\
                (ns my-lib.core)

                (defn helper []
                  "helper function")
                """
            ),
            "src/myapp/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/my_lib:core", "3rdparty/jvm:org.clojure_clojure"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="myapp.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:require [my-lib.core :as lib])
                  (:gen-class))

                (defn -main [& args]
                  (println (lib/helper)))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/myapp", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        jar_entries = set(jar.namelist())

    # my-lib.core should produce my_lib/core*.class files
    my_lib_classes = [e for e in jar_entries if e.startswith('my_lib/')]
    assert len(my_lib_classes) > 0, (
        "Hyphenated namespace my-lib.core should have classes in JAR as my_lib/core*.class"
    )

    # Verify the core class specifically
    assert any('my_lib/core' in e and e.endswith('.class') for e in my_lib_classes), \
        "my_lib/core classes should be present"


def test_hyphenated_main_namespace(rule_runner: RuleRunner) -> None:
    """Verify that hyphenated main namespaces work correctly.

    When the main namespace has hyphens (e.g., my-app.core), the generated
    class name should use underscores (my_app.core). This test ensures the
    main class name is correctly munged.
    """
    import io
    import zipfile

    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/my_app/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="my-app.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/my_app/core.clj": dedent(
                """\
                (ns my-app.core
                  (:gen-class))

                (defn -main [& args]
                  (println "Hello from my-app!"))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/my_app", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        jar_entries = set(jar.namelist())
        manifest = jar.read('META-INF/MANIFEST.MF').decode('utf-8')

    # Main class should be munged to use underscores
    assert 'Main-Class: my_app.core' in manifest, (
        f"Main-Class should be 'my_app.core' (munged from my-app.core), got: {manifest}"
    )

    # The class files should exist with underscored path
    my_app_classes = [e for e in jar_entries if e.startswith('my_app/')]
    assert len(my_app_classes) > 0, (
        "Hyphenated main namespace my-app.core should have classes in JAR as my_app/core*.class"
    )


# =============================================================================
# Tests for source-only JARs (main="clojure.main")
# =============================================================================


def test_package_deploy_jar_clojure_main_source_only(rule_runner: RuleRunner) -> None:
    """Test that main='clojure.main' creates a source-only JAR."""
    import io
    import zipfile

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

                clojure_deploy_jar(
                    name="app",
                    main="clojure.main",  # Source-only mode
                    dependencies=[":core"],
                )
                """
            ),
            # No (:gen-class) needed since we're not AOT compiling app code
            "src/app/core.clj": dedent(
                """\
                (ns app.core)

                (defn -main [& args]
                  (println "Hello"))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])

    # Verify JAR was created
    assert len(result.artifacts) == 1

    # Extract and examine JAR contents
    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        entries = jar.namelist()

        # Should have first-party source files
        source_files = [e for e in entries if e.endswith('.clj') and e.startswith('app/')]
        assert len(source_files) > 0, \
            f"Expected first-party source file not found in {entries}"
        assert 'app/core.clj' in entries, \
            f"Expected app/core.clj in JAR, found: {entries}"

        # Should NOT have first-party compiled classes
        first_party_classes = [e for e in entries if e.startswith('app/') and e.endswith('.class')]
        assert not first_party_classes, \
            f"Unexpected first-party classes in source-only JAR: {first_party_classes}"

        # Should have Clojure runtime (from dependency JARs)
        assert any('clojure/core' in e for e in entries), \
            "Clojure runtime not found in JAR"

        # Check manifest - should have Main-Class: clojure.main
        manifest = jar.read('META-INF/MANIFEST.MF').decode()
        assert 'X-Source-Only: true' in manifest, \
            f"Expected X-Source-Only manifest attribute, got: {manifest}"
        assert 'Main-Class: clojure.main' in manifest, \
            f"Expected Main-Class: clojure.main manifest attribute, got: {manifest}"


def test_package_deploy_jar_clojure_main_no_gen_class_required(rule_runner: RuleRunner) -> None:
    """Test that clojure.main mode doesn't require (:gen-class)."""
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

                clojure_deploy_jar(
                    name="app",
                    main="clojure.main",  # Source-only mode
                    dependencies=[":core"],
                )
                """
            ),
            # Note: NO (:gen-class) in ns declaration - not required for clojure.main
            "src/app/core.clj": dedent(
                """\
                (ns app.core)

                (defn -main [& args]
                  (println "Hi"))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    # Should NOT raise ValueError about missing gen-class
    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1


def test_package_deploy_jar_clojure_main_includes_cljc(rule_runner: RuleRunner) -> None:
    """Test that clojure.main mode includes .cljc files."""
    import io
    import zipfile

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
                    dependencies=[":util", "3rdparty/jvm:org.clojure_clojure"],
                )

                clojure_source(
                    name="util",
                    source="util.cljc",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="clojure.main",  # Source-only mode
                    dependencies=[":core"],
                )
                """
            ),
            "src/app/core.clj": dedent(
                """\
                (ns app.core
                  (:require [app.util]))

                (defn -main [& args]
                  (app.util/greet))
                """
            ),
            "src/app/util.cljc": dedent(
                """\
                (ns app.util)

                (defn greet []
                  (println "Hello"))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    # Extract and examine JAR contents
    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        entries = jar.namelist()

        # Should have both .clj and .cljc files
        assert 'app/core.clj' in entries, \
            f"Expected app/core.clj in JAR, found: {entries}"
        assert 'app/util.cljc' in entries, \
            f"Expected app/util.cljc in JAR, found: {entries}"


def test_package_deploy_jar_clojure_main_with_transitive_deps(rule_runner: RuleRunner) -> None:
    """Test that clojure.main mode includes transitive first-party dependencies as source."""
    import io
    import zipfile

    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            # A library namespace
            "src/mylib/BUILD": dedent(
                """\
                clojure_source(
                    name="utils",
                    source="utils.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/mylib/utils.clj": dedent(
                """\
                (ns mylib.utils)

                (defn format-greeting [name]
                  (str "Hello, " name "!"))
                """
            ),
            # The main app that depends on the library
            "src/myapp/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/mylib:utils", "3rdparty/jvm:org.clojure_clojure"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="clojure.main",  # Source-only mode
                    dependencies=[":core"],
                )
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:require [mylib.utils :as utils]))

                (defn -main [& args]
                  (println (utils/format-greeting "World")))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/myapp", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    # Extract and examine JAR contents
    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        entries = jar.namelist()

        # Should have main app source
        assert 'myapp/core.clj' in entries, \
            f"Expected myapp/core.clj in JAR, found: {entries}"

        # Should have transitive library source
        assert 'mylib/utils.clj' in entries, \
            f"Expected transitive mylib/utils.clj in JAR, found: {entries}"

        # Should NOT have first-party compiled classes
        myapp_classes = [e for e in entries if e.startswith('myapp/') and e.endswith('.class')]
        mylib_classes = [e for e in entries if e.startswith('mylib/') and e.endswith('.class')]
        assert not myapp_classes, \
            f"Unexpected myapp classes in source-only JAR: {myapp_classes}"
        assert not mylib_classes, \
            f"Unexpected mylib classes in source-only JAR: {mylib_classes}"


# =============================================================================
# Tests for macro-generated class handling
# =============================================================================
# These tests verify that AOT classes generated by macros in third-party
# namespaces are correctly included in the final JAR. This handles libraries
# like Specter's declarepath which generate classes in the macro's namespace.


def test_aot_class_not_in_jars_is_kept(rule_runner: RuleRunner) -> None:
    """Verify AOT classes not found in any dependency JAR are kept.

    This is the core test for the macro-generated class fix. Some macros
    (like Specter's declarepath) generate classes in the macro's namespace,
    not the caller's namespace. These classes don't exist in the original JAR -
    they're only created during AOT compilation of user code.

    We simulate this by having first-party code that generates classes that
    wouldn't be in any JAR. The existing tests for transitive first-party
    classes actually cover this scenario since first-party classes are
    "not in any JAR" by definition.

    This test specifically validates the logic: if a class is NOT in any
    dependency JAR's index, it should be kept from AOT output.
    """
    import io
    import zipfile

    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            # Library that uses deftype (generates class in namespace subdirectory)
            "src/mylib/BUILD": dedent(
                """\
                clojure_source(
                    name="types",
                    source="types.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/mylib/types.clj": dedent(
                """\
                (ns mylib.types)

                ;; deftype generates mylib/types/CustomHandler.class
                ;; This class is NOT in any JAR - it's only from AOT compilation
                (deftype CustomHandler [callback]
                  clojure.lang.IFn
                  (invoke [this arg]
                    (callback arg)))
                """
            ),
            "src/myapp/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/mylib:types", "3rdparty/jvm:org.clojure_clojure"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="myapp.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:require [mylib.types])
                  (:import [mylib.types CustomHandler])
                  (:gen-class))

                (defn -main [& args]
                  (let [h (CustomHandler. println)]
                    (h "Hello")))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/myapp", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        jar_entries = set(jar.namelist())

    # The deftype-generated class should be in the JAR
    # deftype generates class in subdirectory: mylib/types/CustomHandler.class
    custom_handler_class = 'mylib/types/CustomHandler.class'
    assert custom_handler_class in jar_entries, (
        f"deftype class {custom_handler_class} should be in JAR (not in any dependency JAR). "
        f"Found entries starting with mylib/: {sorted(e for e in jar_entries if e.startswith('mylib/'))}"
    )

    # The namespace init class should also be present
    assert 'mylib/types__init.class' in jar_entries, \
        "Namespace init class should be in JAR"


def test_nested_inner_class_not_in_jars_is_kept(rule_runner: RuleRunner) -> None:
    """Test that classes with nested inner classes (fn, reify) are handled.

    When first-party code uses anonymous functions or reify, Clojure generates
    classes like mylib/utils$fn__123.class. These should be included from AOT
    since they don't exist in any JAR.
    """
    import io
    import zipfile

    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/mylib/BUILD": dedent(
                """\
                clojure_source(
                    name="utils",
                    source="utils.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/mylib/utils.clj": dedent(
                """\
                (ns mylib.utils)

                ;; Multiple anonymous functions will generate nested inner classes
                ;; e.g., mylib/utils$process_items$fn__123.class
                (defn process-items [items]
                  (map (fn [x] (* x 2))
                       (filter (fn [x] (> x 0))
                               items)))

                ;; reify generates inner classes like mylib/utils$comparator$reify__456.class
                (defn comparator []
                  (reify java.util.Comparator
                    (compare [_ a b]
                      (- a b))))
                """
            ),
            "src/myapp/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/mylib:utils", "3rdparty/jvm:org.clojure_clojure"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="myapp.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:require [mylib.utils :as u])
                  (:gen-class))

                (defn -main [& args]
                  (println (u/process-items [1 2 -3 4])))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/myapp", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        jar_entries = set(jar.namelist())

    # Check for inner classes (fn, reify) - they should be present
    mylib_inner_classes = [e for e in jar_entries
                          if e.startswith('mylib/utils$') and e.endswith('.class')]
    assert len(mylib_inner_classes) > 0, (
        "Inner classes (fn, reify) from mylib.utils should be in JAR. "
        f"Found entries starting with mylib/: {sorted(e for e in jar_entries if e.startswith('mylib/'))}"
    )

    # Verify there are multiple inner classes (from the anonymous functions)
    assert len(mylib_inner_classes) >= 2, (
        f"Expected multiple inner classes from anonymous functions, got: {mylib_inner_classes}"
    )


def test_transitive_macro_generated_classes_included(rule_runner: RuleRunner) -> None:
    """Test that macro-generated classes from transitive dependencies are included.

    Scenario:
    - app depends on lib-a
    - lib-a defines deftype/defrecord that generates classes
    - Those classes must be in the final JAR

    This is similar to test_aot_class_not_in_jars_is_kept but specifically
    tests the transitive case through multiple dependency levels.
    """
    import io
    import zipfile

    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            # Deep library with defrecord
            "src/deep_lib/BUILD": dedent(
                """\
                clojure_source(
                    name="records",
                    source="records.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/deep_lib/records.clj": dedent(
                """\
                (ns deep-lib.records)

                ;; defrecord generates deep_lib/records/Event.class
                (defrecord Event [type payload timestamp])

                ;; defprotocol generates deep_lib/records/EventHandler.class
                (defprotocol EventHandler
                  (handle [this event]))
                """
            ),
            # Middle library that uses the deep library
            "src/mid_lib/BUILD": dedent(
                """\
                clojure_source(
                    name="handlers",
                    source="handlers.clj",
                    dependencies=["//src/deep_lib:records", "3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/mid_lib/handlers.clj": dedent(
                """\
                (ns mid-lib.handlers
                  (:require [deep-lib.records :as r])
                  (:import [deep_lib.records Event]))

                (defn create-event [type payload]
                  (Event. type payload (System/currentTimeMillis)))
                """
            ),
            # App that depends on middle library
            "src/myapp/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//src/mid_lib:handlers", "3rdparty/jvm:org.clojure_clojure"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="myapp.core",
                    dependencies=[":core"],
                )
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:require [mid-lib.handlers :as h])
                  (:gen-class))

                (defn -main [& args]
                  (println (h/create-event :startup {:msg "hello"})))
                """
            ),
        }
    )

    target = rule_runner.get_target(Address("src/myapp", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = None
    for file_content in jar_digest_contents:
        if file_content.path == jar_path:
            jar_content = file_content.content
            break

    assert jar_content is not None

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        jar_entries = set(jar.namelist())

    # Verify the defrecord from deep library is included
    assert 'deep_lib/records/Event.class' in jar_entries, (
        "defrecord Event from transitive deep-lib should be in JAR. "
        f"Found entries: {sorted(e for e in jar_entries if e.startswith('deep_lib/'))}"
    )

    # Verify the defprotocol interface is included
    assert 'deep_lib/records/EventHandler.class' in jar_entries, (
        "defprotocol EventHandler from transitive deep-lib should be in JAR"
    )

    # Verify namespace init classes
    assert 'deep_lib/records__init.class' in jar_entries, \
        "deep-lib.records init class should be in JAR"
    assert 'mid_lib/handlers__init.class' in jar_entries, \
        "mid-lib.handlers init class should be in JAR"
