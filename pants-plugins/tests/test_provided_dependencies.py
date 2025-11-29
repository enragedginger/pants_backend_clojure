"""Tests for provided dependency resolution."""

from __future__ import annotations

from textwrap import dedent

import pytest

from clojure_backend.provided_dependencies import (
    ProvidedDependencies,
    ResolveProvidedDependenciesRequest,
    get_maven_transitive_coordinates,
    resolve_provided_dependencies,
)
from clojure_backend.provided_dependencies import rules as provided_dependencies_rules
from clojure_backend.target_types import (
    ClojureProvidedDependenciesField,
    ClojureDeployJarTarget,
    ClojureSourceTarget,
)
from clojure_backend.target_types import rules as target_types_rules
from pants.build_graph.address import Address
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
            *provided_dependencies_rules(),
            *target_types_rules(),
            *classpath.rules(),
            *jvm_common.rules(),
            *coursier_fetch.rules(),
            *jvm_tool.rules(),
            QueryRule(ProvidedDependencies, [ResolveProvidedDependenciesRequest]),
        ],
    )
    rule_runner.set_options(
        [
            "--jvm-resolves={'java17': 'locks/jvm/java17.lock.jsonc'}",
            "--jvm-default-resolve=java17",
        ]
    )
    return rule_runner


def test_empty_provided_dependencies(rule_runner: RuleRunner) -> None:
    """Test that empty provided field returns empty set."""
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
    field = target[ClojureProvidedDependenciesField]

    result = rule_runner.request(
        ProvidedDependencies,
        [ResolveProvidedDependenciesRequest(field, "java17")]
    )

    assert len(result.addresses) == 0
    assert len(result.coordinates) == 0


def test_single_provided_dependency_no_transitives(rule_runner: RuleRunner) -> None:
    """Test provided dependency with no transitive dependencies."""
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
                    provided=[":api"],
                )
                """
            ),
            "src/lib/api.clj": "(ns lib.api)",
            "src/lib/core.clj": "(ns lib.core (:require [lib.api]) (:gen-class))\n\n(defn -main [& args])",
        }
    )

    target = rule_runner.get_target(Address("src/lib", target_name="app"))
    field = target[ClojureProvidedDependenciesField]

    result = rule_runner.request(
        ProvidedDependencies,
        [ResolveProvidedDependenciesRequest(field, "java17")]
    )

    # Should include just the api target
    assert len(result.addresses) == 1
    assert Address("src/lib", target_name="api") in result.addresses
    # First-party targets don't have coordinates
    assert len(result.coordinates) == 0


def test_provided_dependency_with_transitives(rule_runner: RuleRunner) -> None:
    """Test provided dependency with transitive dependencies."""
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
                    provided=["//src/api:interface"],
                )
                """
            ),
            "src/app/core.clj": "(ns app.core (:require [api.interface]) (:gen-class))\n\n(defn -main [& args])",
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field = target[ClojureProvidedDependenciesField]

    result = rule_runner.request(
        ProvidedDependencies,
        [ResolveProvidedDependenciesRequest(field, "java17")]
    )

    # Should include both api:interface and its transitive dependency base:util
    assert len(result.addresses) == 2
    assert Address("src/api", target_name="interface") in result.addresses
    assert Address("src/base", target_name="util") in result.addresses


def test_multiple_provided_dependencies(rule_runner: RuleRunner) -> None:
    """Test multiple provided dependencies."""
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
                    provided=["//src/api1:lib", "//src/api2:lib"],
                )
                """
            ),
            "src/app/core.clj": "(ns app.core (:require [api1.lib] [api2.lib]) (:gen-class))\n\n(defn -main [& args])",
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field = target[ClojureProvidedDependenciesField]

    result = rule_runner.request(
        ProvidedDependencies,
        [ResolveProvidedDependenciesRequest(field, "java17")]
    )

    # Should include both api libraries
    assert len(result.addresses) == 2
    assert Address("src/api1", target_name="lib") in result.addresses
    assert Address("src/api2", target_name="lib") in result.addresses


def test_provided_dependency_with_shared_transitive(rule_runner: RuleRunner) -> None:
    """Test provided dependencies that share a common transitive dependency."""
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
                    provided=["//src/api1:lib", "//src/api2:lib"],
                )
                """
            ),
            "src/app/core.clj": "(ns app.core (:require [api1.lib] [api2.lib] [common.util]) (:gen-class))\n\n(defn -main [& args])",
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field = target[ClojureProvidedDependenciesField]

    result = rule_runner.request(
        ProvidedDependencies,
        [ResolveProvidedDependenciesRequest(field, "java17")]
    )

    # Should include api1, api2, and their shared common.util dependency
    assert len(result.addresses) == 3
    assert Address("src/api1", target_name="lib") in result.addresses
    assert Address("src/api2", target_name="lib") in result.addresses
    assert Address("src/common", target_name="util") in result.addresses


