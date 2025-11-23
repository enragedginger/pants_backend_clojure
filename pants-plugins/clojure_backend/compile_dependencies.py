from __future__ import annotations

from dataclasses import dataclass

from pants.engine.addresses import Address, Addresses, UnparsedAddressInputs
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.rules import collect_rules, rule
from pants.engine.target import Targets, TransitiveTargets, TransitiveTargetsRequest
from pants.util.ordered_set import FrozenOrderedSet

from clojure_backend.target_types import ClojureCompileDependenciesField


@dataclass(frozen=True)
class CompileOnlyDependencies:
    """The complete set of addresses for compile-only dependencies and their transitives.

    This includes both the directly specified compile-only dependencies and all their
    transitive dependencies. All these addresses should be excluded from the final JAR.
    """
    addresses: FrozenOrderedSet[Address]


@rule
async def resolve_compile_only_dependencies(
    field: ClojureCompileDependenciesField,
) -> CompileOnlyDependencies:
    """Resolve the full transitive closure of compile-only dependencies.

    This rule takes the compile_dependencies field and computes the complete set of
    addresses that should be excluded from the final JAR. This includes:
    - The directly specified compile-only dependencies
    - All transitive dependencies of those compile-only dependencies

    The rule works for both first-party (clojure_source) and third-party (jvm_artifact)
    dependencies. Pants' TransitiveTargets handles both cases automatically.
    """
    if not field.value:
        # No compile-only dependencies specified
        return CompileOnlyDependencies(FrozenOrderedSet())

    # Parse the addresses from the field
    # SpecialCasedDependencies provides to_unparsed_address_inputs() method
    unparsed_inputs = field.to_unparsed_address_inputs()

    # Resolve to actual target objects
    compile_only_targets = await Get(Targets, UnparsedAddressInputs, unparsed_inputs)

    # Get the transitive closure for each compile-only dependency
    all_transitive = await MultiGet(
        Get(TransitiveTargets, TransitiveTargetsRequest([target.address]))
        for target in compile_only_targets
    )

    # Collect all addresses (both roots and their transitive dependencies)
    all_addresses: set[Address] = set()
    for transitive in all_transitive:
        # Add the root compile-only dependency itself
        all_addresses.add(transitive.roots[0].address)
        # Add all transitive dependencies
        all_addresses.update(dep.address for dep in transitive.dependencies)

    return CompileOnlyDependencies(FrozenOrderedSet(sorted(all_addresses)))


def rules():
    return collect_rules()
