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

    default_version = "2026.01.19"
    default_known_versions = [
        "2026.01.19|linux_x86_64|4a4e7bde622b0bd8c4fd368d3a8584690faa007f3ce72255ae63d4090f0e7c92|19754577",
        "2026.01.19|linux_arm64|49b96cfe3d27528b50074226d10b3327412345e5b58d838157177bba45243f3a|19676029",
        "2026.01.19|macos_x86_64|9b89f0265ccf8c6cdcb083708a07af5b4a29047ecae4d322646871dccd5640f5|18114413",
        "2026.01.19|macos_arm64|911f3803be075d59172cb92f0d39aea077fc7550dcdc4e8c946d61552fa51a12|18433094",
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
        return f"https://github.com/clj-kondo/clj-kondo/releases/download/v{version}/clj-kondo-{version}-{platform_str}.zip"

    def generate_exe(self, _plat: Platform) -> str:
        """The path to the executable after extraction.

        Returned with a "./" prefix (the Pants convention for external tools) so
        that argv[0] resolves relative to the sandbox working directory. Pants runs
        the process with PATH="" (see process_execution local runner), so a bare
        name would be subject to platform-dependent PATH resolution and can fail to
        start the binary under local execution on some platforms.
        """
        return "./clj-kondo"
