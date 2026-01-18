"""Formatter for Clojure code using cljfmt."""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.fmt import FmtResult, FmtTargetsRequest, Partitions
from pants.core.util_rules.config_files import ConfigFiles, ConfigFilesRequest, find_config_file
from pants.core.util_rules.external_tool import (
    DownloadedExternalTool,
    ExternalToolRequest,
    download_external_tool,
)
from pants.core.util_rules.partitions import PartitionerType
from pants.engine.fs import Digest, MergeDigests
from pants.engine.intrinsics import execute_process, merge_digests
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import collect_rules, implicitly, rule
from pants.util.logging import LogLevel
from pants.util.strutil import pluralize

from pants_backend_clojure.subsystems.cljfmt import Cljfmt
from pants_backend_clojure.target_types import CljfmtFieldSet


class CljfmtRequest(FmtTargetsRequest):
    field_set_type = CljfmtFieldSet
    tool_subsystem = Cljfmt
    partitioner_type = PartitionerType.DEFAULT_SINGLE_PARTITION


@rule(desc="Format with cljfmt", level=LogLevel.DEBUG)
async def cljfmt_fmt(
    request: CljfmtRequest.Batch,
    cljfmt: Cljfmt,
    platform: Platform,
) -> FmtResult:
    """Format Clojure source files using cljfmt.

    This rule downloads the cljfmt native binary, finds any config files,
    and runs `cljfmt fix` on the source files.
    """
    # Download cljfmt binary
    downloaded_cljfmt = await download_external_tool(cljfmt.get_request(platform))

    # Find config files if discovery is enabled
    config_files = await find_config_file(
        ConfigFilesRequest(
            discovery=cljfmt.config_discovery,
            check_existence=[".cljfmt.edn", ".cljfmt.clj", "cljfmt.edn", "cljfmt.clj"],
        ),
    )

    # Merge all input files: source files + cljfmt binary + config files
    input_digest = await merge_digests(
        MergeDigests(
            [
                request.snapshot.digest,
                downloaded_cljfmt.digest,
                config_files.snapshot.digest,
            ]
        ),
    )

    # Build command line: cljfmt fix [args] [files]
    # The "fix" command modifies files in place
    argv = [
        downloaded_cljfmt.exe,
        "fix",
        *cljfmt.args,
        *request.snapshot.files,
    ]

    # Execute cljfmt
    result = await execute_process(
        Process(
            argv=argv,
            input_digest=input_digest,
            output_files=request.snapshot.files,
            description=f"Run cljfmt on {pluralize(len(request.snapshot.files), 'file')}.",
            level=LogLevel.DEBUG,
        ),
        **implicitly(),
    )

    return await FmtResult.create(request, result)


def rules():
    return [
        *collect_rules(),
        *CljfmtRequest.rules(),
    ]
