"""Dependency inference for Clojure targets."""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.addresses import Address, Addresses
from pants.engine.fs import Digest, DigestContents
from pants.engine.internals.graph import Owners, OwnersRequest
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.rules import collect_rules, rule
from pants.engine.target import (
    DependenciesRequest,
    ExplicitlyProvidedDependencies,
    FieldSet,
    InferDependenciesRequest,
    InferredDependencies,
    Targets,
)
from pants.engine.unions import UnionRule
from pants.jvm.dependency_inference.symbol_mapper import SymbolMapping
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.target_types import JvmDependenciesField, JvmResolveField
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel
from pants.util.ordered_set import OrderedSet

from clojure_backend.clojure_symbol_mapping import ClojureNamespaceMapping
from clojure_backend.target_types import (
    ClojureSourceField,
    ClojureSourceTarget,
    ClojureTestSourceField,
    ClojureTestTarget,
)
from clojure_backend.utils.namespace_parser import (
    is_jdk_class,
    namespace_to_path,
    parse_imports,
    parse_requires,
)


@dataclass(frozen=True)
class ClojureSourceDependenciesInferenceFieldSet(FieldSet):
    """FieldSet for inferring dependencies of Clojure source files."""

    required_fields = (ClojureSourceField, JvmDependenciesField, JvmResolveField)

    address: Address
    source: ClojureSourceField
    dependencies: JvmDependenciesField
    resolve: JvmResolveField


@dataclass(frozen=True)
class ClojureTestDependenciesInferenceFieldSet(FieldSet):
    """FieldSet for inferring dependencies of Clojure test files."""

    required_fields = (ClojureTestSourceField, JvmDependenciesField, JvmResolveField)

    address: Address
    source: ClojureTestSourceField
    dependencies: JvmDependenciesField
    resolve: JvmResolveField


class InferClojureSourceDependencies(InferDependenciesRequest):
    """Request to infer dependencies for Clojure source files."""

    infer_from = ClojureSourceDependenciesInferenceFieldSet


class InferClojureTestDependencies(InferDependenciesRequest):
    """Request to infer dependencies for Clojure test files."""

    infer_from = ClojureTestDependenciesInferenceFieldSet


@dataclass(frozen=True)
class ClojureMapping:
    """Mapping from Clojure namespaces to target addresses.

    Used for dependency inference.
    """

    # Maps (namespace, resolve) -> Address
    mapping: FrozenDict[tuple[str, str], Address]
    # Tracks namespaces with multiple providers per resolve
    ambiguous_modules: FrozenDict[tuple[str, str], tuple[Address, ...]]


