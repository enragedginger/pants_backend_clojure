from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.test import (
    TestExtraEnvVarsField,
    TestFieldSet,
    TestTimeoutField,
)
from pants.engine.rules import collect_rules
from pants.engine.target import (
    BoolField,
    COMMON_TARGET_FIELDS,
    FieldSet,
    MultipleSourcesField,
    SingleSourceField,
    SpecialCasedDependencies,
    StringField,
    StringSequenceField,
    Target,
    TargetFilesGenerator,
    generate_multiple_sources_field_help_message,
)
from pants.jvm.target_types import (
    JvmDependenciesField,
    JvmJdkField,
    JvmMainClassNameField,
    JvmProvidesTypesField,
    JvmResolveField,
    OutputPathField,
)


class ClojureSourceField(SingleSourceField):
    expected_file_extensions = (".clj", ".cljc")


class ClojureGeneratorSourcesField(MultipleSourcesField):
    expected_file_extensions = (".clj", ".cljc")


class SkipCljfmtField(BoolField):
    alias = "skip_cljfmt"
    default = False
    help = "If true, don't run cljfmt on this target's code."


class SkipCljKondoField(BoolField):
    alias = "skip_clj_kondo"
    default = False
    help = "If true, don't run clj-kondo on this target's code."


@dataclass(frozen=True)
class ClojureFieldSet(FieldSet):
    required_fields = (ClojureSourceField,)

    sources: ClojureSourceField


@dataclass(frozen=True)
class CljfmtFieldSet(FieldSet):
    """Field set for targets that can be formatted with cljfmt."""

    required_fields = (ClojureSourceField,)

    sources: ClojureSourceField
    skip_cljfmt: SkipCljfmtField


@dataclass(frozen=True)
class CljKondoFieldSet(FieldSet):
    """Field set for targets that can be linted with clj-kondo."""

    required_fields = (ClojureSourceField,)

    sources: ClojureSourceField
    skip_clj_kondo: SkipCljKondoField
    resolve: JvmResolveField


@dataclass(frozen=True)
class ClojureGeneratorFieldSet(FieldSet):
    required_fields = (ClojureGeneratorSourcesField,)

    sources: ClojureGeneratorSourcesField


# -----------------------------------------------------------------------------------------------
# `clojure_source` and `clojure_sources` targets
# -----------------------------------------------------------------------------------------------


class ClojureSourceTarget(Target):
    alias = "clojure_source"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        JvmDependenciesField,
        ClojureSourceField,
        JvmResolveField,
        JvmMainClassNameField,
        JvmProvidesTypesField,
        JvmJdkField,
        SkipCljfmtField,
        SkipCljKondoField,
    )
    help = "A single Clojure source file containing application or library code."


class ClojureSourcesGeneratorSourcesField(ClojureGeneratorSourcesField):
    default = (
        "*.clj",
        "*.cljc",
        # Exclude test files by default
        "!*_test.clj",
        "!*_test.cljc",
        "!test_*.clj",
        "!test_*.cljc",
    )
    help = generate_multiple_sources_field_help_message(
        "Example: `sources=['Example.clj', 'New*.clj', '!OldExample.clj']`"
    )


class ClojureSourcesGeneratorTarget(TargetFilesGenerator):
    alias = "clojure_sources"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ClojureSourcesGeneratorSourcesField,
    )
    generated_target_cls = ClojureSourceTarget
    copied_fields = COMMON_TARGET_FIELDS
    moved_fields = (
        JvmDependenciesField,
        JvmResolveField,
        JvmJdkField,
        JvmMainClassNameField,
        JvmProvidesTypesField,
    )
    help = "Generate a `clojure_source` target for each file in the `sources` field."


# -----------------------------------------------------------------------------------------------
# `clojure_test` and `clojure_tests` targets
# -----------------------------------------------------------------------------------------------


class ClojureTestSourceField(ClojureSourceField):
    """A Clojure test file using clojure.test."""


class ClojureTestTimeoutField(TestTimeoutField):
    """Timeout for Clojure tests."""


class ClojureTestExtraEnvVarsField(TestExtraEnvVarsField):
    """Extra environment variables for Clojure tests."""


class ClojureTestTarget(Target):
    alias = "clojure_test"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ClojureTestSourceField,
        ClojureTestTimeoutField,
        ClojureTestExtraEnvVarsField,
        JvmDependenciesField,
        JvmResolveField,
        JvmProvidesTypesField,
        JvmJdkField,
        SkipCljfmtField,
        SkipCljKondoField,
    )
    help = "A single Clojure test file using clojure.test."


class ClojureTestsGeneratorSourcesField(ClojureGeneratorSourcesField):
    default = ("*_test.clj", "*_test.cljc", "test_*.clj", "test_*.cljc")
    help = generate_multiple_sources_field_help_message(
        "Example: `sources=['*_test.clj', '!skip_test.clj']`"
    )


