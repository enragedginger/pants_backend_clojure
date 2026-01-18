"""Subsystem for Clojure tools.build integration."""

from __future__ import annotations

from dataclasses import dataclass

from pants.engine.rules import collect_rules, rule
from pants.jvm.resolve.common import ArtifactRequirement, ArtifactRequirements, Coordinate
from pants.jvm.resolve.coursier_fetch import (
    ToolClasspath,
    ToolClasspathRequest,
    materialize_classpath_for_tool,
)
from pants.option.option_types import StrOption
from pants.option.subsystem import Subsystem
from pants.util.strutil import softwrap


class ToolsBuildSubsystem(Subsystem):
    """Configuration for Clojure tools.build."""

    options_scope = "clojure-tools-build"
    name = "tools.build"
    help = softwrap(
        """
        Configuration for Clojure tools.build, which is used for AOT compilation
        and uberjar creation.

        tools.build is the official Clojure build library that handles AOT compilation
        and uberjar packaging. This backend delegates to tools.build for these tasks,
        ensuring correct handling of complex AOT scenarios like protocol classes,
        macro-generated classes, and transitive compilation.
        """
    )

    version = StrOption(
        default="0.10.11",
        help="Version of tools.build to use for AOT compilation and uberjar creation.",
    )


@dataclass(frozen=True)
class ToolsBuildClasspathRequest:
    """Request to get the tools.build classpath."""

    pass


@rule(desc="Fetch tools.build classpath")
async def get_tools_build_classpath(
    request: ToolsBuildClasspathRequest,
    tools_build: ToolsBuildSubsystem,
) -> ToolClasspath:
    """Fetch the tools.build classpath using Coursier."""
    return await materialize_classpath_for_tool(
        ToolClasspathRequest(
            artifact_requirements=ArtifactRequirements([
                ArtifactRequirement(
                    coordinate=Coordinate(
                        group="io.github.clojure",
                        artifact="tools.build",
                        version=tools_build.version,
                    )
                ),
            ]),
        ),
    )


def rules():
    return collect_rules()
