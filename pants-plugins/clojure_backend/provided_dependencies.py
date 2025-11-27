from __future__ import annotations

from dataclasses import dataclass

from pants.engine.addresses import Address, UnparsedAddressInputs
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.rules import collect_rules, rule
from pants.engine.target import Targets, TransitiveTargets, TransitiveTargetsRequest
from pants.jvm.target_types import JvmArtifactArtifactField, JvmArtifactGroupField
from pants.util.ordered_set import FrozenOrderedSet

from clojure_backend.target_types import ClojureProvidedDependenciesField


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


@rule
async def resolve_provided_dependencies(
    field: ClojureProvidedDependenciesField,
) -> ProvidedDependencies:
    """Resolve the full transitive closure of provided dependencies.

    This rule takes the provided field and computes the complete set of
    addresses and Maven coordinates that should be excluded from the final JAR.

    For first-party targets (clojure_source), uses address-based exclusion.
    For third-party targets (jvm_artifact), uses coordinate-based exclusion
    (groupId:artifactId, ignoring version for Maven "provided" scope semantics).
    """
    if not field.value:
        # No provided dependencies specified
        return ProvidedDependencies(
            addresses=FrozenOrderedSet(),
            coordinates=FrozenOrderedSet(),
        )

    # Parse the addresses from the field
    # SpecialCasedDependencies provides to_unparsed_address_inputs() method
    unparsed_inputs = field.to_unparsed_address_inputs()

    # Resolve to actual target objects
    provided_targets = await Get(Targets, UnparsedAddressInputs, unparsed_inputs)

    # Get the transitive closure for each provided dependency
    all_transitive = await MultiGet(
        Get(TransitiveTargets, TransitiveTargetsRequest([target.address]))
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

    # Extract Maven coordinates from jvm_artifact targets
    # This enables coordinate-based filtering for third-party JARs
    coordinates: set[tuple[str, str]] = set()
    for target in all_targets:
        if target.has_field(JvmArtifactGroupField):
            group = target[JvmArtifactGroupField].value
            artifact = target[JvmArtifactArtifactField].value
            if group and artifact:
                coordinates.add((group, artifact))

    return ProvidedDependencies(
        addresses=FrozenOrderedSet(sorted(all_addresses)),
        coordinates=FrozenOrderedSet(sorted(coordinates)),
    )


def rules():
    return collect_rules()
