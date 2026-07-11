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

    default_version = "0.16.2"
    default_known_versions = [
        # Linux amd64 (static build)
        "0.16.2|linux_x86_64|e653468a7d8e0f23b6344d0c4ea1446eb6072b46ff0f87675ccbe6f43b987cc6|10564293",
        # Linux aarch64
        "0.16.2|linux_arm64|89ee200174443ca1a3a1eb175c29e8b7676e7cbeb10912d013873af810c6868a|11261773",
        # macOS aarch64 (Apple Silicon)
        "0.16.2|macos_arm64|e59d2d0f3f62829b3f54ad302b34d39a363d447c10af9a09e90c4c27abb0d89d|10613688",
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
            raise ValueError(f"Unsupported platform for cljfmt: {plat.value}. Supported platforms: {', '.join(platform_mapping.keys())}")

        version = self.version
        return f"https://github.com/weavejester/cljfmt/releases/download/{version}/cljfmt-{version}-{platform_str}.tar.gz"

    def generate_exe(self, _plat: Platform) -> str:
        """Generate the path to the executable.

        The executable is always named 'cljfmt' after extraction from the tar.gz.
        It is returned with a "./" prefix (the Pants convention for external tools)
        so that argv[0] resolves relative to the sandbox working directory. Pants
        runs the process with PATH="" (see process_execution local runner), so a
        bare name would be subject to platform-dependent PATH resolution and can
        fail to start the binary under local execution on some platforms.

        Args:
            _plat: The platform (unused, executable name is the same for all platforms).

        Returns:
            The sandbox-relative path to the executable.
        """
        return "./cljfmt"
