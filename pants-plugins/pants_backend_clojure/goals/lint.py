"""Linter for Clojure code using clj-kondo."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from pants.base.glob_match_error_behavior import GlobMatchErrorBehavior
from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.config_files import ConfigFilesRequest, find_config_file
from pants.core.util_rules.external_tool import (
    download_external_tool,
)
from pants.core.util_rules.partitions import (
    Partition,
    PartitionerType,
    Partitions,
)
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.fs import EMPTY_DIGEST, Digest, MergeDigests, PathGlobs
from pants.engine.intrinsics import execute_process, merge_digests, path_globs_to_digest
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import collect_rules, implicitly, rule
from pants.jvm.resolve.coursier_fetch import (
    coursier_fetch_lockfile,
    get_coursier_lockfile_for_resolve,
)
from pants.jvm.resolve.key import CoursierResolveKey
from pants.jvm.subsystems import JvmSubsystem
from pants.util.logging import LogLevel
from pants.util.strutil import pluralize

from pants_backend_clojure.subsystems.clj_kondo import CljKondo
from pants_backend_clojure.target_types import CljKondoFieldSet


class CljKondoRequest(LintTargetsRequest):
    field_set_type = CljKondoFieldSet
    tool_subsystem = CljKondo
    partitioner_type = PartitionerType.CUSTOM


@dataclass(frozen=True)
class CljKondoPartitionMetadata:
    """Metadata for a clj-kondo partition."""

    resolve: str
    classpath_digest: Digest

    @property
    def description(self) -> str:
        return f"clj-kondo (resolve: {self.resolve})"


@rule
async def partition_clj_kondo_by_resolve(
    request: CljKondoRequest.PartitionRequest,
    jvm: JvmSubsystem,
    clj_kondo: CljKondo,
) -> Partitions:
    """Partition clj-kondo lint targets by JVM resolve.

    Each resolve has its own set of third-party dependencies, so we must lint
    them separately. The classpath (third-party JARs only) is resolved once per
    partition here, rather than per-batch, to avoid redundant work and enable
    parallelism when --lint-batch-size is lowered.
    """
    # Group field sets by resolve
    resolves_to_field_sets: dict[str, list[CljKondoFieldSet]] = defaultdict(list)

    for field_set in request.field_sets:
        resolve = field_set.resolve.normalized_value(jvm)
        resolves_to_field_sets[resolve].append(field_set)

    # Create one partition per resolve, pre-resolving third-party JARs if needed
    partitions = []
    for resolve, field_sets in sorted(resolves_to_field_sets.items()):
        classpath_digest = EMPTY_DIGEST

        if clj_kondo.use_classpath:
            # Construct CoursierResolveKey directly from the resolve name,
            # avoiding CoarsenedTargets which would trigger first-party compilation.
            # This mirrors select_coursier_resolve_for_targets (coursier_fetch.py:722-731).
            resolve_path = jvm.resolves[resolve]
            lockfile_source = PathGlobs(
                [resolve_path],
                glob_match_error_behavior=GlobMatchErrorBehavior.error,
                description_of_origin=f"The resolve `{resolve}` from `[jvm].resolves`",
            )
            resolve_digest = await path_globs_to_digest(lockfile_source)
            resolve_key = CoursierResolveKey(resolve, resolve_path, resolve_digest)

            # Fetch all third-party JARs from the lockfile (no compilation triggered)
            lockfile = await get_coursier_lockfile_for_resolve(resolve_key)
            resolved_entries = await coursier_fetch_lockfile(lockfile)

            # Merge all JAR digests into a single digest for the partition
            classpath_digest = await merge_digests(MergeDigests(entry.digest for entry in resolved_entries))

        partitions.append(
            Partition(
                tuple(field_sets),
                CljKondoPartitionMetadata(
                    resolve=resolve,
                    classpath_digest=classpath_digest,
                ),
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
    and runs `clj-kondo --lint` on the source files. Third-party dependency JARs
    for symbol resolution are pre-resolved at the partition level.
    """
    # Step 1: Download clj-kondo binary
    downloaded_clj_kondo = await download_external_tool(clj_kondo.get_request(platform))

    # Step 2: Find config files if discovery is enabled
    config_files = await find_config_file(
        ConfigFilesRequest(
            discovery=clj_kondo.config_discovery,
            check_existence=[".clj-kondo/config.edn"],
        ),
    )

    # Step 2b: Capture the rest of the .clj-kondo/ config tree.
    #
    # ConfigFilesRequest (Step 2) only captures the root .clj-kondo/config.edn.
    # clj-kondo also reads, relative to .clj-kondo/:
    #   - custom hook code under hooks/, and
    #   - imported library configs under <group-id>/<artifact-id>/ (the standard
    #     `clj-kondo --copy-configs --dependencies` layout), each of which can
    #     carry its own config.edn plus hook namespaces.
    # ConfigFilesRequest captures individual files, not directories, so capture
    # the whole tree here so imported configs and hooks resolve in the sandbox
    # exactly as they do for an editor or a bare `clj-kondo` invocation. The
    # runtime cache (.clj-kondo/.cache) is excluded: it is large, changes every
    # run (which would bust this action's cache), and is supplied separately as
    # an append-only cache below.
    #
    # NOTE: like Step 2, this only sees anything if the workspace un-ignores the
    # dotdir, e.g. `[GLOBAL].pants_ignore.add = ["!/.clj-kondo/"]`.
    clj_kondo_tree_digest = EMPTY_DIGEST
    if clj_kondo.config_discovery:
        clj_kondo_tree_digest = await path_globs_to_digest(
            PathGlobs(
                [".clj-kondo/**", "!.clj-kondo/.cache/**"],
                glob_match_error_behavior=GlobMatchErrorBehavior.ignore,
            ),
        )

    # Step 3: Get source files
    source_files = await determine_source_files(
        SourceFilesRequest(element.sources for element in request.elements),
    )

    # Step 4: Use pre-resolved classpath from partition metadata
    # Third-party JARs are resolved once per resolve in the partitioner,
    # avoiding redundant work across batches and skipping first-party compilation.
    classpath_digests = []
    if clj_kondo.use_classpath and request.partition_metadata:
        classpath_digests = [request.partition_metadata.classpath_digest]

    # Step 5: Merge all inputs
    input_digest = await merge_digests(
        MergeDigests(
            [
                source_files.snapshot.digest,
                downloaded_clj_kondo.digest,
                config_files.snapshot.digest,
                clj_kondo_tree_digest,
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
        *cache_args,  # --cache-dir .clj-kondo/.cache (if enabled)
        "--lint",  # lint source files only (first-party)
        *clj_kondo.args,
        *source_files.snapshot.files,
    ]

    # Step 8: Execute clj-kondo (with cache mapping)
    result = await execute_process(
        Process(
            argv=argv,
            input_digest=input_digest,
            append_only_caches=cache_mapping,
            description=f"Run clj-kondo on {pluralize(len(source_files.snapshot.files), 'file')}.",
            level=LogLevel.DEBUG,
        ),
        **implicitly(),
    )

    # Step 9: Return result with partition description
    return LintResult(
        exit_code=result.exit_code,
        stdout=result.stdout.decode(),
        stderr=result.stderr.decode(),
        linter_name="clj-kondo",
        partition_description=request.partition_metadata.description if request.partition_metadata else None,
    )


def rules():
    return [
        *collect_rules(),
        *CljKondoRequest.rules(),
    ]
