"""Subsystem for Clojure dependency inference options."""

from __future__ import annotations

from pants.engine.rules import collect_rules
from pants.option.option_types import BoolOption, DictOption
from pants.option.subsystem import Subsystem
from pants.util.strutil import softwrap


class ClojureInferSubsystem(Subsystem):
    options_scope = "clojure-infer"
    name = "Clojure dependency inference"
    help = softwrap(
        """
        Options controlling Clojure dependency inference.

        Clojure dependency inference automatically discovers dependencies by parsing
        :require and :import forms in Clojure source files, then matching them to
        first-party sources and third-party JVM artifacts.
        """
    )

    namespaces = BoolOption(
        default=True,
        help=softwrap(
            """
            Infer dependencies from :require forms in Clojure source files.

            When enabled, the dependency inference system will parse Clojure source
            files to extract required namespaces and match them to targets that provide
            those namespaces.
            """
        ),
    )

    java_imports = BoolOption(
        default=True,
        help=softwrap(
            """
            Infer dependencies from :import forms in Clojure source files.

            When enabled, the dependency inference system will parse Clojure source
            files to extract Java imports and match them to targets that provide those
            classes.
            """
        ),
    )

    third_party_namespace_mapping = DictOption[str](
        default={},
        help=softwrap(
            """
            A mapping of Clojure namespace patterns to JVM artifact coordinates.

            For example: {"my.custom.lib.**": "com.example:my-lib"}

            The namespace pattern may be made recursive by adding `.**` to the end,
            which will match the specified namespace and all sub-namespaces.

            This is useful when JAR analysis cannot detect namespaces (e.g., AOT-only
            JARs with non-standard class naming) or when you want to override the
            automatic namespace discovery.

            Mappings specified here take lower precedence than:
            1. Manual `packages` field on jvm_artifact targets
            2. Automatic JAR analysis from lockfiles

            Example configuration in pants.toml:

                [clojure-infer]
                third_party_namespace_mapping = {
                    "my.custom.ns.**": "com.example:my-lib",
                    "legacy.code": "org.legacy:old-lib",
                }
            """
        ),
    )


def rules():
    return collect_rules()