async def _infer_clojure_dependencies_impl(
    field_set: ClojureSourceDependenciesInferenceFieldSet | ClojureTestDependenciesInferenceFieldSet,
    jvm: JvmSubsystem,
    symbol_mapping: SymbolMapping,
    clojure_mapping: ClojureNamespaceMapping,
) -> InferredDependencies:
    """Shared implementation for inferring dependencies of Clojure files.

    This function handles both source and test files, extracting required namespaces
    and imported Java classes, then resolving them to target dependencies.

    Args:
        field_set: The field set for the target being analyzed (source or test)
        jvm: JVM subsystem for resolve information
        symbol_mapping: Mapping for resolving Java class dependencies
        clojure_mapping: Mapping for resolving third-party Clojure namespace dependencies

    Returns:
        InferredDependencies containing all resolved dependencies
    """
    # Get explicitly provided dependencies for disambiguation and source file
    explicitly_provided_deps, source_files = await MultiGet(
        Get(ExplicitlyProvidedDependencies, DependenciesRequest(field_set.dependencies)),
        Get(SourceFiles, SourceFilesRequest([field_set.source])),
    )

    # Read the source file content
    digest_contents = await Get(DigestContents, Digest, source_files.snapshot.digest)
    if not digest_contents:
        return InferredDependencies([])

    source_content = digest_contents[0].content.decode('utf-8')

    # Parse required Clojure namespaces and imported Java classes
    required_namespaces = parse_requires(source_content)
    imported_classes = parse_imports(source_content)

    # Convert namespaces to potential file paths and find owners
    dependencies: OrderedSet[Address] = OrderedSet()
    my_resolve = field_set.resolve.normalized_value(jvm)

    for namespace in required_namespaces:
        # Strategy: Try first-party sources first, then fall back to third-party mapping
        # This ensures that local code takes precedence over third-party libraries

        # FIRST: Try first-party sources using OwnersRequest
        # Convert namespace to expected file path
        # e.g., "example.project-a.core" -> "example/project_a/core.clj"
        file_path = namespace_to_path(namespace)

        # Try to find owners with different path variations
        # Since we don't know the source root, try with just the namespace path
        # and also try **/path (glob pattern)
        possible_paths = [
            file_path,  # Direct path
            f"**/{file_path}",  # Glob to find anywhere in project
        ]

        found_first_party = False
        for path in possible_paths:
            owners = await Get(Owners, OwnersRequest((path,)))
            if owners:
                # Filter owners to only those with matching resolve
                # This handles cases where the same file has multiple targets with different resolves
                # Get actual targets to check their resolve fields
                owner_targets = await Get(Targets, Addresses(owners))

                matching_owners = []
                for target in owner_targets:
                    # Check if target has a resolve field and if it matches our resolve
                    if target.has_field(JvmResolveField):
                        target_resolve = target[JvmResolveField].normalized_value(jvm)
                        if target_resolve == my_resolve:
                            matching_owners.append(target.address)

                # If we found matching owners, use those; otherwise fall back to all owners
                candidates = tuple(matching_owners) if matching_owners else owners

                # Use disambiguated to handle remaining ambiguity
                explicitly_provided_deps.maybe_warn_of_ambiguous_dependency_inference(
                    candidates,
                    field_set.address,
                    import_reference="namespace",
                    context=f"The target {field_set.address} requires `{namespace}`",
                )
                maybe_disambiguated = explicitly_provided_deps.disambiguated(candidates)
                if maybe_disambiguated:
                    dependencies.add(maybe_disambiguated)
                found_first_party = True
                break  # Found owners, no need to try other paths

        # SECOND: If no first-party source found, check third-party mapping
        if not found_first_party:
            third_party_addrs = clojure_mapping.addresses_for_namespace(namespace, my_resolve)
            if third_party_addrs:
                # Found in third-party mapping - apply same disambiguation logic
                explicitly_provided_deps.maybe_warn_of_ambiguous_dependency_inference(
                    third_party_addrs,
                    field_set.address,
                    import_reference="namespace",
                    context=f"The target {field_set.address} requires `{namespace}`",
                )
                maybe_disambiguated = explicitly_provided_deps.disambiguated(third_party_addrs)
                if maybe_disambiguated:
                    dependencies.add(maybe_disambiguated)

    # Handle Java class imports using SymbolMapping
    for class_name in imported_classes:
        # Skip JDK classes (implicit in classpath)
        if is_jdk_class(class_name):
            continue

        # Query symbol mapping for this class
        # This handles both first-party Java sources and third-party artifacts
        symbol_matches = symbol_mapping.addresses_for_symbol(class_name, my_resolve)

        # Flatten matches from all namespaces and add to dependencies
        for matches in symbol_matches.values():
            explicitly_provided_deps.maybe_warn_of_ambiguous_dependency_inference(
                matches,
                field_set.address,
                import_reference="class",
                context=f"The target {field_set.address} imports `{class_name}`",
            )
            maybe_disambiguated = explicitly_provided_deps.disambiguated(matches)
            if maybe_disambiguated:
                dependencies.add(maybe_disambiguated)

    return InferredDependencies(sorted(dependencies))


@rule(desc="Infer Clojure source dependencies", level=LogLevel.DEBUG)
async def infer_clojure_source_dependencies(
    request: InferClojureSourceDependencies,
    jvm: JvmSubsystem,
    symbol_mapping: SymbolMapping,
    clojure_mapping: ClojureNamespaceMapping,
) -> InferredDependencies:
    """Infer dependencies for a Clojure source file by analyzing its :require and :import forms."""
    return await _infer_clojure_dependencies_impl(request.field_set, jvm, symbol_mapping, clojure_mapping)


@rule(desc="Infer Clojure test dependencies", level=LogLevel.DEBUG)
async def infer_clojure_test_dependencies(
    request: InferClojureTestDependencies,
    jvm: JvmSubsystem,
    symbol_mapping: SymbolMapping,
    clojure_mapping: ClojureNamespaceMapping,
) -> InferredDependencies:
    """Infer dependencies for a Clojure test file by analyzing its :require and :import forms."""
    return await _infer_clojure_dependencies_impl(request.field_set, jvm, symbol_mapping, clojure_mapping)


def rules():
    return [
        *collect_rules(),
        UnionRule(InferDependenciesRequest, InferClojureSourceDependencies),
        UnionRule(InferDependenciesRequest, InferClojureTestDependencies),
    ]
