"""Clojure namespace to JVM artifact mapping.

This module provides a mapping from Clojure namespaces to jvm_artifact addresses,
enabling automatic dependency inference for third-party Clojure libraries.

The mapping is built automatically by analyzing JARs from JVM lockfiles. This approach:
- Works immediately after `pants generate-lockfiles` - no manual metadata step required
- Analyzes actual JAR contents for accuracy
- Is cached by Pants' rule system based on lockfile digest

Similar to Pants' SymbolMapping for Java classes, but specifically for Clojure
namespaces which have different semantics and file structure conventions.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pants.engine.addresses import Address
from pants.engine.fs import Digest, DigestContents, FileContent, PathGlobs
from pants.engine.intrinsics import get_digest_contents, path_globs_to_digest
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.jvm.dependency_inference.artifact_mapper import (
    AllJvmArtifactTargets,
    DEFAULT_SYMBOL_NAMESPACE,
    FrozenTrieNode,
    MutableTrieNode,
)
from pants.jvm.resolve.coursier_fetch import (
    CoursierResolvedLockfile,
    coursier_fetch_one_coord,
)
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.target_types import (
    JvmArtifactArtifactField,
    JvmArtifactGroupField,
    JvmArtifactPackagesField,
    JvmResolveField,
)
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel

from pants_backend_clojure.utils.jar_analyzer import analyze_jar_for_namespaces


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClojureNamespaceMapping:
    """Mapping from Clojure namespaces to jvm_artifact addresses using a trie.

    This mapping is built automatically by analyzing JARs in the JVM lockfiles.
    It enables automatic dependency inference for third-party Clojure libraries.

    The trie structure allows efficient pattern matching with wildcard patterns
    like `my.namespace.**` which matches the namespace and all sub-namespaces.

    Attributes:
        mapping_per_resolve: Maps resolve name -> FrozenTrieNode containing
                            namespace -> addresses mappings.
    """
    mapping_per_resolve: FrozenDict[str, FrozenTrieNode]

    def addresses_for_namespace(
        self,
        namespace: str,
        resolve: str,
    ) -> tuple[Address, ...]:
        """Look up which jvm_artifact(s) provide a given Clojure namespace.

        This method uses trie-based lookup which supports:
        - Exact namespace matches
        - Recursive pattern matches (e.g., `ring.**` matches `ring.core`)

        Args:
            namespace: The Clojure namespace to look up (e.g., "clojure.data.json").
            resolve: The JVM resolve name (e.g., "default", "java17").

        Returns:
            Tuple of addresses that provide this namespace. Empty tuple if not found.
            Multiple addresses indicate ambiguity - the namespace is provided by
            multiple artifacts and needs disambiguation.

        Examples:
            >>> mapping.addresses_for_namespace("clojure.data.json", "default")
            (Address("3rdparty/jvm", target_name="data-json"),)

            >>> mapping.addresses_for_namespace("unknown.namespace", "default")
            ()

            >>> mapping.addresses_for_namespace("ring.middleware.cookies", "default")
            # Matches if "ring.**" or "ring.middleware.**" is registered
            (Address("3rdparty/jvm", target_name="ring-core"),)
        """
        trie = self.mapping_per_resolve.get(resolve)
        if not trie:
            return ()

        # Use the trie's built-in symbol lookup which handles recursive patterns
        matches = trie.addresses_for_symbol(namespace)
        if not matches:
            return ()

        # Flatten addresses from all namespaces (typically just DEFAULT_SYMBOL_NAMESPACE)
        result: list[Address] = []
        for addresses in matches.values():
            for addr in addresses:
                if addr not in result:
                    result.append(addr)
        return tuple(result)


# ============================================================================
# Third-party namespace mapping via automatic JAR analysis
# ============================================================================


@dataclass(frozen=True)
class ThirdPartyClojureNamespaceMappingRequest:
    """Request to build namespace mapping for a resolve by analyzing JARs."""
    resolve_name: str


@dataclass(frozen=True)
class ThirdPartyClojureNamespaceMapping:
    """Mapping of Clojure namespaces to jvm_artifact addresses for a resolve.

    This is the result of analyzing all JARs in a lockfile to discover which
    Clojure namespaces they provide.
    """
    # namespace -> addresses (multiple if ambiguous)
    mapping: FrozenDict[str, tuple[Address, ...]]


# ============================================================================
# Manual namespace overrides via `packages` field on jvm_artifact
# ============================================================================


@dataclass(frozen=True)
class AvailableClojureArtifactPackages:
    """Manual namespace overrides from jvm_artifact targets.

    This contains namespace patterns specified via the `packages` field on
    jvm_artifact targets. These patterns take highest precedence for namespace
    resolution.

    The `packages` field supports glob patterns like `my.namespace.**` which
    matches the namespace and all sub-namespaces.
    """
    # Maps (resolve, namespace_pattern) -> addresses
    # Multiple addresses if the same pattern is declared on multiple artifacts
    mapping: FrozenDict[tuple[str, str], tuple[Address, ...]]


@rule(desc="Extract packages from jvm_artifact targets", level=LogLevel.DEBUG)
async def find_clojure_artifact_packages(
    all_jvm_artifact_tgts: AllJvmArtifactTargets,
    jvm: JvmSubsystem,
) -> AvailableClojureArtifactPackages:
    """Extract packages field from jvm_artifact targets for Clojure namespace inference.

    This rule collects all explicitly declared `packages` fields from jvm_artifact
    targets. These manual declarations take highest precedence over automatic JAR
    analysis.

    Example BUILD file:
        jvm_artifact(
            name="cheshire",
            group="cheshire",
            artifact="cheshire",
            version="5.11.0",
            packages=["cheshire.**"],  # Manual namespace declaration
        )
    """
    # Maps (resolve, namespace_pattern) -> list of addresses
    mapping: dict[tuple[str, str], list[Address]] = {}

    for tgt in all_jvm_artifact_tgts:
        packages = tgt[JvmArtifactPackagesField].value
        if not packages:
            # Skip targets without explicit packages field
            continue

        resolve = tgt[JvmResolveField].normalized_value(jvm)

        for package_pattern in packages:
            key = (resolve, package_pattern)
            if key not in mapping:
                mapping[key] = []
            if tgt.address not in mapping[key]:
                mapping[key].append(tgt.address)

    return AvailableClojureArtifactPackages(
        FrozenDict({key: tuple(addrs) for key, addrs in mapping.items()})
    )


@rule(desc="Analyzing JARs for Clojure namespaces", level=LogLevel.DEBUG)
async def build_third_party_clojure_namespace_mapping(
    request: ThirdPartyClojureNamespaceMappingRequest,
    jvm: JvmSubsystem,
) -> ThirdPartyClojureNamespaceMapping:
    """Build namespace mapping for a resolve by analyzing JARs from the lockfile.

    This rule:
    1. Loads the lockfile for the specified resolve
    2. Fetches all JARs (using Coursier cache)
    3. Analyzes each JAR for Clojure namespaces
    4. Returns a mapping from namespace to jvm_artifact addresses
    """
    # Get lockfile path for this resolve
    lockfile_path = jvm.resolves.get(request.resolve_name)
    if not lockfile_path:
        logger.debug(f"No lockfile found for resolve '{request.resolve_name}'")
        return ThirdPartyClojureNamespaceMapping(FrozenDict())

    # Read the lockfile
    try:
        lockfile_digest = await path_globs_to_digest(PathGlobs([lockfile_path]))
        lockfile_contents = await get_digest_contents(lockfile_digest)
        if not lockfile_contents:
            logger.debug(f"Lockfile at {lockfile_path} is empty")
            return ThirdPartyClojureNamespaceMapping(FrozenDict())
    except Exception as e:
        logger.warning(f"Could not read lockfile at {lockfile_path}: {e}")
        return ThirdPartyClojureNamespaceMapping(FrozenDict())

    # Parse lockfile
    try:
        lockfile = CoursierResolvedLockfile.from_serialized(lockfile_contents[0].content)
    except Exception as e:
        logger.warning(f"Could not parse lockfile at {lockfile_path}: {e}")
        return ThirdPartyClojureNamespaceMapping(FrozenDict())

    if not lockfile.entries:
        return ThirdPartyClojureNamespaceMapping(FrozenDict())

    # Fetch all JARs using Coursier (uses cache)
    classpath_entries = await concurrently(
        coursier_fetch_one_coord(entry, **implicitly())
        for entry in lockfile.entries
    )

    # Analyze each JAR for Clojure namespaces
    mapping: dict[str, list[Address]] = {}

    for entry, classpath_entry in zip(lockfile.entries, classpath_entries):
        # Skip entries without pants_address (shouldn't happen in practice)
        if not entry.pants_address:
            continue

        address = Address.parse(entry.pants_address)

        # Materialize JAR to analyze it
        try:
            jar_contents = await get_digest_contents(classpath_entry.digest)
            if not jar_contents:
                continue

            # Write to temp file and analyze
            # TODO: Consider making analyze_jar_for_namespaces accept bytes directly
            with tempfile.NamedTemporaryFile(suffix='.jar', delete=False) as tmp_jar:
                tmp_jar.write(jar_contents[0].content)
                tmp_jar.flush()
                jar_path = Path(tmp_jar.name)

                try:
                    analysis = analyze_jar_for_namespaces(jar_path)

                    for namespace in analysis.namespaces:
                        if namespace not in mapping:
                            mapping[namespace] = []
                        if address not in mapping[namespace]:
                            mapping[namespace].append(address)

                finally:
                    jar_path.unlink(missing_ok=True)

        except Exception as e:
            coord_str = f"{entry.coord.group}:{entry.coord.artifact}:{entry.coord.version}"
            logger.debug(f"Error analyzing JAR for {coord_str}: {e}")
            continue

    return ThirdPartyClojureNamespaceMapping(
        FrozenDict({ns: tuple(addrs) for ns, addrs in mapping.items()})
    )


# ============================================================================
# Main mapping rule - combines automatic analysis with metadata files
# ============================================================================


def _namespace_matches_pattern(namespace: str, pattern: str) -> bool:
    """Check if a namespace matches a pattern.

    Supports exact matches and recursive glob patterns with `.**` suffix.

    Note: This function is kept for backwards compatibility and testing.
    The main lookup now uses trie-based matching which handles patterns natively.

    Args:
        namespace: The namespace to check (e.g., "ring.middleware.cookies")
        pattern: The pattern to match against (e.g., "ring.middleware.**")

    Returns:
        True if the namespace matches the pattern.

    Examples:
        >>> _namespace_matches_pattern("ring.core", "ring.core")
        True
        >>> _namespace_matches_pattern("ring.middleware.cookies", "ring.**")
        True
        >>> _namespace_matches_pattern("ring.middleware.cookies", "ring.middleware.**")
        True
        >>> _namespace_matches_pattern("other.ns", "ring.**")
        False
    """
    if pattern.endswith(".**"):
        # Recursive glob pattern: matches the base and all sub-namespaces
        base = pattern[:-3]  # Remove ".**"
        return namespace == base or namespace.startswith(base + ".")
    else:
        # Exact match
        return namespace == pattern


def _parse_namespace_pattern(pattern: str) -> tuple[str, bool]:
    """Parse a namespace pattern into symbol and recursive flag.

    Args:
        pattern: A namespace pattern, optionally ending with `.**`

    Returns:
        Tuple of (symbol, recursive) where symbol is the base namespace
        and recursive indicates if the pattern includes sub-namespaces.

    Examples:
        >>> _parse_namespace_pattern("ring.core")
        ("ring.core", False)
        >>> _parse_namespace_pattern("ring.**")
        ("ring", True)
    """
    wildcard_suffix = ".**"
    if pattern.endswith(wildcard_suffix):
        return pattern[:-len(wildcard_suffix)], True
    else:
        return pattern, False


@rule(desc="Build Clojure namespace mapping", level=LogLevel.DEBUG)
async def load_clojure_namespace_mapping(
    jvm: JvmSubsystem,
    artifact_packages: AvailableClojureArtifactPackages,
) -> ClojureNamespaceMapping:
    """Build Clojure namespace mapping using trie-based pattern matching.

    This rule combines multiple sources to build a unified namespace-to-address mapping
    using a trie (prefix tree) structure for efficient pattern matching.

    Resolution order (highest to lowest precedence):
    1. Manual `packages` field on jvm_artifact targets
    2. Automatic JAR analysis from lockfiles
    3. Legacy metadata files (*_clojure_namespaces.json)

    The trie structure supports:
    - Exact namespace matches (e.g., "clojure.data.json")
    - Recursive wildcard patterns (e.g., "ring.**" matches ring and all sub-namespaces)

    Returns:
        ClojureNamespaceMapping with trie-based namespace->address mappings per resolve.
    """
    # Get all resolve names
    resolve_names = list(jvm.resolves.keys())

    if not resolve_names:
        return ClojureNamespaceMapping(mapping_per_resolve=FrozenDict())

    # Build third-party mappings for all resolves in parallel
    third_party_mappings = await concurrently(
        build_third_party_clojure_namespace_mapping(
            ThirdPartyClojureNamespaceMappingRequest(resolve_name),
            **implicitly(),
        )
        for resolve_name in resolve_names
    )

    # Also load legacy metadata files for backwards compatibility
    legacy_mapping = await _load_legacy_metadata_files()

    # Build a trie per resolve
    tries: dict[str, MutableTrieNode] = {r: MutableTrieNode() for r in resolve_names}

    # Insert entries from all sources with proper precedence
    # We insert in reverse precedence order, so higher precedence sources
    # can override lower precedence ones when the trie is queried

    # 3. Legacy metadata files (lowest precedence)
    for (namespace, resolve), addresses in legacy_mapping.items():
        if resolve in tries:
            tries[resolve].insert(
                namespace,
                addresses,
                first_party=False,
                recursive=False,
                namespace=DEFAULT_SYMBOL_NAMESPACE,
            )

    # 2. Automatic JAR analysis
    for resolve_name, third_party_mapping in zip(resolve_names, third_party_mappings):
        for namespace, addresses in third_party_mapping.mapping.items():
            tries[resolve_name].insert(
                namespace,
                addresses,
                first_party=False,
                recursive=False,
                namespace=DEFAULT_SYMBOL_NAMESPACE,
            )

    # 1. Manual packages field (highest precedence)
    for (resolve, pattern), addresses in artifact_packages.mapping.items():
        if resolve not in tries:
            continue
        symbol, recursive = _parse_namespace_pattern(pattern)
        tries[resolve].insert(
            symbol,
            addresses,
            first_party=False,
            recursive=recursive,
            namespace=DEFAULT_SYMBOL_NAMESPACE,
        )

    # Freeze all tries
    frozen_tries = FrozenDict({
        resolve: trie.frozen()
        for resolve, trie in tries.items()
    })

    return ClojureNamespaceMapping(mapping_per_resolve=frozen_tries)


async def _load_legacy_metadata_files() -> dict[tuple[str, str], tuple[Address, ...]]:
    """Load legacy *_clojure_namespaces.json metadata files.

    This provides backwards compatibility for users who have generated
    metadata files using the generate-clojure-lockfile-metadata goal.

    Returns:
        Mapping from (namespace, resolve) to tuple of addresses.
    """
    # Find all Clojure namespace metadata files
    try:
        metadata_files_digest = await path_globs_to_digest(
            PathGlobs(["**/*_clojure_namespaces.json"]),
        )
        metadata_contents = await get_digest_contents(metadata_files_digest)
    except Exception:
        return {}

    if not metadata_contents:
        return {}

    # Build mapping from metadata files
    mapping: dict[tuple[str, str], list[Address]] = {}

    for file_content in metadata_contents:
        try:
            metadata = _parse_metadata_file(file_content)

            for coord, artifact_meta in metadata.artifacts.items():
                address = Address.parse(artifact_meta.address)

                for namespace in artifact_meta.namespaces:
                    key = (namespace, metadata.resolve)
                    if key not in mapping:
                        mapping[key] = []
                    if address not in mapping[key]:
                        mapping[key].append(address)

        except Exception as e:
            logger.warning(
                f"Failed to parse Clojure namespace metadata file {file_content.path}: {e}"
            )

    return {key: tuple(addrs) for key, addrs in mapping.items()}


# ============================================================================
# Legacy metadata file support
# ============================================================================


@dataclass(frozen=True)
class ClojureNamespaceMetadataFile:
    """Request to load a Clojure namespace metadata file.

    Attributes:
        path: Path to the metadata JSON file (e.g., "3rdparty/jvm/default_clojure_namespaces.json").
    """
    path: str


@dataclass(frozen=True)
class ClojureNamespaceMetadata:
    """Parsed contents of a Clojure namespace metadata file.

    Attributes:
        resolve: The JVM resolve name this metadata is for.
        lockfile_hash: SHA256 hash of the lockfile when metadata was generated.
        artifacts: Map of Maven coordinate to artifact metadata.
    """
    resolve: str
    lockfile_hash: str
    artifacts: dict[str, ArtifactNamespaceMetadata]


@dataclass(frozen=True)
class ArtifactNamespaceMetadata:
    """Metadata about namespaces provided by a single artifact.

    Attributes:
        address: Pants address of the jvm_artifact target.
        namespaces: Clojure namespaces provided by this artifact.
        source: How the namespaces were determined ("jar-analysis", "manual", "heuristic").
    """
    address: str
    namespaces: tuple[str, ...]
    source: str = "jar-analysis"


def _parse_metadata_file(file_content: FileContent) -> ClojureNamespaceMetadata:
    """Parse a Clojure namespace metadata JSON file.

    Args:
        file_content: The metadata file content.

    Returns:
        Parsed ClojureNamespaceMetadata.

    Raises:
        ValueError: If the file is malformed or has invalid structure.
    """
    data = json.loads(file_content.content.decode('utf-8'))

    # Validate required fields
    if 'resolve' not in data:
        raise ValueError("Metadata file missing 'resolve' field")
    if 'artifacts' not in data:
        raise ValueError("Metadata file missing 'artifacts' field")

    # Parse artifact metadata
    artifacts = {}
    for coord, artifact_data in data['artifacts'].items():
        if 'address' not in artifact_data:
            raise ValueError(f"Artifact {coord} missing 'address' field")
        if 'namespaces' not in artifact_data:
            raise ValueError(f"Artifact {coord} missing 'namespaces' field")

        artifacts[coord] = ArtifactNamespaceMetadata(
            address=artifact_data['address'],
            namespaces=tuple(artifact_data['namespaces']),
            source=artifact_data.get('source', 'jar-analysis'),
        )

    return ClojureNamespaceMetadata(
        resolve=data['resolve'],
        lockfile_hash=data.get('lockfile_hash', ''),
        artifacts=artifacts,
    )


def create_metadata_file_content(
    resolve: str,
    lockfile_path: str,
    lockfile_digest: Digest,
    artifact_namespaces: dict[str, tuple[str, tuple[str, ...]]],
) -> FileContent:
    """Create a Clojure namespace metadata file.

    Args:
        resolve: The JVM resolve name.
        lockfile_path: Path to the lockfile this metadata is for.
        lockfile_digest: Digest of the lockfile (for staleness detection).
        artifact_namespaces: Map of Maven coordinate to (address, namespaces).

    Returns:
        FileContent for the metadata JSON file.
    """
    # Compute lockfile hash
    lockfile_hash = f"sha256:{lockfile_digest.fingerprint}"

    # Build artifacts metadata
    artifacts = {}
    for coord, (address, namespaces) in artifact_namespaces.items():
        artifacts[coord] = {
            "address": address,
            "namespaces": list(namespaces),
            "source": "jar-analysis",
        }

    # Build metadata structure
    metadata = {
        "version": "1.0",
        "resolve": resolve,
        "lockfile": lockfile_path,
        "lockfile_hash": lockfile_hash,
        "artifacts": artifacts,
    }

    # Serialize to JSON with nice formatting
    content = json.dumps(metadata, indent=2, sort_keys=True)

    # Determine output path: lockfile.lock -> lockfile_clojure_namespaces.json
    lockfile_name = Path(lockfile_path).stem  # Remove .lock extension
    output_path = f"{Path(lockfile_path).parent}/{lockfile_name}_clojure_namespaces.json"

    return FileContent(
        path=output_path,
        content=content.encode('utf-8'),
    )


def rules():
    return collect_rules()
