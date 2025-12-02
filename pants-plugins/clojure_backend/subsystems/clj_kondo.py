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

    default_version = "2025.10.23"
    default_known_versions = [
        "2025.10.23|linux_x86_64|7d3e563668ec4e8da164c78ed1a9264b5f442a2933c4934c6d0a06652bbfe494|20530383",
        "2025.10.23|linux_arm64|75c90f734caac87e1cabb163fbe2201a2e985f6be72eb1e0f132a7f774b33fcb|20325538",
        "2025.10.23|macos_x86_64|b6876f9311f2998cce0df226adf4792eacf287cfeba9bd067f44d56650956970|18998940",
        "2025.10.23|macos_arm64|9915429099bdb5d35ce0cc88e0e346d9be78a7fd44d9ea8689b19843927e3a07|19220185",
    ]

    skip = SkipOption("lint")

    config_discovery = BoolOption(
        default=True,
        help=(
            "If true, Pants will search for configuration files "
            "(.clj-kondo/config.edn) in the workspace and include them "
            "in the sandbox when running clj-kondo. This allows clj-kondo "
            "to respect project-specific linter configurations.\n\n"
            "Note: You must tell Pants to include the `.clj-kondo` directory by "
            'adding `"!/.clj-kondo/"` to `[GLOBAL].pants_ignore.add` in your '
            "`pants.toml`, since Pants ignores dotfile directories by default."
        ),
    )

    args = ArgsListOption(example="--fail-level warning --parallel")

    use_cache = BoolOption(
        default=True,
        advanced=True,
        help=(
            "Use clj-kondo's built-in caching to speed up incremental linting. "
            "The cache is stored in Pants' named cache directory and persists "
            "across runs. Recommended for all workflows, especially with "
            "'use_classpath' enabled."
        ),
    )

    use_classpath = BoolOption(
        default=True,
        advanced=True,
        help=(
            "Include the full JVM classpath (transitive dependencies) when linting. "
            "This allows clj-kondo to resolve symbols from dependencies and detect "
            "more issues. Highly recommended for accuracy. May increase first-run "
            "time, but subsequent runs are fast due to caching."
        ),
    )

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
