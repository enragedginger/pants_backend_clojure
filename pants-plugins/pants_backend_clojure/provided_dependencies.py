from __future__ import annotations

from dataclasses import dataclass

from pants.engine.addresses import Address, Addresses, UnparsedAddressInputs
from pants.engine.fs import Digest, DigestContents, PathGlobs
from pants.engine.internals.graph import (
    resolve_targets,
    resolve_unparsed_address_inputs,
    transitive_targets,
)
from pants.engine.intrinsics import get_digest_contents, path_globs_to_digest
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import Targets, TransitiveTargets, TransitiveTargetsRequest
from pants.jvm.resolve.coursier_fetch import CoursierResolvedLockfile
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.target_types import JvmArtifactArtifactField, JvmArtifactGroupField
from pants.util.ordered_set import FrozenOrderedSet

from pants_backend_clojure.target_types import ClojureProvidedDependenciesField


@dataclass(frozen=True)
class ProvidedDependencies:
    """The complete set of addresses and coordinates for provided dependencies.

    Similar to Maven's "provided" scope - these dependencies are available during
    compilation but excluded from the final JAR.

    This includes both the directly specified provided dependencies and all their
    transitive dependencies. All these should be excluded from the final JAR.

    Attributes:
        addresses: Pants addresses to exclude (for first-party source filtering)
        coordinates: Maven groupId:artifactId pairs to exclude (for third-party JAR filtering)
    """
    addresses: FrozenOrderedSet[Address]
    coordinates: FrozenOrderedSet[tuple[str, str]]  # (group_id, artifact_id)


@dataclass(frozen=True)
class ResolveProvidedDependenciesRequest:
    """Request to resolve provided dependencies for a specific JVM resolve."""
    field: ClojureProvidedDependenciesField
    resolve_name: str | None  # None when only first-party sources are provided


def get_maven_transitive_coordinates(
    lockfile: CoursierResolvedLockfile,
    coordinates: set[tuple[str, str]]
) -> set[tuple[str, str]]:
    """Get full transitive closure of Maven coordinates from lockfile.

    Simply looks up each coordinate in the lockfile and collects the
    pre-computed transitive dependencies from entry.dependencies.
    No graph traversal needed - Coursier pre-computes the full closure.

    Args:
        lockfile: The parsed Coursier lockfile containing all entries
        coordinates: The initial set of (group, artifact) coordinates to expand

    Returns:
        The expanded set including all transitive Maven dependencies
    """
    # Build lookup dictionary: (group, artifact) -> entry
    # Note: We ignore version since provided uses version-agnostic matching.
    entries_by_coord: dict[tuple[str, str], list] = {}
    for entry in lockfile.entries:
        key = (entry.coord.group, entry.coord.artifact)
        if key not in entries_by_coord:
            entries_by_coord[key] = []
        entries_by_coord[key].append(entry)

    # Collect transitives - no BFS needed since entry.dependencies is already
    # the full transitive closure pre-computed by Coursier
    result = set(coordinates)
    for coord in coordinates:
        entries = entries_by_coord.get(coord, [])
        for entry in entries:
            # entry.dependencies is the FULL transitive closure, not just direct deps
            for dep in entry.dependencies:
                result.add((dep.group, dep.artifact))

    return result


@rule
async def resolve_provided_dependencies(
    request: ResolveProvidedDependenciesRequest,
    jvm: JvmSubsystem,
) -> ProvidedDependencies:
    """Resolve the full transitive closure of provided dependencies.

    This rule takes the provided field and computes the complete set of
    addresses and Maven coordinates that should be excluded from the final JAR.

    For first-party targets (clojure_source), uses address-based exclusion.
    For third-party targets (jvm_artifact), uses coordinate-based exclusion
    (groupId:artifactId, ignoring version for Maven "provided" scope semantics).

    When a resolve_name is provided, also looks up Maven transitive dependencies
    from the lockfile and includes them in the coordinates set.
    """
    field = request.field

    if not field.value:
        # No provided dependencies specified
        return ProvidedDependencies(
            addresses=FrozenOrderedSet(),
            coordinates=FrozenOrderedSet(),
        )

    # Parse the addresses from the field
    # SpecialCasedDependencies provides to_unparsed_address_inputs() method
    unparsed_inputs = field.to_unparsed_address_inputs()

    # Resolve to actual addresses first, then to targets
    provided_addresses = await resolve_unparsed_address_inputs(unparsed_inputs, **implicitly())
    provided_targets = await resolve_targets(**implicitly({provided_addresses: Addresses}))

    # Get the transitive closure for each provided dependency
    all_transitive = await concurrently(
        transitive_targets(TransitiveTargetsRequest([target.address]), **implicitly())
        for target in provided_targets
    )

    # Collect all addresses (both roots and their transitive dependencies)
    all_addresses: set[Address] = set()
    all_targets: list = []
    for transitive in all_transitive:
        # Add the root provided dependency itself
        all_addresses.add(transitive.roots[0].address)
        all_targets.extend(transitive.roots)
        # Add all transitive dependencies
        all_addresses.update(dep.address for dep in transitive.dependencies)
        all_targets.extend(transitive.dependencies)

    # Extract Maven coordinates from jvm_artifact targets (Pants target graph)
    # This enables coordinate-based filtering for third-party JARs
    coordinates: set[tuple[str, str]] = set()
    for target in all_targets:
        if target.has_field(JvmArtifactGroupField):
            group = target[JvmArtifactGroupField].value
            artifact = target[JvmArtifactArtifactField].value
            if group and artifact:
                coordinates.add((group, artifact))

    # Expand coordinates with Maven transitive dependencies from lockfile
    # This handles dependencies that exist only in the lockfile, not as Pants targets
    if request.resolve_name and coordinates:
        lockfile_path = jvm.resolves[request.resolve_name]
        lockfile_digest = await path_globs_to_digest(PathGlobs([lockfile_path]))
        lockfile_contents = await get_digest_contents(lockfile_digest)
        lockfile = CoursierResolvedLockfile.from_serialized(lockfile_contents[0].content)

        # Expand coordinates with Maven transitives from lockfile
        coordinates = get_maven_transitive_coordinates(lockfile, coordinates)

    return ProvidedDependencies(
        addresses=FrozenOrderedSet(sorted(all_addresses)),
        coordinates=FrozenOrderedSet(sorted(coordinates)),
    )


def rules():
    return collect_rules()
