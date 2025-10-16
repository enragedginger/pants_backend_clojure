"""Formatter for Clojure code using cljfmt."""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.fmt import FmtResult, FmtTargetsRequest, Partitions
from pants.core.util_rules.config_files import ConfigFiles, ConfigFilesRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.partitions import PartitionerType
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import concurrently
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import collect_rules, Get, rule
from pants.util.logging import LogLevel
from pants.util.strutil import pluralize

from clojure_backend.subsystems.cljfmt import Cljfmt
from clojure_backend.target_types import CljfmtFieldSet


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
    downloaded_cljfmt = await Get(
        DownloadedExternalTool, ExternalToolRequest, cljfmt.get_request(platform)
    )

    # Find config files if discovery is enabled
    config_files = await Get(
        ConfigFiles,
        ConfigFilesRequest(
            discovery=cljfmt.config_discovery,
            check_existence=[".cljfmt.edn", ".cljfmt.clj", "cljfmt.edn", "cljfmt.clj"],
        ),
    )

    # Merge all input files: source files + cljfmt binary + config files
    input_digest = await Get(
        Digest,
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
        *request.files,
    ]

    # Execute cljfmt
    result = await Get(
        ProcessResult,
        Process(
            argv=argv,
            input_digest=input_digest,
            output_files=request.files,
            description=f"Run cljfmt on {pluralize(len(request.files), 'file')}.",
            level=LogLevel.DEBUG,
        ),
    )

    return await FmtResult.create(request, result)


def rules():
    return [
        *collect_rules(),
        *CljfmtRequest.rules(),
    ]
