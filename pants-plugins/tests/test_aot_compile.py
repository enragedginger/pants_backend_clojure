"""Tests for Clojure AOT compilation."""

from __future__ import annotations

from textwrap import dedent

import pytest

from clojure_backend.aot_compile import (
    CompileClojureAOTRequest,
    CompiledClojureClasses,
    aot_compile_clojure,
)
from clojure_backend.aot_compile import rules as aot_compile_rules
from clojure_backend import compile_clj
from clojure_backend.target_types import ClojureSourceTarget
from clojure_backend.target_types import rules as target_types_rules
from pants.build_graph.address import Address
from pants.core.util_rules import source_files, stripped_source_files
from pants.engine.addresses import Addresses
from pants.engine.fs import DigestContents
from pants.engine.rules import QueryRule
from pants.jvm import classpath, jvm_common
from pants.jvm.resolve import coursier_fetch, jvm_tool
from pants.jvm.target_types import JvmArtifactTarget, JvmResolveField
from pants.testutil.rule_runner import RuleRunner


# Lockfile with Clojure for AOT compile tests
# Now that we rely on the user's classpath to provide Clojure (instead of fetching
# it via ToolClasspathRequest), the tests need Clojure in the lockfile.
# Using version 1.11.0 with correct fingerprints.
LOCKFILE_WITH_CLOJURE = """\
# --- BEGIN PANTS LOCKFILE METADATA: DO NOT EDIT OR REMOVE ---
# {
#   "version": 1,
#   "generated_with_requirements": [
#     "org.clojure:clojure:1.11.0,url=not_provided,jar=not_provided"
#   ]
# }
# --- END PANTS LOCKFILE METADATA ---

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


# Common BUILD file for 3rdparty Clojure dependency
CLOJURE_3RDPARTY_BUILD = """\
jvm_artifact(
    name="org.clojure_clojure",
    group="org.clojure",
    artifact="clojure",
    version="1.11.0",
)
"""


@pytest.fixture
def rule_runner() -> RuleRunner:
    rule_runner = RuleRunner(
        target_types=[ClojureSourceTarget, JvmArtifactTarget],
        rules=[
            *aot_compile_rules(),
            *classpath.rules(),
            *compile_clj.rules(),
            *target_types_rules(),
            *jvm_common.rules(),
            *source_files.rules(),
            *stripped_source_files.rules(),
            *coursier_fetch.rules(),
            *jvm_tool.rules(),
            QueryRule(CompiledClojureClasses, [CompileClojureAOTRequest]),
        ],
    )
    rule_runner.set_options(
        [
            "--jvm-resolves={'java17': 'locks/jvm/java17.lock.jsonc'}",
            "--jvm-default-resolve=java17",
        ]
    )
    return rule_runner


def test_aot_compile_simple_namespace(rule_runner: RuleRunner) -> None:
    """Test AOT compiling a simple namespace with gen-class."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_CLOJURE,
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

    target_address = Address("src/hello", target_name="core")
    request = CompileClojureAOTRequest(
        namespaces=("hello.core",),
        source_addresses=Addresses([target_address]),
        jdk=None,
        resolve=None,
    )

    result = rule_runner.request(CompiledClojureClasses, [request])

    # Check that we got a valid digest
    assert result.digest is not None
    assert result.classpath_entry is not None

    # Check that .class files were generated
    contents = rule_runner.request(DigestContents, [result.digest])
    class_files = [fc.path for fc in contents]

    # Should have generated at least:
    # - hello/core.class (main class)
    # - hello/core__init.class (namespace loader)
    # - hello/core$_main.class (the -main function)
    assert any("hello/core.class" in path for path in class_files)
    assert any("hello/core__init.class" in path for path in class_files)
    assert any("hello/core$_main.class" in path for path in class_files)


def test_aot_compile_namespace_with_functions(rule_runner: RuleRunner) -> None:
    """Test AOT compiling a namespace with multiple functions."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_CLOJURE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/math/BUILD": dedent(
                """\
                clojure_source(
                    name="calc",
                    source="calc.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/math/calc.clj": dedent(
                """\
                (ns math.calc
                  (:gen-class))

                (defn add [a b]
                  (+ a b))

                (defn multiply [a b]
                  (* a b))

                (defn -main [& args]
                  (println (add 1 2)))
                """
            ),
        }
    )

    target_address = Address("src/math", target_name="calc")
    request = CompileClojureAOTRequest(
        namespaces=("math.calc",),
        source_addresses=Addresses([target_address]),
        jdk=None,
        resolve=None,
    )

    result = rule_runner.request(CompiledClojureClasses, [request])
    contents = rule_runner.request(DigestContents, [result.digest])
    class_files = [fc.path for fc in contents]

    # Should have classes for the namespace and functions
    assert any("math/calc.class" in path for path in class_files)
    assert any("math/calc$add.class" in path for path in class_files)
    assert any("math/calc$multiply.class" in path for path in class_files)
    assert any("math/calc$_main.class" in path for path in class_files)