def test_deep_transitive_chain(rule_runner: RuleRunner) -> None:
    """Test provided dependency with deep transitive chain."""
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
                    provided=["//src/c:lib"],
                )
                """
            ),
            "src/app/core.clj": "(ns app.core (:require [c.lib]) (:gen-class))\n\n(defn -main [& args])",
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field = target[ClojureProvidedDependenciesField]

    result = rule_runner.request(
        ProvidedDependencies,
        [ResolveProvidedDependenciesRequest(field, "java17")]
    )

    # Should include the entire transitive chain: c -> b -> a
    assert len(result.addresses) == 3
    assert Address("src/c", target_name="lib") in result.addresses
    assert Address("src/b", target_name="lib") in result.addresses
    assert Address("src/a", target_name="lib") in result.addresses


def test_provided_dependencies_field_not_set(rule_runner: RuleRunner) -> None:
    """Test that targets without provided field return empty set."""
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
    field = target.get(ClojureProvidedDependenciesField)

    result = rule_runner.request(
        ProvidedDependencies,
        [ResolveProvidedDependenciesRequest(field, "java17")]
    )

    assert len(result.addresses) == 0
    assert len(result.coordinates) == 0


# Minimal lockfile with servlet-api for testing jvm_artifact provided
LOCKFILE_WITH_SERVLET_API = """\
# --- BEGIN PANTS LOCKFILE METADATA: DO NOT EDIT OR REMOVE ---
# {
#   "version": 1,
#   "generated_with_requirements": [
#     "javax.servlet:javax.servlet-api:4.0.1,url=not_provided,jar=not_provided"
#   ]
# }
# --- END PANTS LOCKFILE METADATA ---

[[entries]]
directDependencies = []
dependencies = []
file_name = "javax.servlet_javax.servlet-api_4.0.1.jar"

[entries.coord]
group = "javax.servlet"
artifact = "javax.servlet-api"
version = "4.0.1"
packaging = "jar"
[entries.file_digest]
fingerprint = "b35f4dc26610e3a2eb55619c42f40a68d56554895e66d5d1c1f53f3a2e2b9a43"
serialized_bytes_length = 1234
"""


def test_jvm_artifact_provided_dependency(rule_runner: RuleRunner) -> None:
    """Test that jvm_artifact targets are resolved with Maven coordinates."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_SERVLET_API,
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="servlet-api",
                    group="javax.servlet",
                    artifact="javax.servlet-api",
                    version="4.0.1",
                )
                """
            ),
            "src/hello/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//3rdparty/jvm:servlet-api"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="hello.core",
                    dependencies=[":core", "//3rdparty/jvm:servlet-api"],
                    provided=["//3rdparty/jvm:servlet-api"],
                )
                """
            ),
            "src/hello/core.clj": "(ns hello.core (:gen-class))\n\n(defn -main [& args] (println \"Hello\"))",
        }
    )

    target = rule_runner.get_target(Address("src/hello", target_name="app"))
    field = target[ClojureProvidedDependenciesField]

    result = rule_runner.request(
        ProvidedDependencies,
        [ResolveProvidedDependenciesRequest(field, "java17")]
    )

    # Should include the jvm_artifact address
    assert len(result.addresses) == 1
    assert Address("3rdparty/jvm", target_name="servlet-api") in result.addresses

    # Should include the Maven coordinates for JAR filtering
    assert len(result.coordinates) == 1
    assert ("javax.servlet", "javax.servlet-api") in result.coordinates


# Lockfile with Clojure that has transitive dependencies (spec.alpha, core.specs.alpha)
LOCKFILE_WITH_CLOJURE_TRANSITIVES = """\
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
directDependencies = []
dependencies = []

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
directDependencies = []
dependencies = []

[entries.coord]
group = "org.clojure"
artifact = "spec.alpha"
version = "0.3.218"
packaging = "jar"
[entries.file_digest]
fingerprint = "67ec898eb55c66a957a55279dd85d1376bb994bd87668b2b0de1eb3b97e8aae0"
serialized_bytes_length = 635617
"""


