from __future__ import annotations

from dataclasses import dataclass

from pants.engine.rules import collect_rules
from pants.engine.target import (
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


@dataclass(frozen=True)
class ClojureFieldSet(FieldSet):
    required_fields = (ClojureSourceField,)

    sources: ClojureSourceField


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
    )
    help = "A single Clojure source file containing application or library code."


class ClojureSourcesGeneratorSourcesField(ClojureGeneratorSourcesField):
    default = ("*.clj", "*.cljc")
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


def rules():
    return [
        *collect_rules(),
    ]