def test_aot_compile_multiple_namespaces(rule_runner: RuleRunner) -> None:
    """Test AOT compiling multiple namespaces at once."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_CLOJURE,
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
                """
            ),
            "src/app/core.clj": dedent(
                """\
                (ns app.core
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

    request = CompileClojureAOTRequest(
        namespaces=("app.core", "app.util"),
        source_addresses=Addresses(
            [
                Address("src/app", target_name="core"),
                Address("src/app", target_name="util"),
            ]
        ),
        jdk=None,
        resolve=None,
    )

    result = rule_runner.request(CompiledClojureClasses, [request])
    contents = rule_runner.request(DigestContents, [result.digest])
    class_files = [fc.path for fc in contents]

    # Should have classes for both namespaces
    # Core has gen-class so it gets app/core.class
    assert any("app/core.class" in path for path in class_files)
    # Util doesn't have gen-class, so it only gets __init.class
    assert any("app/util__init.class" in path for path in class_files)


def test_aot_compile_with_dependencies(rule_runner: RuleRunner) -> None:
    """Test AOT compiling a namespace that requires another namespace."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_CLOJURE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/myapp/BUILD": dedent(
                """\
                clojure_source(
                    name="util",
                    source="util.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=[":util", "3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/myapp/util.clj": dedent(
                """\
                (ns myapp.util)

                (defn greet [name]
                  (str "Hello, " name "!"))
                """
            ),
            "src/myapp/core.clj": dedent(
                """\
                (ns myapp.core
                  (:require [myapp.util :as util])
                  (:gen-class))

                (defn -main [& args]
                  (println (util/greet "World")))
                """
            ),
        }
    )

    # Compile the main namespace (which requires util)
    # Clojure should automatically compile dependencies transitively
    request = CompileClojureAOTRequest(
        namespaces=("myapp.core",),
        source_addresses=Addresses(
            [
                Address("src/myapp", target_name="core"),
                Address("src/myapp", target_name="util"),
            ]
        ),
        jdk=None,
        resolve=None,
    )

    result = rule_runner.request(CompiledClojureClasses, [request])
    contents = rule_runner.request(DigestContents, [result.digest])
    class_files = [fc.path for fc in contents]

    # Both namespaces should be compiled (transitive compilation)
    # Core has gen-class so it gets myapp/core.class
    assert any("myapp/core.class" in path for path in class_files)
    # Util doesn't have gen-class, so it only gets __init.class
    assert any("myapp/util__init.class" in path for path in class_files)


def test_aot_compile_syntax_error_fails(rule_runner: RuleRunner) -> None:
    """Test that AOT compilation fails gracefully with syntax errors."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_CLOJURE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/bad/BUILD": dedent(
                """\
                clojure_source(
                    name="syntax_error",
                    source="syntax_error.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/bad/syntax_error.clj": dedent(
                """\
                (ns bad.syntax-error
                  (:gen-class))

                (defn broken-fn []
                  ; Missing closing parenthesis
                  (+ 1 2
                """
            ),
        }
    )

    target_address = Address("src/bad", target_name="syntax_error")
    request = CompileClojureAOTRequest(
        namespaces=("bad.syntax-error",),
        source_addresses=Addresses([target_address]),
        jdk=None,
        resolve=None,
    )

    # Compilation should fail due to syntax error
    with pytest.raises(Exception):
        rule_runner.request(CompiledClojureClasses, [request])


def test_aot_compile_mixed_gen_class_and_regular_namespaces(rule_runner: RuleRunner) -> None:
    """Test AOT compiling with both gen-class and regular namespaces."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_CLOJURE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/mixed/BUILD": dedent(
                """\
                clojure_source(
                    name="util",
                    source="util.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                clojure_source(
                    name="main",
                    source="main.clj",
                    dependencies=[":util", "3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/mixed/util.clj": dedent(
                """\
                (ns mixed.util)
                ; No gen-class - this is a regular namespace

                (defn helper-fn [x]
                  (* x 2))
                """
            ),
            "src/mixed/main.clj": dedent(
                """\
                (ns mixed.main
                  (:gen-class)
                  (:require [mixed.util :as util]))

                (defn -main [& args]
                  (println (util/helper-fn 21)))
                """
            ),
        }
    )

    # Compile both namespaces - only main should have gen-class
    main_address = Address("src/mixed", target_name="main")
    util_address = Address("src/mixed", target_name="util")
    request = CompileClojureAOTRequest(
        namespaces=("mixed.main", "mixed.util"),
        source_addresses=Addresses([main_address, util_address]),
        jdk=None,
        resolve=None,
    )

    result = rule_runner.request(CompiledClojureClasses, [request])
    contents = rule_runner.request(DigestContents, [result.digest])
    class_files = [fc.path for fc in contents]

    # mixed.main should have .class files (has gen-class)
    assert any("mixed/main.class" in path for path in class_files)
    # mixed.util should also be compiled (AOT compiles dependencies)
    # but it won't have a main .class file since no gen-class
    assert any("mixed/util" in path for path in class_files)


def test_aot_compile_namespace_without_gen_class(rule_runner: RuleRunner) -> None:
    """Test that compiling a namespace without gen-class still works."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_CLOJURE,
            "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
            "src/plain/BUILD": dedent(
                """\
                clojure_source(
                    name="lib",
                    source="lib.clj",
                    dependencies=["3rdparty/jvm:org.clojure_clojure"],
                )
                """
            ),
            "src/plain/lib.clj": dedent(
                """\
                (ns plain.lib)
                ; No gen-class - just a regular library

                (defn compute [x y]
                  (+ x y))
                """
            ),
        }
    )

    target_address = Address("src/plain", target_name="lib")
    request = CompileClojureAOTRequest(
        namespaces=("plain.lib",),
        source_addresses=Addresses([target_address]),
        jdk=None,
        resolve=None,
    )

    result = rule_runner.request(CompiledClojureClasses, [request])
    contents = rule_runner.request(DigestContents, [result.digest])
    class_files = [fc.path for fc in contents]

    # Should still generate __init and function classes
    assert any("plain/lib__init.class" in path for path in class_files)
    assert any("plain/lib$compute.class" in path for path in class_files)
    # But no main .class file since no gen-class
    assert not any("plain/lib.class" == path for path in class_files)