def test_maven_transitive_simple(rule_runner: RuleRunner) -> None:
    """Test that Maven transitive dependencies are included in coordinates."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_CLOJURE_TRANSITIVES,
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="1.11.0",
                )
                """
            ),
            "src/hello/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//3rdparty/jvm:clojure"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="hello.core",
                    dependencies=[":core", "//3rdparty/jvm:clojure"],
                    provided=["//3rdparty/jvm:clojure"],
                )
                """
            ),
            "src/hello/core.clj": "(ns hello.core (:gen-class))\n\n(defn -main [& args] (println \"Hello\"))",
        }
    )

    target = rule_runner.get_target(Address("src/hello", target_name="app"))
    field = target[ClojureProvidedDependenciesField]

    result = rule_runner.request(
        ProvidedDependencies,
        [ResolveProvidedDependenciesRequest(field, "java17")]
    )

    # Should include the jvm_artifact address
    assert len(result.addresses) == 1
    assert Address("3rdparty/jvm", target_name="clojure") in result.addresses

    # Should include clojure AND its transitive dependencies
    assert ("org.clojure", "clojure") in result.coordinates
    assert ("org.clojure", "spec.alpha") in result.coordinates
    assert ("org.clojure", "core.specs.alpha") in result.coordinates
    assert len(result.coordinates) == 3


# Lockfile with diamond dependency: guava -> jsr305, guava -> failureaccess
LOCKFILE_WITH_DIAMOND = """\
# --- BEGIN PANTS LOCKFILE METADATA: DO NOT EDIT OR REMOVE ---
# {
#   "version": 1,
#   "generated_with_requirements": [
#     "com.google.guava:guava:31.1-jre,url=not_provided,jar=not_provided",
#     "com.google.j2objc:j2objc-annotations:1.3,url=not_provided,jar=not_provided"
#   ]
# }
# --- END PANTS LOCKFILE METADATA ---

[[entries]]
file_name = "com.google.guava_guava_31.1-jre.jar"
[[entries.directDependencies]]
group = "com.google.code.findbugs"
artifact = "jsr305"
version = "3.0.2"
packaging = "jar"

[[entries.directDependencies]]
group = "com.google.guava"
artifact = "failureaccess"
version = "1.0.1"
packaging = "jar"

[[entries.dependencies]]
group = "com.google.code.findbugs"
artifact = "jsr305"
version = "3.0.2"
packaging = "jar"

[[entries.dependencies]]
group = "com.google.guava"
artifact = "failureaccess"
version = "1.0.1"
packaging = "jar"


[entries.coord]
group = "com.google.guava"
artifact = "guava"
version = "31.1-jre"
packaging = "jar"
[entries.file_digest]
fingerprint = "d5be94d65e87bd219fb3193ad1517baa55a3b88fc91d21cf735826ab5af087b9"
serialized_bytes_length = 2836000

[[entries]]
file_name = "com.google.code.findbugs_jsr305_3.0.2.jar"
directDependencies = []
dependencies = []

[entries.coord]
group = "com.google.code.findbugs"
artifact = "jsr305"
version = "3.0.2"
packaging = "jar"
[entries.file_digest]
fingerprint = "766ad2a0783f2687962c8ad74ceecc38a28b9f72a2d085ee438b7813e928d0c7"
serialized_bytes_length = 19936

[[entries]]
file_name = "com.google.guava_failureaccess_1.0.1.jar"
directDependencies = []
dependencies = []

[entries.coord]
group = "com.google.guava"
artifact = "failureaccess"
version = "1.0.1"
packaging = "jar"
[entries.file_digest]
fingerprint = "a171ee4c734dd2da837e4b16be9df4661afab72a41adaf31eb84dfdaf936ca26"
serialized_bytes_length = 4617

[[entries]]
file_name = "com.google.j2objc_j2objc-annotations_1.3.jar"
directDependencies = []
dependencies = []

[entries.coord]
group = "com.google.j2objc"
artifact = "j2objc-annotations"
version = "1.3"
packaging = "jar"
[entries.file_digest]
fingerprint = "21af30c92267bd6122c0e0b4d20cccb6641a37eaf956c6950f98f061d2a61b86"
serialized_bytes_length = 8781
"""


def test_maven_transitive_diamond(rule_runner: RuleRunner) -> None:
    """Test provided dependency with diamond transitive structure (guava -> jsr305, failureaccess)."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_DIAMOND,
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="guava",
                    group="com.google.guava",
                    artifact="guava",
                    version="31.1-jre",
                )

                jvm_artifact(
                    name="j2objc",
                    group="com.google.j2objc",
                    artifact="j2objc-annotations",
                    version="1.3",
                )
                """
            ),
            "src/app/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//3rdparty/jvm:guava", "//3rdparty/jvm:j2objc"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="app.core",
                    dependencies=[":core", "//3rdparty/jvm:guava", "//3rdparty/jvm:j2objc"],
                    provided=["//3rdparty/jvm:guava"],
                )
                """
            ),
            "src/app/core.clj": "(ns app.core (:gen-class))\n\n(defn -main [& args])",
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field = target[ClojureProvidedDependenciesField]

    result = rule_runner.request(
        ProvidedDependencies,
        [ResolveProvidedDependenciesRequest(field, "java17")]
    )

    # Should include guava and its transitives, but NOT j2objc
    assert ("com.google.guava", "guava") in result.coordinates
    assert ("com.google.code.findbugs", "jsr305") in result.coordinates
    assert ("com.google.guava", "failureaccess") in result.coordinates
    # j2objc should NOT be in coordinates since it's not provided
    assert ("com.google.j2objc", "j2objc-annotations") not in result.coordinates
    assert len(result.coordinates) == 3


