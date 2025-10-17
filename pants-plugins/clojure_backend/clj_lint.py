"""Linter for Clojure code using clj-kondo."""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.config_files import ConfigFiles, ConfigFilesRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.partitions import PartitionerType
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import Digest, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import collect_rules, Get, rule
from pants.util.logging import LogLevel
from pants.util.strutil import pluralize

from clojure_backend.subsystems.clj_kondo import CljKondo
from clojure_backend.target_types import CljKondoFieldSet


class CljKondoRequest(LintTargetsRequest):
    field_set_type = CljKondoFieldSet
    tool_subsystem = CljKondo
    partitioner_type = PartitionerType.DEFAULT_SINGLE_PARTITION


@rule(desc="Lint with clj-kondo", level=LogLevel.DEBUG)
async def clj_kondo_lint(
    request: CljKondoRequest.Batch,
    clj_kondo: CljKondo,
    platform: Platform,
) -> LintResult:
    """Lint Clojure source files using clj-kondo.

    This rule downloads the clj-kondo native binary, finds any config files,
    and runs `clj-kondo --lint` on the source files.
    """
    # Download clj-kondo binary
    downloaded_clj_kondo = await Get(
        DownloadedExternalTool, ExternalToolRequest, clj_kondo.get_request(platform)
    )

    # Find config files if discovery is enabled
    config_files = await Get(
        ConfigFiles,
        ConfigFilesRequest(
            discovery=clj_kondo.config_discovery,
            check_existence=[".clj-kondo/config.edn"],
        ),
    )

    # Get snapshot from elements
    # For lint batches, we need to derive the snapshot from elements
    source_files = await Get(
        SourceFiles,
        SourceFilesRequest(element.sources for element in request.elements),
    )

    # Merge all input files: source files + clj-kondo binary + config files
    input_digest = await Get(
        Digest,
        MergeDigests(
            [
                source_files.snapshot.digest,
                downloaded_clj_kondo.digest,
                config_files.snapshot.digest,
            ]
        ),
    )

    # Build command line: clj-kondo --lint [args] [files]
    # The "--lint" command performs read-only analysis
    argv = [
        downloaded_clj_kondo.exe,
        "--lint",
        *clj_kondo.args,
        *source_files.snapshot.files,
    ]

    # Execute clj-kondo
    # Use FallibleProcessResult because non-zero exit codes are expected when issues are found
    result = await Get(
        FallibleProcessResult,
        Process(
            argv=argv,
            input_digest=input_digest,
            description=f"Run clj-kondo on {pluralize(len(source_files.snapshot.files), 'file')}.",
            level=LogLevel.DEBUG,
        ),
    )

    return LintResult(
        exit_code=result.exit_code,
        stdout=result.stdout.decode(),
        stderr=result.stderr.decode(),
        linter_name="clj-kondo",
    )


def rules():
    return [
        *collect_rules(),
        *CljKondoRequest.rules(),
    ]
