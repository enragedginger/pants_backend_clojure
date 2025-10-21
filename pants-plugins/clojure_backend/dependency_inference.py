"""Dependency inference for Clojure targets."""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.addresses import Address
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
)
from pants.engine.unions import UnionRule
from pants.jvm.dependency_inference.symbol_mapper import SymbolMapping
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.target_types import JvmDependenciesField, JvmResolveField
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel
from pants.util.ordered_set import OrderedSet

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


@rule(desc="Infer Clojure source dependencies", level=LogLevel.DEBUG)
async def infer_clojure_source_dependencies(
    request: InferClojureSourceDependencies,
    jvm: JvmSubsystem,
    symbol_mapping: SymbolMapping,
) -> InferredDependencies:
    """Infer dependencies for a Clojure source file by analyzing its :require and :import forms."""

    # Get explicitly provided dependencies for disambiguation and source file
    explicitly_provided_deps, source_files = await MultiGet(
        Get(ExplicitlyProvidedDependencies, DependenciesRequest(request.field_set.dependencies)),
        Get(SourceFiles, SourceFilesRequest([request.field_set.source])),
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
    my_resolve = request.field_set.resolve.normalized_value(jvm)

    for namespace in required_namespaces:
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

        for path in possible_paths:
            owners = await Get(Owners, OwnersRequest((path,)))
            if owners:
                # Filter owners to only those with matching resolve
                # This handles cases where the same file has multiple targets with different resolves
                my_resolve = request.field_set.resolve.normalized_value(jvm)

                # Group owners by whether they match our resolve
                # We need to check the target's resolve field, but we can infer it from the address
                # For generated targets like "core.clj:../../java17", the resolve is in the generator
                matching_owners = []
                for addr in owners:
                    # Check if the address contains a resolve indicator
                    # Generated targets have addresses like "path/file.clj:../../resolve_name"
                    if f":../../{my_resolve}" in str(addr) or f":{my_resolve}" in str(addr):
                        matching_owners.append(addr)

                # If we found matching owners, use those; otherwise fall back to all owners
                candidates = tuple(matching_owners) if matching_owners else owners

                # Use disambiguated to handle remaining ambiguity
                explicitly_provided_deps.maybe_warn_of_ambiguous_dependency_inference(
                    candidates,
                    request.field_set.address,
                    import_reference="namespace",
                    context=f"The target {request.field_set.address} requires `{namespace}`",
                )
                maybe_disambiguated = explicitly_provided_deps.disambiguated(candidates)
                if maybe_disambiguated:
                    dependencies.add(maybe_disambiguated)
                break  # Found owners, no need to try other paths

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
                request.field_set.address,
                import_reference="class",
                context=f"The target {request.field_set.address} imports `{class_name}`",
            )
            maybe_disambiguated = explicitly_provided_deps.disambiguated(matches)
            if maybe_disambiguated:
                dependencies.add(maybe_disambiguated)

    return InferredDependencies(sorted(dependencies))


@rule(desc="Infer Clojure test dependencies", level=LogLevel.DEBUG)
async def infer_clojure_test_dependencies(
    request: InferClojureTestDependencies,
    jvm: JvmSubsystem,
    symbol_mapping: SymbolMapping,
) -> InferredDependencies:
    """Infer dependencies for a Clojure test file by analyzing its :require and :import forms."""

    # Get explicitly provided dependencies for disambiguation and source file
    explicitly_provided_deps, source_files = await MultiGet(
        Get(ExplicitlyProvidedDependencies, DependenciesRequest(request.field_set.dependencies)),
        Get(SourceFiles, SourceFilesRequest([request.field_set.source])),
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
    my_resolve = request.field_set.resolve.normalized_value(jvm)

    for namespace in required_namespaces:
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

        for path in possible_paths:
            owners = await Get(Owners, OwnersRequest((path,)))
            if owners:
                # Filter owners to only those with matching resolve
                # This handles cases where the same file has multiple targets with different resolves
                # Group owners by whether they match our resolve
                # We need to check the target's resolve field, but we can infer it from the address
                # For generated targets like "core.clj:../../java17", the resolve is in the generator
                matching_owners = []
                for addr in owners:
                    # Check if the address contains a resolve indicator
                    # Generated targets have addresses like "path/file.clj:../../resolve_name"
                    if f":../../{my_resolve}" in str(addr) or f":{my_resolve}" in str(addr):
                        matching_owners.append(addr)

                # If we found matching owners, use those; otherwise fall back to all owners
                candidates = tuple(matching_owners) if matching_owners else owners

                # Use disambiguated to handle remaining ambiguity
                explicitly_provided_deps.maybe_warn_of_ambiguous_dependency_inference(
                    candidates,
                    request.field_set.address,
                    import_reference="namespace",
                    context=f"The target {request.field_set.address} requires `{namespace}`",
                )
                maybe_disambiguated = explicitly_provided_deps.disambiguated(candidates)
                if maybe_disambiguated:
                    dependencies.add(maybe_disambiguated)
                break  # Found owners, no need to try other paths

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
                request.field_set.address,
                import_reference="class",
                context=f"The target {request.field_set.address} imports `{class_name}`",
            )
            maybe_disambiguated = explicitly_provided_deps.disambiguated(matches)
            if maybe_disambiguated:
                dependencies.add(maybe_disambiguated)

    return InferredDependencies(sorted(dependencies))


def rules():
    return [
        *collect_rules(),
        UnionRule(InferDependenciesRequest, InferClojureSourceDependencies),
        UnionRule(InferDependenciesRequest, InferClojureTestDependencies),
    ]
