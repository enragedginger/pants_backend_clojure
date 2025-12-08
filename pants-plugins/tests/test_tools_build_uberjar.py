"""Tests for tools.build uberjar creation."""

from __future__ import annotations

import io
import zipfile
from textwrap import dedent

import pytest

from clojure_backend import compile_clj
from clojure_backend.namespace_analysis import rules as namespace_analysis_rules
from clojure_backend.provided_dependencies import rules as provided_dependencies_rules
from clojure_backend.subsystems.tools_build import rules as tools_build_rules
from clojure_backend.target_types import ClojureSourceField, ClojureSourceTarget
from clojure_backend.target_types import rules as target_types_rules
from clojure_backend.tools_build_uberjar import (
    ToolsBuildUberjarRequest,
    ToolsBuildUberjarResult,
    generate_build_script,
    rules as tools_build_uberjar_rules,
)
from pants.build_graph.address import Address
from pants.core.util_rules import config_files, external_tool, source_files, stripped_source_files, system_binaries
from pants.core.util_rules.stripped_source_files import StrippedSourceFiles
from pants.engine.addresses import Addresses
from pants.engine.fs import DigestContents, EMPTY_DIGEST
from pants.engine.rules import QueryRule
from pants.engine.target import Targets
from pants.jvm import classpath, jvm_common, non_jvm_dependencies
from pants.jvm.classpath import Classpath
from pants.jvm.goals import lockfile
from pants.jvm.resolve import coursier_fetch, jvm_tool
from pants.jvm.resolve.coursier_setup import rules as coursier_setup_rules
from pants.jvm.target_types import JvmArtifactTarget
from pants.jvm.util_rules import rules as jdk_util_rules
from pants.testutil.rule_runner import PYTHON_BOOTSTRAP_ENV, RuleRunner

from tests.clojure_test_fixtures import CLOJURE_3RDPARTY_BUILD, CLOJURE_LOCKFILE

_JVM_RESOLVES = {
    "java17": "locks/jvm/java17.lock.jsonc",
}