@dataclass(frozen=True)
class ClojureTestFieldSet(TestFieldSet):
    required_fields = (
        ClojureTestSourceField,
        JvmJdkField,
    )

    sources: ClojureTestSourceField
    timeout: ClojureTestTimeoutField
    jdk_version: JvmJdkField
    dependencies: JvmDependenciesField
    extra_env_vars: ClojureTestExtraEnvVarsField


@dataclass(frozen=True)
class ClojureTestGeneratorFieldSet(FieldSet):
    required_fields = (ClojureTestsGeneratorSourcesField,)

    sources: ClojureTestsGeneratorSourcesField


class ClojureTestsGeneratorTarget(TargetFilesGenerator):
    alias = "clojure_tests"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ClojureTestsGeneratorSourcesField,
    )
    generated_target_cls = ClojureTestTarget
    copied_fields = COMMON_TARGET_FIELDS
    moved_fields = (
        ClojureTestTimeoutField,
        ClojureTestExtraEnvVarsField,
        JvmDependenciesField,
        JvmJdkField,
        JvmProvidesTypesField,
        JvmResolveField,
    )
    help = "Generate a `clojure_test` target for each file in the `sources` field."


# -----------------------------------------------------------------------------------------------
# `clojure_deploy_jar` target for creating uberjars
# -----------------------------------------------------------------------------------------------


class ClojureMainNamespaceField(StringField):
    alias = "main"
    required = True
    help = (
        "Main namespace with -main function and (:gen-class) in its ns declaration. "
        "Example: 'my.app.core'. This namespace will be AOT compiled and used as the "
        "entry point for the executable JAR."
    )


class ClojureAOTNamespacesField(StringSequenceField):
    alias = "aot"
    help = (
        "Namespaces to AOT compile. Options:\n"
        "- [':none']: No AOT compilation (source-only JAR, not directly executable)\n"
        "- Empty (default): Compile only main namespace (transitive)\n"
        "- [':all']: Compile all project namespaces from dependencies\n"
        "- ['ns1', 'ns2']: Compile specific namespaces\n\n"
        "Note: AOT compilation is transitive - compiling a namespace will "
        "automatically compile all namespaces it requires.\n\n"
        "The ':none' mode creates a source-only JAR that defers all compilation "
        "to runtime. This avoids AOT-related issues but:\n"
        "- Results in slower startup (compilation happens at runtime)\n"
        "- JAR is not directly executable with 'java -jar'\n"
        "- Run with: java -cp app.jar clojure.main -m my.app.core"
    )
    default = ()  # Empty = main namespace only (transitive)


class ClojureProvidedDependenciesField(SpecialCasedDependencies):
    alias = "provided"
    help = (
        "Dependencies that are 'provided' at runtime and should be excluded from the final JAR.\n\n"
        "Similar to Maven's 'provided' scope - dependencies in this field will be available "
        "during AOT compilation, but they (and all their transitive dependencies) will be excluded "
        "from the packaged JAR.\n\n"
        "For jvm_artifact targets, this includes Maven transitive dependencies that are resolved "
        "by Coursier and stored in the lockfile. These transitive dependencies are automatically "
        "looked up and excluded from the JAR, even if they don't have explicit Pants targets.\n\n"
        "Matching is based on Maven groupId:artifactId coordinates (version is ignored). "
        "This means if you mark `org.example:lib:1.0` as provided, any version of "
        "`org.example:lib` will be excluded, along with all its Maven transitive dependencies.\n\n"
        "Important: Dependencies listed here MUST also appear in the regular 'dependencies' field. "
        "The 'provided' field marks which dependencies should be excluded from the JAR, "
        "while the 'dependencies' field makes them available for compilation and dependency resolution.\n\n"
        "Common use cases:\n"
        "- Servlet APIs or application server libraries that will be provided at runtime\n"
        "- Platform-specific dependencies that are available in the deployment environment\n"
        "- Large runtime libraries (like Apache Spark) where all transitives should be excluded\n\n"
        "Example:\n"
        "  clojure_deploy_jar(\n"
        "      name='webapp',\n"
        "      main='my.web.handler',\n"
        "      dependencies=[':servlet-api', ':my-lib'],\n"
        "      provided=[':servlet-api'],  # Container provides this at runtime\n"
        "  )"
    )


class ClojureDeployJarTarget(Target):
    alias = "clojure_deploy_jar"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        JvmDependenciesField,
        ClojureMainNamespaceField,
        ClojureAOTNamespacesField,
        ClojureProvidedDependenciesField,
        JvmResolveField,
        JvmJdkField,
        OutputPathField,
    )
    help = (
        "A Clojure application packaged as an executable JAR (uberjar).\n\n"
        "The main namespace will be AOT compiled along with its dependencies, "
        "and packaged with all transitive dependencies into a single JAR file "
        "that can be executed with `java -jar`.\n\n"
        "The main namespace must include (:gen-class) in its ns declaration and "
        "define a -main function."
    )


def rules():
    return [
        *collect_rules(),
    ]
