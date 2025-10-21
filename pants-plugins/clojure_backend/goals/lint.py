"""Linter for Clojure code using clj-kondo."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.config_files import ConfigFiles, ConfigFilesRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.partitions import (
    Partition,
    Partitions,
    PartitionerType,
)
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.addresses import Addresses
from pants.engine.fs import Digest, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import collect_rules, Get, implicitly, rule
from pants.jvm.classpath import classpath as classpath_get
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.target_types import JvmResolveField
from pants.util.logging import LogLevel
from pants.util.strutil import pluralize

from clojure_backend.subsystems.clj_kondo import CljKondo
from clojure_backend.target_types import CljKondoFieldSet


class CljKondoRequest(LintTargetsRequest):
    field_set_type = CljKondoFieldSet
    tool_subsystem = CljKondo
    partitioner_type = PartitionerType.CUSTOM


@dataclass(frozen=True)
class CljKondoPartitionMetadata:
    """Metadata for a clj-kondo partition."""

    resolve: str

    @property
    def description(self) -> str:
        return f"clj-kondo (resolve: {self.resolve})"


@rule
async def partition_clj_kondo_by_resolve(
    request: CljKondoRequest.PartitionRequest,
    jvm: JvmSubsystem,
) -> Partitions:
    """Partition clj-kondo lint targets by JVM resolve.

    Each resolve has its own classpath, so we must lint them separately
    to avoid mixing dependencies from different resolves.
    """
    # Group field sets by resolve
    resolves_to_field_sets: dict[str, list[CljKondoFieldSet]] = defaultdict(list)

    for field_set in request.field_sets:
        # Get the resolve from the field set
        resolve = field_set.resolve.normalized_value(jvm)
        resolves_to_field_sets[resolve].append(field_set)

    # Create one partition per resolve
    partitions = []
    for resolve, field_sets in sorted(resolves_to_field_sets.items()):
        partitions.append(
            Partition(
                tuple(field_sets),
                CljKondoPartitionMetadata(resolve=resolve),
            )
        )

    return Partitions(partitions)


@rule(desc="Lint with clj-kondo", level=LogLevel.DEBUG)
async def clj_kondo_lint(
    request: CljKondoRequest.Batch,
    clj_kondo: CljKondo,
    platform: Platform,
) -> LintResult:
    """Lint Clojure source files using clj-kondo.

    This rule downloads the clj-kondo native binary, finds any config files,
    and runs `clj-kondo --lint` on the source files. If classpath support is
    enabled, it also resolves and includes all transitive dependencies.
    """
    # Step 1: Download clj-kondo binary
    downloaded_clj_kondo = await Get(
        DownloadedExternalTool, ExternalToolRequest, clj_kondo.get_request(platform)
    )

    # Step 2: Find config files if discovery is enabled
    config_files = await Get(
        ConfigFiles,
        ConfigFilesRequest(
            discovery=clj_kondo.config_discovery,
            check_existence=[".clj-kondo/config.edn"],
        ),
    )

    # Step 3: Get source files
    source_files = await Get(
        SourceFiles,
        SourceFilesRequest(element.sources for element in request.elements),
    )

    # Step 4: Get classpath if enabled
    classpath_digests = []

    if clj_kondo.use_classpath and request.elements:
        # Collect all addresses in this batch
        addresses = Addresses(element.address for element in request.elements)

        # Get classpath for these targets
        # Since we've partitioned by resolve, all targets in this batch have the same resolve
        classpath = await classpath_get(**implicitly({addresses: Addresses}))

        # Include classpath JARs in the input digest so clj-kondo can use them for
        # symbol resolution via its cache, but don't pass them to --lint since we
        # only want to lint first-party sources
        classpath_digests = list(classpath.digests())

    # Step 5: Merge all inputs (includes classpath digests)
    input_digest = await Get(
        Digest,
        MergeDigests(
            [
                source_files.snapshot.digest,
                downloaded_clj_kondo.digest,
                config_files.snapshot.digest,
                *classpath_digests,
            ]
        ),
    )

    # Step 6: Build cache arguments
    cache_args = []
    cache_mapping = {}

    if clj_kondo.use_cache:
        cache_args = ["--cache-dir", ".clj-kondo/.cache"]
        cache_mapping = {"clj_kondo_cache": ".clj-kondo/.cache"}

    # Step 7: Build command line
    argv = [
        downloaded_clj_kondo.exe,
        *cache_args,          # --cache-dir .clj-kondo/.cache (if enabled)
        "--lint",             # lint source files only (first-party)
        *clj_kondo.args,
        *source_files.snapshot.files,
    ]

    # Step 8: Execute clj-kondo (with cache mapping)
    result = await Get(
        FallibleProcessResult,
        Process(
            argv=argv,
            input_digest=input_digest,
            append_only_caches=cache_mapping,
            description=f"Run clj-kondo on {pluralize(len(source_files.snapshot.files), 'file')}.",
            level=LogLevel.DEBUG,
        ),
    )

    # Step 9: Return result with partition description
    return LintResult(
        exit_code=result.exit_code,
        stdout=result.stdout.decode(),
        stderr=result.stderr.decode(),
        linter_name="clj-kondo",
        partition_description=request.partition_metadata.description
            if request.partition_metadata else None,
    )


def rules():
    return [
        *collect_rules(),
        *CljKondoRequest.rules(),
    ]
