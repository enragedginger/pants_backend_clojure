"""Subsystem for cljfmt, the Clojure code formatter."""

from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform
from pants.option.option_types import ArgsListOption, BoolOption, SkipOption


class Cljfmt(ExternalTool):
    """Clojure code formatter (cljfmt).

    cljfmt is a tool for formatting Clojure code idiomatically. It detects and fixes
    formatting errors in Clojure code based on the Clojure Style Guide.

    See https://github.com/weavejester/cljfmt for more information.
    """

    options_scope = "cljfmt"
    name = "cljfmt"
    help = "Format Clojure code using cljfmt."

    default_version = "0.14.0"
    default_known_versions = [
        # Linux amd64 (static build)
        "0.14.0|linux_x86_64|bc6ba1234417765866b8d3e7a795dfe10535656fdb9cea766cb53641dbac6233|10537219",
        # Linux aarch64
        "0.14.0|linux_arm64|2007c49596ba706dda7a0a472bffbe4b823f4dadd4e6ef6f2d4b24b142e8f89d|11219584",
        # macOS amd64 (Intel)
        "0.14.0|macos_x86_64|aa753197c1b0b4f4a1a7a51f2d4e0cc69c000b850374131d5b0074de7f52f1cc|10386743",
        # macOS aarch64 (Apple Silicon)
        "0.14.0|macos_arm64|bd5160fe4fe0165a6996757acca0eab274b6d51e78e96989914e58a9dc06ef3e|10586633",
    ]

    skip = SkipOption("fmt", "lint")

    config_discovery = BoolOption(
        default=True,
        help=(
            "If true, Pants will search for cljfmt config files (.cljfmt.edn, .cljfmt.clj, "
            "cljfmt.edn, cljfmt.clj) in the project and include them in the formatter sandbox. "
            "\n\n"
            "cljfmt will search for configuration files in the current directory and parent "
            "directories, allowing you to have project-wide or directory-specific formatting rules."
            "\n\n"
            "Note: If using dotfile configs (.cljfmt.edn or .cljfmt.clj), you may need to tell "
            "Pants to include them by adding the appropriate pattern to `[GLOBAL].pants_ignore.add` "
            "in your `pants.toml`, since Pants ignores dotfiles by default."
        ),
    )

    args = ArgsListOption(example="--indents indentation.edn")

    def generate_url(self, plat: Platform) -> str:
        """Generate download URL for the cljfmt native binary.

        Args:
            plat: The platform to download for.

        Returns:
            The download URL for the specified platform.

        Raises:
            ValueError: If the platform is not supported.
        """
        # Map Pants platform names to cljfmt release artifact names
        platform_mapping = {
            "linux_x86_64": "linux-amd64-static",
            "linux_arm64": "linux-aarch64",
            "macos_x86_64": "darwin-amd64",
            "macos_arm64": "darwin-aarch64",
        }

        platform_str = platform_mapping.get(plat.value)
        if not platform_str:
            raise ValueError(
                f"Unsupported platform for cljfmt: {plat.value}. "
                f"Supported platforms: {', '.join(platform_mapping.keys())}"
            )

        version = self.version
        return (
            f"https://github.com/weavejester/cljfmt/releases/download/"
            f"{version}/cljfmt-{version}-{platform_str}.tar.gz"
        )

    def generate_exe(self, _plat: Platform) -> str:
        """Generate the executable name.

        The executable is always named 'cljfmt' after extraction from the tar.gz.

        Args:
            _plat: The platform (unused, executable name is the same for all platforms).

        Returns:
            The executable name.
        """
        return "cljfmt"
