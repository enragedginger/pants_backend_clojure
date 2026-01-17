"""Goal to generate Clojure namespace metadata from JVM lockfiles.

This goal analyzes JAR files in JVM lockfiles to extract which Clojure namespaces
they provide, then generates metadata files that enable automatic dependency inference
for third-party Clojure libraries.

Usage:
    pants generate-clojure-lockfile-metadata ::
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pants.engine.console import Console
from pants.engine.fs import CreateDigest, Digest, DigestContents, FileContent, PathGlobs, Workspace
from pants.engine.goal import Goal, GoalSubsystem
from pants.engine.rules import Get, MultiGet, collect_rules, goal_rule, rule
from pants.engine.unions import UnionRule
from pants.jvm.resolve.coursier_fetch import (
    ClasspathEntry,
    CoursierLockfileEntry,
    CoursierResolvedLockfile,
)
from pants.jvm.subsystems import JvmSubsystem
from pants.util.logging import LogLevel

from pants_backend_clojure.utils.jar_analyzer import analyze_jar_for_namespaces


class GenerateClojureLockfileMetadataSubsystem(GoalSubsystem):
    """Generate Clojure namespace metadata from JVM lockfiles."""

    name = "generate-clojure-lockfile-metadata"
    help = """Generate Clojure namespace metadata from JVM lockfiles.

    This goal analyzes all JAR files in your JVM lockfiles to extract which
    Clojure namespaces they provide. It generates metadata JSON files that
    enable automatic dependency inference for third-party Clojure libraries.

    The metadata files are written to the same directory as the lockfiles,
    with the naming pattern: <resolve>_clojure_namespaces.json

    Example:
        3rdparty/jvm/default.lock -> 3rdparty/jvm/default_clojure_namespaces.json
        3rdparty/jvm/java17.lock -> 3rdparty/jvm/java17_clojure_namespaces.json
    """


class GenerateClojureLockfileMetadata(Goal):
    """Generate Clojure namespace metadata from JVM lockfiles."""

    subsystem_cls = GenerateClojureLockfileMetadataSubsystem
    environment_behavior = Goal.EnvironmentBehavior.LOCAL_ONLY


@dataclass(frozen=True)
class GenerateClojureLockfileMetadataRequest:
    """Request to generate Clojure namespace metadata for a single resolve."""

    resolve_name: str
    lockfile_path: str
    lockfile_digest: Digest


@dataclass(frozen=True)
class GeneratedClojureLockfileMetadata:
    """Generated Clojure namespace metadata for a resolve."""

    resolve_name: str
    metadata_path: str
    metadata_digest: Digest
    namespace_count: int
    artifact_count: int


@rule(desc="Generate Clojure namespace metadata for resolve", level=LogLevel.DEBUG)
async def generate_metadata_for_resolve(
    request: GenerateClojureLockfileMetadataRequest,
) -> GeneratedClojureLockfileMetadata:
    """Generate Clojure namespace metadata for a single JVM resolve.

    This function:
    1. Loads the lockfile
    2. Downloads all JARs in the lockfile
    3. Analyzes each JAR for Clojure namespaces
    4. Generates a metadata JSON file
    """
    import tempfile
    from pathlib import Path

    # Load the lockfile
    lockfile_contents = await Get(DigestContents, Digest, request.lockfile_digest)
    if not lockfile_contents:
        raise ValueError(f"Could not read lockfile at {request.lockfile_path}")

    lockfile = CoursierResolvedLockfile.from_serialized(lockfile_contents[0].content)

    # Fetch all JARs using MultiGet
    classpath_entries = await MultiGet(
        Get(ClasspathEntry, CoursierLockfileEntry, entry) for entry in lockfile.entries
    )

    artifact_namespaces: dict[str, tuple[str, tuple[str, ...]]] = {}
    total_namespaces = 0

    # Analyze each JAR
    for entry, classpath_entry in zip(lockfile.entries, classpath_entries):
        # Materialize the JAR to analyze it
        jar_contents = await Get(DigestContents, Digest, classpath_entry.digest)
        if not jar_contents:
            continue

        # Write to a temp file and analyze
        # TODO: In the future, we could make analyze_jar_for_namespaces accept bytes directly
        with tempfile.NamedTemporaryFile(suffix='.jar', delete=False) as tmp_jar:
            tmp_jar.write(jar_contents[0].content)
            tmp_jar.flush()
            jar_path = Path(tmp_jar.name)

            try:
                # Analyze the JAR for Clojure namespaces
                analysis = analyze_jar_for_namespaces(jar_path)

                if analysis.namespaces:
                    # Build the artifact coordinate string
                    coord_str = f"{entry.coord.group}:{entry.coord.artifact}:{entry.coord.version}"

                    # Use the pants_address from the lockfile entry if available
                    address = entry.pants_address or f"<unknown for {coord_str}>"

                    artifact_namespaces[coord_str] = (address, analysis.namespaces)
                    total_namespaces += len(analysis.namespaces)

            finally:
                # Clean up temp file
                jar_path.unlink(missing_ok=True)

    # Generate metadata JSON
    from pathlib import Path as PathlibPath

    lockfile_name = PathlibPath(request.lockfile_path).stem  # Remove .lock extension
    lockfile_parent = PathlibPath(request.lockfile_path).parent
    metadata_path = str(lockfile_parent / f"{lockfile_name}_clojure_namespaces.json")

    metadata = {
        "version": "1.0",
        "resolve": request.resolve_name,
        "lockfile": request.lockfile_path,
        "lockfile_hash": f"sha256:{request.lockfile_digest.fingerprint}",
        "artifacts": {
            coord: {
                "address": address,
                "namespaces": list(namespaces),
                "source": "jar-analysis",
            }
            for coord, (address, namespaces) in artifact_namespaces.items()
        },
    }

    metadata_json = json.dumps(metadata, indent=2, sort_keys=True)
    metadata_content = FileContent(metadata_path, metadata_json.encode("utf-8"))

    metadata_digest = await Get(Digest, CreateDigest([metadata_content]))

    return GeneratedClojureLockfileMetadata(
        resolve_name=request.resolve_name,
        metadata_path=metadata_path,
        metadata_digest=metadata_digest,
        namespace_count=total_namespaces,
        artifact_count=len(artifact_namespaces),
    )


@goal_rule
async def generate_clojure_lockfile_metadata(
    console: Console,
    jvm_subsystem: JvmSubsystem,
    workspace: Workspace,
) -> GenerateClojureLockfileMetadata:
    """Generate Clojure namespace metadata for all JVM resolves."""

    console.print_stdout("Generating Clojure namespace metadata from JVM lockfiles...")

    # Get all JVM resolves
    resolves = jvm_subsystem.resolves

    if not resolves:
        console.print_stdout("No JVM resolves configured. Nothing to do.")
        return GenerateClojureLockfileMetadata(exit_code=0)

    # Build requests for each resolve
    requests: list[GenerateClojureLockfileMetadataRequest] = []

    for resolve_name, lockfile_path in resolves.items():
        console.print_stdout(f"  Processing resolve '{resolve_name}' ({lockfile_path})...")

        # Get the lockfile digest
        try:
            lockfile_digest = await Get(Digest, PathGlobs([lockfile_path]))
            requests.append(
                GenerateClojureLockfileMetadataRequest(
                    resolve_name=resolve_name,
                    lockfile_path=lockfile_path,
                    lockfile_digest=lockfile_digest,
                )
            )
        except Exception as e:
            console.print_stderr(
                f"  ⚠ Warning: Could not read lockfile for resolve '{resolve_name}': {e}"
            )
            continue

    if not requests:
        console.print_stdout("No valid lockfiles found. Nothing to do.")
        return GenerateClojureLockfileMetadata(exit_code=0)

    # Generate metadata for all resolves in parallel
    results = await MultiGet(
        Get(GeneratedClojureLockfileMetadata, GenerateClojureLockfileMetadataRequest, req)
        for req in requests
    )

    # Write all metadata files
    for result in results:
        workspace.write_digest(result.metadata_digest)
        console.print_stdout(
            f"  ✓ Generated {result.metadata_path}: "
            f"{result.artifact_count} artifacts, {result.namespace_count} namespaces"
        )

    console.print_stdout(
        f"\n✓ Successfully generated metadata for {len(results)} resolve(s)."
    )

    return GenerateClojureLockfileMetadata(exit_code=0)


def rules():
    return collect_rules()
