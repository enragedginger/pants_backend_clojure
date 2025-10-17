"""clj-kondo subsystem for linting Clojure code."""

from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform
from pants.option.option_types import ArgsListOption, BoolOption, SkipOption


class CljKondo(ExternalTool):
    """Clojure linter (clj-kondo).

    clj-kondo is a linter for Clojure code that sparks joy. It performs
    static analysis on Clojure, ClojureScript, and EDN to detect potential
    errors without executing code.

    Homepage: https://github.com/clj-kondo/clj-kondo
    """

    options_scope = "clj-kondo"
    name = "clj-kondo"
    help = "Lint Clojure code using clj-kondo."

    default_version = "2024.09.27"
    default_known_versions = [
        "2024.09.27|linux_x86_64|991e48e0efbb9ff6a2c819a9542c26930c045d737bc39ca4fb82d2086b647c11|13586168",
        "2024.09.27|linux_arm64|f1be097fadf6706f3956e82f117aefa13e722617911fe45fb716e940b3262883|13609242",
        "2024.09.27|macos_x86_64|8538b1ebfb06e8460a99fed27fafee4e74a9d7ffb916ba5cc0359b89eef90841|12741239",
        "2024.09.27|macos_arm64|2e581e12a8574aef032653059ff4053279a205047a9372ece83b0aa37be4e851|12949739",
    ]

    skip = SkipOption("lint")

    config_discovery = BoolOption(
        default=True,
        help=(
            "If true, Pants will search for configuration files "
            "(.clj-kondo/config.edn) in the workspace and include them "
            "in the sandbox when running clj-kondo. This allows clj-kondo "
            "to respect project-specific linter configurations."
        ),
    )

    args = ArgsListOption(example="--fail-level warning --parallel")

    def generate_url(self, plat: Platform) -> str:
        """Generate download URL for clj-kondo binary."""
        platform_mapping = {
            "linux_x86_64": "linux-amd64",
            "linux_arm64": "linux-aarch64",
            "macos_x86_64": "macos-amd64",
            "macos_arm64": "macos-aarch64",
        }
        platform_str = platform_mapping.get(plat.value)
        if not platform_str:
            raise ValueError(f"Unsupported platform: {plat.value}")

        version = self.version
        return (
            f"https://github.com/clj-kondo/clj-kondo/releases/download/"
            f"v{version}/clj-kondo-{version}-{platform_str}.zip"
        )

    def generate_exe(self, _plat: Platform) -> str:
        """The executable name after extraction."""
        return "clj-kondo"
