"""Dependency inference for Clojure targets."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePath
from typing import DefaultDict

from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.core.util_rules.stripped_source_files import StrippedFileName, StrippedFileNameRequest
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
    Targets,
)
from pants.engine.unions import UnionRule
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.target_types import JvmResolveField
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel
from pants.util.ordered_set import OrderedSet

from clojure_backend.target_types import (
    ClojureSourceField,
    ClojureSourceTarget,
    ClojureTestSourceField,
    ClojureTestTarget,
)
from pants.jvm.target_types import JvmDependenciesField


# Regex patterns for parsing Clojure namespace declarations and requires
NS_PATTERN = re.compile(r'\(ns\s+([\w\.\-]+)', re.MULTILINE)


def parse_clojure_namespace(source_content: str) -> str | None:
    """Extract the namespace from a Clojure source file.

    Example:
        (ns example.project-a.core) -> "example.project-a.core"
    """
    match = NS_PATTERN.search(source_content)
    return match.group(1) if match else None


def parse_clojure_requires(source_content: str) -> set[str]:
    """Extract required namespaces from a Clojure source file.

    Handles both :require and :use forms.

    Examples:
        (ns example.foo
          (:require [example.bar :as bar]
                    [example.baz])
          (:use [example.qux]))

        Returns: {"example.bar", "example.baz", "example.qux"}
    """
    required_namespaces = set()

    # Find the ns form - it starts with (ns and ends with a matching paren
    ns_match = re.search(r'\(ns\s+[\w\.\-]+\s*(.*?)(?=\n\(|\Z)', source_content, re.DOTALL)
    if not ns_match:
        return required_namespaces

    ns_body = ns_match.group(1)

    # Find :require and :use sections - look for (:require ...) and (:use ...)
    for directive in [':require', ':use']:
        directive_match = re.search(rf'\({directive}\s+(.*?)(?=\(:|$)', ns_body, re.DOTALL)
        if not directive_match:
            continue

        directive_body = directive_match.group(1)

        # Extract namespaces - they appear at the start of [namespace ...] forms
        # Match patterns like [example.foo ...] or [example.bar]
        for match in re.finditer(r'\[([a-zA-Z][\w\.\-]*)', directive_body):
            namespace = match.group(1)
            # Only include if it looks like a namespace (has a dot)
            if '.' in namespace:
                required_namespaces.add(namespace)

    return required_namespaces


def parse_clojure_imports(source_content: str) -> set[str]:
    """Extract Java class imports from :import forms.

    Handles both vector and single-class import syntax.

    Examples:
        (ns example.foo
          (:import [java.util Date ArrayList]
                   [java.io File]))

        Returns: {"java.util.Date", "java.util.ArrayList", "java.io.File"}

        (ns example.bar
          (:import java.util.Date
                   java.io.File))

        Returns: {"java.util.Date", "java.io.File"}
    """
    imported_classes = set()

    # Find the ns form
    ns_match = re.search(r'\(ns\s+[\w\.\-]+\s*(.*?)(?=\n\(|\Z)', source_content, re.DOTALL)
    if not ns_match:
        return imported_classes

    ns_body = ns_match.group(1)

    # Find :import section
    import_match = re.search(r'\(:import\s+(.*?)(?=\(:|$)', ns_body, re.DOTALL)
    if not import_match:
        return imported_classes

    import_body = import_match.group(1)

    # Handle vector syntax: [java.util Date ArrayList]
    # Match [package Class1 Class2 ...]
    for match in re.finditer(r'\[([a-zA-Z][\w\.]*)\s+([^\]]+)\]', import_body):
        package = match.group(1)
        classes_str = match.group(2)
        # Split on whitespace to get individual class names
        class_names = classes_str.split()
        for class_name in class_names:
            # Only include valid class names (start with letter, no special chars except _)
            if re.match(r'^[A-Z][\w\$]*$', class_name):
                imported_classes.add(f"{package}.{class_name}")

    # Handle single-class syntax: java.util.Date
    # Match fully-qualified class names (package.Class)
    # Must have at least one dot and end with uppercase letter (class name)
    for match in re.finditer(r'\b([a-z][\w]*(?:\.[a-z][\w]*)+\.[A-Z][\w\$]*)\b', import_body):
        class_name = match.group(1)
        # Avoid matching things inside vector forms (already handled above)
        imported_classes.add(class_name)

    return imported_classes


def class_to_path(class_name: str) -> str:
    """Convert a Java class name to its expected file path.

    Examples:
        "com.example.Foo" -> "com/example/Foo.java"
        "java.util.HashMap" -> "java/util/HashMap.java"
        "java.util.Map$Entry" -> "java/util/Map.java" (inner class)

    Note: Inner classes (containing $) are mapped to their outer class file.
    """
    # Handle inner classes by taking only the outer class
    if '$' in class_name:
        class_name = class_name.split('$')[0]

    path = class_name.replace('.', '/')
    return f"{path}.java"


def is_jdk_class(class_name: str) -> bool:
    """Check if a class is part of the JDK (implicit dependency).

    JDK packages include:
    - java.* (java.lang, java.util, java.io, etc.)
    - javax.* (javax.swing, javax.sql, etc.)
    - sun.* (internal, discouraged but sometimes used)
    - jdk.* (JDK 9+ modules)
    """
    jdk_prefixes = ("java.", "javax.", "sun.", "jdk.")
    return any(class_name.startswith(prefix) for prefix in jdk_prefixes)


def namespace_to_path(namespace: str) -> str:
    """Convert a Clojure namespace to its expected file path.

    Example:
        "example.project-a.core" -> "example/project_a/core.clj"

    Note: Clojure uses underscores in file paths for hyphens in namespaces.
    """
    path = namespace.replace('.', '/').replace('-', '_')
    return f"{path}.clj"


def path_to_namespace(file_path: str) -> str:
    """Convert a file path to a Clojure namespace.

    Example:
        "example/project_a/core.clj" -> "example.project-a.core"

    Note: Clojure uses hyphens in namespaces for underscores in file paths.
    """
    # Remove .clj or .cljc extension
    path = file_path
    if path.endswith('.clj'):
        path = path[:-4]
    elif path.endswith('.cljc'):
        path = path[:-5]

    # Convert path separators to dots and underscores to hyphens
    namespace = path.replace('/', '.').replace('_', '-')
    return namespace


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
) -> InferredDependencies:
    """Infer dependencies for a Clojure source file by analyzing its :require forms."""

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

    # Parse required namespaces
    required_namespaces = parse_clojure_requires(source_content)

    if not required_namespaces:
        return InferredDependencies([])

    # Convert namespaces to potential file paths and find owners
    dependencies: OrderedSet[Address] = OrderedSet()

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

    return InferredDependencies(sorted(dependencies))


@rule(desc="Infer Clojure test dependencies", level=LogLevel.DEBUG)
async def infer_clojure_test_dependencies(
    request: InferClojureTestDependencies,
    jvm: JvmSubsystem,
) -> InferredDependencies:
    """Infer dependencies for a Clojure test file by analyzing its :require forms."""

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

    # Parse required namespaces
    required_namespaces = parse_clojure_requires(source_content)

    if not required_namespaces:
        return InferredDependencies([])

    # Convert namespaces to potential file paths and find owners
    dependencies: OrderedSet[Address] = OrderedSet()

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

    return InferredDependencies(sorted(dependencies))


def rules():
    return [
        *collect_rules(),
        UnionRule(InferDependenciesRequest, InferClojureSourceDependencies),
        UnionRule(InferDependenciesRequest, InferClojureTestDependencies),
    ]