@pytest.fixture
def rule_runner() -> RuleRunner:
    rule_runner = RuleRunner(
        preserve_tmpdirs=True,
        target_types=[ClojureSourceTarget, JvmArtifactTarget],
        rules=[
            *tools_build_uberjar_rules(),
            *tools_build_rules(),
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
            QueryRule(ToolsBuildUberjarResult, [ToolsBuildUberjarRequest]),
            QueryRule(Classpath, [Addresses]),
            QueryRule(Targets, [Addresses]),
            QueryRule(StrippedSourceFiles, [source_files.SourceFilesRequest]),
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


def test_generate_build_script() -> None:
    """Test that the build script generator produces valid Clojure code."""
    script = generate_build_script(
        main_ns="my.app.core",
        main_class="my.app.core",
        java_cmd="/path/to/java",
        class_dir="classes",
        uber_file="app.jar",
    )

    # Check that key elements are present
    assert "(ns build" in script
    assert "clojure.tools.build.api" in script
    assert 'main-ns \'my.app.core' in script
    assert 'main-class \'my.app.core' in script
    assert 'class-dir "classes"' in script
    assert 'uber-file "app.jar"' in script
    assert 'java-cmd "/path/to/java"' in script
    assert "(b/compile-clj" in script
    assert ":java-cmd java-cmd" in script
    assert "(b/uber" in script


def test_generate_build_script_custom_values() -> None:
    """Test build script with custom values."""
    script = generate_build_script(
        main_ns="com.example.server",
        main_class="com.example.CustomMain",
        java_cmd="/custom/java",
        class_dir="target/classes",
        uber_file="server.jar",
    )

    assert 'main-ns \'com.example.server' in script
    assert 'main-class \'com.example.CustomMain' in script
    assert 'class-dir "target/classes"' in script
    assert 'uber-file "server.jar"' in script
    assert 'java-cmd "/custom/java"' in script


def test_build_simple_uberjar(rule_runner: RuleRunner) -> None:
    """Test building a simple uberjar with tools.build."""
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

    # Get the target and its classpath
    target_addresses = Addresses([Address("src/hello", target_name="core")])
    targets = rule_runner.request(Targets, [target_addresses])

    # Get stripped source files
    source_fields = [tgt[ClojureSourceField] for tgt in targets if tgt.has_field(ClojureSourceField)]
    stripped_sources = rule_runner.request(
        StrippedSourceFiles,
        [source_files.SourceFilesRequest(source_fields)],
    )

    # Get classpath
    classpath_result = rule_runner.request(Classpath, [target_addresses])

    # Build the uberjar (no provided deps in this test)
    request = ToolsBuildUberjarRequest(
        main_namespace="hello.core",
        main_class="hello.core",
        compile_classpath=classpath_result,
        runtime_classpath=classpath_result,
        source_digest=stripped_sources.snapshot.digest,
        provided_source_digest=EMPTY_DIGEST,
        provided_namespaces=(),
        provided_jar_prefixes=(),
    )

    result = rule_runner.request(ToolsBuildUberjarResult, [request])

    # Verify the result
    assert result.jar_path == "app.jar"
    assert result.digest is not None

    # Check JAR contents
    contents = rule_runner.request(DigestContents, [result.digest])
    assert len(contents) == 1
    jar_content = contents[0]
    assert jar_content.path == "app.jar"

    # Open the JAR and verify contents
    jar_bytes = io.BytesIO(jar_content.content)
    with zipfile.ZipFile(jar_bytes, "r") as jar:
        jar_entries = jar.namelist()

        # Check for expected entries
        assert "META-INF/MANIFEST.MF" in jar_entries

        # Check that main class is included
        assert "hello/core.class" in jar_entries or "hello/core__init.class" in jar_entries

        # Check that Clojure is included
        clojure_classes = [e for e in jar_entries if e.startswith("clojure/")]
        assert len(clojure_classes) > 0, "Expected Clojure classes in uberjar"

        # Check manifest
        manifest = jar.read("META-INF/MANIFEST.MF").decode("utf-8")
        assert "Main-Class:" in manifest
        assert "hello.core" in manifest


def test_build_uberjar_with_transitive_deps(rule_runner: RuleRunner) -> None:
    """Test building an uberjar with transitive dependencies."""
    setup_rule_runner(rule_runner)
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/utils/BUILD": dedent(
                """\
                clojure_source(
                    name="helper",
                    source="helper.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/utils/helper.clj": dedent(
                """\
                (ns utils.helper)

                (defn greet [name]
                  (str "Hello, " name "!"))
                """
            ),
            "src/app/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=[
                        "3rdparty/jvm:org.clojure_clojure",
                        "src/utils:helper",
                    ],
                )
                """
            ),
            "src/app/core.clj": dedent(
                """\
                (ns app.core
                  (:require [utils.helper :as h])
                  (:gen-class))

                (defn -main
                  [& args]
                  (println (h/greet "World")))
                """
            ),
        }
    )

    # Get all targets
    all_addresses = Addresses([
        Address("src/app", target_name="core"),
        Address("src/utils", target_name="helper"),
    ])
    targets = rule_runner.request(Targets, [all_addresses])

    # Get stripped source files
    source_fields = [tgt[ClojureSourceField] for tgt in targets if tgt.has_field(ClojureSourceField)]
    stripped_sources = rule_runner.request(
        StrippedSourceFiles,
        [source_files.SourceFilesRequest(source_fields)],
    )

    # Get classpath
    classpath_result = rule_runner.request(Classpath, [all_addresses])

    # Build the uberjar
    request = ToolsBuildUberjarRequest(
        main_namespace="app.core",
        main_class="app.core",
        compile_classpath=classpath_result,
        runtime_classpath=classpath_result,
        source_digest=stripped_sources.snapshot.digest,
        provided_source_digest=EMPTY_DIGEST,
        provided_namespaces=(),
        provided_jar_prefixes=(),
    )

    result = rule_runner.request(ToolsBuildUberjarResult, [request])

    # Verify the result
    assert result.jar_path == "app.jar"

    # Check JAR contents
    contents = rule_runner.request(DigestContents, [result.digest])
    jar_bytes = io.BytesIO(contents[0].content)
    with zipfile.ZipFile(jar_bytes, "r") as jar:
        jar_entries = jar.namelist()

        # Both namespaces should be compiled
        assert any("app/core" in e for e in jar_entries), "Expected app.core classes"
        assert any("utils/helper" in e for e in jar_entries), "Expected utils.helper classes"


def test_build_uberjar_with_hyphenated_namespace(rule_runner: RuleRunner) -> None:
    """Test building an uberjar with hyphenated namespace."""
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
                """
            ),
            "src/my_app/core.clj": dedent(
                """\
                (ns my-app.core
                  (:gen-class))

                (defn -main
                  [& args]
                  (println "Hello from hyphenated namespace!"))
                """
            ),
        }
    )

    target_addresses = Addresses([Address("src/my_app", target_name="core")])
    targets = rule_runner.request(Targets, [target_addresses])

    source_fields = [tgt[ClojureSourceField] for tgt in targets if tgt.has_field(ClojureSourceField)]
    stripped_sources = rule_runner.request(
        StrippedSourceFiles,
        [source_files.SourceFilesRequest(source_fields)],
    )

    classpath_result = rule_runner.request(Classpath, [target_addresses])

    request = ToolsBuildUberjarRequest(
        main_namespace="my-app.core",
        main_class="my_app.core",  # Munged: hyphens -> underscores
        compile_classpath=classpath_result,
        runtime_classpath=classpath_result,
        source_digest=stripped_sources.snapshot.digest,
        provided_source_digest=EMPTY_DIGEST,
        provided_namespaces=(),
        provided_jar_prefixes=(),
    )

    result = rule_runner.request(ToolsBuildUberjarResult, [request])

    # Check JAR contents
    contents = rule_runner.request(DigestContents, [result.digest])
    jar_bytes = io.BytesIO(contents[0].content)
    with zipfile.ZipFile(jar_bytes, "r") as jar:
        jar_entries = jar.namelist()

        # Clojure converts hyphens to underscores
        assert any("my_app/core" in e for e in jar_entries), (
            f"Expected my_app/core classes (hyphen -> underscore), got: {jar_entries}"
        )
