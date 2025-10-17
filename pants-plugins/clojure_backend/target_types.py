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


def rules():
    return [
        *collect_rules(),
    ]