def test_maven_transitive_with_first_party(rule_runner: RuleRunner) -> None:
    """Test mix of first-party sources and third-party with Maven transitives."""
    rule_runner.write_files(
        {
            "locks/jvm/java17.lock.jsonc": LOCKFILE_WITH_CLOJURE_TRANSITIVES,
            "3rdparty/jvm/BUILD": dedent(
                """\
                jvm_artifact(
                    name="clojure",
                    group="org.clojure",
                    artifact="clojure",
                    version="1.11.0",
                )
                """
            ),
            "src/api/BUILD": dedent(
                """\
                clojure_source(
                    name="interface",
                    source="interface.clj",
                )
                """
            ),
            "src/api/interface.clj": "(ns api.interface)",
            "src/app/BUILD": dedent(
                """\
                clojure_source(
                    name="core",
                    source="core.clj",
                    dependencies=["//3rdparty/jvm:clojure", "//src/api:interface"],
                )

                clojure_deploy_jar(
                    name="app",
                    main="app.core",
                    dependencies=[":core", "//3rdparty/jvm:clojure", "//src/api:interface"],
                    provided=["//3rdparty/jvm:clojure", "//src/api:interface"],
                )
                """
            ),
            "src/app/core.clj": "(ns app.core (:require [api.interface]) (:gen-class))\n\n(defn -main [& args])",
        }
    )

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field = target[ClojureProvidedDependenciesField]

    result = rule_runner.request(
        ProvidedDependencies,
        [ResolveProvidedDependenciesRequest(field, "java17")]
    )

    # Should include both first-party address and jvm_artifact
    assert Address("src/api", target_name="interface") in result.addresses
    assert Address("3rdparty/jvm", target_name="clojure") in result.addresses
    assert len(result.addresses) == 2

    # Should include clojure AND its transitive dependencies
    assert ("org.clojure", "clojure") in result.coordinates
    assert ("org.clojure", "spec.alpha") in result.coordinates
    assert ("org.clojure", "core.specs.alpha") in result.coordinates
    assert len(result.coordinates) == 3


def test_get_maven_transitive_coordinates_unit() -> None:
    """Unit test for get_maven_transitive_coordinates helper function."""
    from pants.jvm.resolve.coursier_fetch import CoursierResolvedLockfile

    # Create a mock lockfile with known dependencies
    lockfile_content = LOCKFILE_WITH_CLOJURE_TRANSITIVES.encode("utf-8")
    lockfile = CoursierResolvedLockfile.from_serialized(lockfile_content)

    # Test with a single coordinate that has transitives
    coordinates = {("org.clojure", "clojure")}
    result = get_maven_transitive_coordinates(lockfile, coordinates)

    assert ("org.clojure", "clojure") in result
    assert ("org.clojure", "spec.alpha") in result
    assert ("org.clojure", "core.specs.alpha") in result
    assert len(result) == 3


def test_get_maven_transitive_coordinates_empty() -> None:
    """Unit test for get_maven_transitive_coordinates with empty input."""
    from pants.jvm.resolve.coursier_fetch import CoursierResolvedLockfile

    lockfile_content = LOCKFILE_WITH_CLOJURE_TRANSITIVES.encode("utf-8")
    lockfile = CoursierResolvedLockfile.from_serialized(lockfile_content)

    # Test with empty coordinates
    coordinates: set[tuple[str, str]] = set()
    result = get_maven_transitive_coordinates(lockfile, coordinates)

    assert len(result) == 0


def test_get_maven_transitive_coordinates_missing_entry() -> None:
    """Unit test for get_maven_transitive_coordinates with missing lockfile entry."""
    from pants.jvm.resolve.coursier_fetch import CoursierResolvedLockfile

    lockfile_content = LOCKFILE_WITH_CLOJURE_TRANSITIVES.encode("utf-8")
    lockfile = CoursierResolvedLockfile.from_serialized(lockfile_content)

    # Test with a coordinate not in the lockfile - should still return the input
    coordinates = {("com.unknown", "missing")}
    result = get_maven_transitive_coordinates(lockfile, coordinates)

    # Should still include the input coordinate even if not found
    assert ("com.unknown", "missing") in result
    assert len(result) == 1
