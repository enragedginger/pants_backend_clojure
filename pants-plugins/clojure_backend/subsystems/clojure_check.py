"""Subsystem for the Clojure check goal."""

from __future__ import annotations

from pants.option.option_types import ArgsListOption, BoolOption, SkipOption
from pants.option.subsystem import Subsystem
from pants.util.strutil import softwrap


class ClojureCheckSubsystem(Subsystem):
    options_scope = "clojure-check"
    name = "Clojure check"
    help = softwrap(
        """
        Options for checking Clojure compilation via namespace loading.

        The check goal validates that Clojure source code compiles correctly without
        producing artifacts. By default, it loads all namespaces to verify they compile.
        """
    )

    skip = SkipOption("check")

    use_aot = BoolOption(
        default=False,
        help=softwrap(
            """
            Use AOT compilation instead of namespace loading for checking.

            Namespace loading (default) is faster and sufficient for most cases.
            AOT compilation is more thorough and validates :gen-class and other
            AOT-specific features, but is slower.
            """
        ),
    )

    fail_on_warnings = BoolOption(
        default=False,
        help=softwrap(
            """
            Treat Clojure compilation warnings as errors.

            When enabled, warnings such as reflection warnings will cause the check to fail.
            """
        ),
    )

    args = ArgsListOption(example="-Dclojure.compiler.direct-linking=true")
