from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass

from pants.core.goals.package import (
    BuiltPackage,
    BuiltPackageArtifact,
    OutputPathField,
    PackageFieldSet,
)
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.core.util_rules.stripped_source_files import StrippedSourceFiles
from pants.engine.addresses import Addresses
from pants.engine.fs import (
    CreateDigest,
    Digest,
    DigestContents,
    EMPTY_DIGEST,
    FileContent,
    MergeDigests,
)
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.rules import collect_rules, rule
from pants.engine.target import (
    TransitiveTargets,
    TransitiveTargetsRequest,
)
from pants.engine.unions import UnionRule
from pants.jvm.classpath import Classpath
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.target_types import JvmJdkField, JvmResolveField
from pants.util.logging import LogLevel

from clojure_backend.namespace_analysis import (
    ClojureNamespaceAnalysis,
    ClojureNamespaceAnalysisRequest,
)
from clojure_backend.provided_dependencies import (
    ProvidedDependencies,
    ResolveProvidedDependenciesRequest,
)
from clojure_backend.target_types import (
    ClojureMainNamespaceField,
    ClojureProvidedDependenciesField,
    ClojureSourceField,
    ClojureTestSourceField,
)
from clojure_backend.tools_build_uberjar import (
    ToolsBuildUberjarRequest,
    ToolsBuildUberjarResult,
)

logger = logging.getLogger(__name__)


def extract_main_class(main_namespace: str, source_content: str) -> str:
    """Extract the main class name from a Clojure source file.

    If the namespace has (:gen-class :name com.example.MyClass), returns "com.example.MyClass".
    Otherwise, returns the munged namespace name (hyphens -> underscores).

    Args:
        main_namespace: The namespace name (e.g., "my-app.core")
        source_content: The source file content

    Returns:
        The main class name for the manifest
    """
    # Look for :name in gen-class declaration
    # Match patterns like:
    #   (:gen-class :name com.example.MyClass)
    #   (:gen-class :init init :name com.example.MyClass :methods [...])
    gen_class_name_match = re.search(
        r'\(:gen-class[^)]*:name\s+([\w.-]+)',
        source_content,
        re.DOTALL,
    )

    if gen_class_name_match:
        return gen_class_name_match.group(1)
    else:
        # Default: munge namespace name (hyphens -> underscores)
        return main_namespace.replace("-", "_")


@dataclass(frozen=True)
class ClojureDeployJarFieldSet(PackageFieldSet):
    """FieldSet for packaging a clojure_deploy_jar target."""

    required_fields = (
        ClojureMainNamespaceField,
        JvmResolveField,
    )

    main: ClojureMainNamespaceField
    provided: ClojureProvidedDependenciesField
    jdk: JvmJdkField
    resolve: JvmResolveField
    output_path: OutputPathField


@rule(desc="Package Clojure deploy jar", level=LogLevel.DEBUG)
async def package_clojure_deploy_jar(
    field_set: ClojureDeployJarFieldSet,
    jvm: JvmSubsystem,
) -> BuiltPackage:
    """Package a Clojure application into an executable JAR.

    This rule handles two modes:
    1. Source-only JAR (main="clojure.main"): Packages source files without AOT compilation
    2. AOT-compiled JAR: Delegates to tools.build for AOT compilation and uberjar creation

    tools.build handles the complexity of AOT compilation correctly, including:
    - Protocol classes
    - Macro-generated classes
    - Transitive namespace compilation
    """
    main_namespace = field_set.main.value
    skip_aot = main_namespace == "clojure.main"

    # Get transitive targets to find all Clojure sources
    transitive_targets = await Get(
        TransitiveTargets,
        TransitiveTargetsRequest([field_set.address]),
    )

    # Find all Clojure source targets in dependencies
    clojure_source_targets = [
        tgt
        for tgt in transitive_targets.dependencies
        if tgt.has_field(ClojureSourceField) or tgt.has_field(ClojureTestSourceField)
    ]

    # Get provided dependencies to exclude from the JAR
    resolve_name = field_set.resolve.normalized_value(jvm)
    provided_deps = await Get(
        ProvidedDependencies,
        ResolveProvidedDependenciesRequest(field_set.provided, resolve_name),
    )

    # Determine output filename
    output_filename = field_set.output_path.value_or_default(file_ending="jar")

    # =========================================================================
    # Source-only JAR path (main="clojure.main")
    # =========================================================================
    if skip_aot:
        # Build runtime address set for JAR packaging (excludes provided)
        runtime_source_addresses = Addresses(
            tgt.address for tgt in clojure_source_targets
            if tgt.address not in provided_deps.addresses
        )

        # Get classpath (excluding provided deps via address filtering)
        runtime_classpath = await Get(Classpath, Addresses, runtime_source_addresses)

        # Get first-party source files with stripped roots
        first_party_source_fields = [
            tgt[ClojureSourceField] if tgt.has_field(ClojureSourceField)
            else tgt[ClojureTestSourceField]
            for tgt in clojure_source_targets
            if tgt.address not in provided_deps.addresses
        ]

        if first_party_source_fields:
            stripped_sources = await Get(
                StrippedSourceFiles,
                SourceFilesRequest(
                    first_party_source_fields,
                    for_sources_types=(ClojureSourceField, ClojureTestSourceField),
                ),
            )
            source_digest = stripped_sources.snapshot.digest
        else:
            source_digest = EMPTY_DIGEST

        # Build set of artifact prefixes to exclude based on coordinates
        excluded_artifact_prefixes = set()
        for group, artifact in provided_deps.coordinates:
            excluded_artifact_prefixes.add(f"{group}_{artifact}_")

        # Get dependency JAR contents
        merged_classpath = await Get(Digest, MergeDigests(runtime_classpath.digests()))
        classpath_contents = await Get(DigestContents, Digest, merged_classpath)
        source_contents = await Get(DigestContents, Digest, source_digest)

        # Create the JAR in memory
        jar_buffer = io.BytesIO()
        with zipfile.ZipFile(jar_buffer, 'w', zipfile.ZIP_DEFLATED) as jar:
            # Write manifest (source-only mode)
            manifest_content = """\
Manifest-Version: 1.0
Main-Class: clojure.main
Created-By: Pants Build System
X-Source-Only: true
"""
            jar.writestr('META-INF/MANIFEST.MF', manifest_content, compress_type=zipfile.ZIP_STORED)
            added_entries = {'META-INF/MANIFEST.MF'}

            # Add first-party source files
            for file_content in source_contents:
                arcname = file_content.path
                if arcname not in added_entries:
                    jar.writestr(arcname, file_content.content)
                    added_entries.add(arcname)

            # Extract and add contents from dependency JARs
            for file_content in classpath_contents:
                if file_content.path.endswith('.jar'):
                    # Check if this JAR is from a provided dependency
                    jar_filename = file_content.path.rsplit('/', 1)[-1]
                    if any(jar_filename.startswith(prefix) for prefix in excluded_artifact_prefixes):
                        continue

                    try:
                        jar_bytes = io.BytesIO(file_content.content)
                        with zipfile.ZipFile(jar_bytes, 'r') as dep_jar:
                            for item in dep_jar.namelist():
                                # Skip META-INF and LICENSE files
                                if item.startswith('META-INF/'):
                                    continue
                                if item.upper().startswith('LICENSE'):
                                    continue
                                if item not in added_entries:
                                    data = dep_jar.read(item)
                                    jar.writestr(item, data)
                                    added_entries.add(item)
                    except Exception:
                        pass

        # Create output
        jar_bytes_data = jar_buffer.getvalue()
        output_digest = await Get(
            Digest,
            CreateDigest([FileContent(output_filename, jar_bytes_data)]),
        )

        return BuiltPackage(
            digest=output_digest,
            artifacts=(BuiltPackageArtifact(relpath=output_filename),),
        )

    # =========================================================================
    # AOT-compiled JAR path (delegate to tools.build)
    # =========================================================================

    # Get source files for validation
    source_fields = []
    for tgt in clojure_source_targets:
        if tgt.has_field(ClojureSourceField):
            source_fields.append(tgt[ClojureSourceField])
        elif tgt.has_field(ClojureTestSourceField):
            source_fields.append(tgt[ClojureTestSourceField])

    if not source_fields:
        raise ValueError(
            f"No Clojure source files found for deploy jar at {field_set.address}.\n\n"
            f"Ensure the target has dependencies on clojure_source targets."
        )

    source_files = await Get(
        SourceFiles,
        SourceFilesRequest(source_fields),
    )

    # Analyze source files to validate main namespace has (:gen-class)
    namespace_analysis = await Get(
        ClojureNamespaceAnalysis,
        ClojureNamespaceAnalysisRequest(source_files.snapshot),
    )
    digest_contents = await Get(DigestContents, Digest, source_files.snapshot.digest)

    # Validate main namespace has (:gen-class)
    # Build reverse mapping: namespace -> file path
    namespace_to_file = {ns: path for path, ns in namespace_analysis.namespaces.items()}
    main_source_path = namespace_to_file.get(main_namespace)
    main_source_file = None

    if main_source_path:
        for file_content in digest_contents:
            if file_content.path == main_source_path:
                main_source_file = file_content.content.decode("utf-8")
                break

    if not main_source_file:
        raise ValueError(
            f"Could not find source file for main namespace '{main_namespace}'.\n\n"
            f"Common causes:\n"
            f"  - Main namespace is not in the dependencies of this target\n"
            f"  - Namespace name doesn't match the file path\n"
            f"  - Missing (ns {main_namespace}) declaration in source file\n\n"
            f"Troubleshooting:\n"
            f"  1. Verify dependencies: pants dependencies {field_set.address}\n"
            f"  2. Check file contains (ns {main_namespace}) declaration\n"
            f"  3. Ensure the namespace follows Clojure naming conventions\n"
        )

    # Check for (:gen-class) in the namespace declaration
    ns_with_gen_class = re.search(
        r'\(ns\s+[\w.-]+.*?\(:gen-class',
        main_source_file,
        re.DOTALL,
    )

    if not ns_with_gen_class:
        raise ValueError(
            f"Main namespace '{main_namespace}' must include (:gen-class) in its ns declaration "
            f"to be used as an entry point for an executable JAR.\n\n"
            f"Example:\n"
            f"(ns {main_namespace}\n"
            f"  (:gen-class))\n\n"
            f"(defn -main [& args]\n"
            f"  (println \"Hello, World!\"))"
        )

    # Build address sets for classpaths
    all_source_addresses = Addresses(tgt.address for tgt in clojure_source_targets)
    runtime_source_addresses = Addresses(
        addr for addr in all_source_addresses
        if addr not in provided_deps.addresses
    )

    # Get both classpaths:
    # - compile_classpath: ALL deps including provided (for AOT compilation)
    # - runtime_classpath: Only runtime deps excluding provided (for packaging)
    compile_classpath, runtime_classpath = await MultiGet(
        Get(Classpath, Addresses, all_source_addresses),
        Get(Classpath, Addresses, runtime_source_addresses),
    )

    # Get stripped source files for RUNTIME first-party code (excluding provided)
    runtime_source_fields = [
        tgt[ClojureSourceField] if tgt.has_field(ClojureSourceField)
        else tgt[ClojureTestSourceField]
        for tgt in clojure_source_targets
        if tgt.address not in provided_deps.addresses
    ]

    # Get stripped source files for PROVIDED first-party code
    # These are needed for compilation but should not be packaged
    provided_source_fields = [
        tgt[ClojureSourceField] if tgt.has_field(ClojureSourceField)
        else tgt[ClojureTestSourceField]
        for tgt in clojure_source_targets
        if tgt.address in provided_deps.addresses
    ]

    # Get both sets of stripped sources
    if runtime_source_fields:
        stripped_runtime_sources = await Get(
            StrippedSourceFiles,
            SourceFilesRequest(
                runtime_source_fields,
                for_sources_types=(ClojureSourceField, ClojureTestSourceField),
            ),
        )
        runtime_source_digest = stripped_runtime_sources.snapshot.digest
    else:
        runtime_source_digest = EMPTY_DIGEST

    provided_namespaces: tuple[str, ...] = ()
    if provided_source_fields:
        # Get stripped sources for provided deps
        stripped_provided_sources = await Get(
            StrippedSourceFiles,
            SourceFilesRequest(
                provided_source_fields,
                for_sources_types=(ClojureSourceField, ClojureTestSourceField),
            ),
        )
        provided_source_digest = stripped_provided_sources.snapshot.digest

        # Analyze provided sources to get their namespace names (for exclusion patterns)
        provided_source_files = await Get(
            SourceFiles,
            SourceFilesRequest(provided_source_fields),
        )
        provided_ns_analysis = await Get(
            ClojureNamespaceAnalysis,
            ClojureNamespaceAnalysisRequest(provided_source_files.snapshot),
        )
        provided_namespaces = tuple(provided_ns_analysis.namespaces.values())
    else:
        provided_source_digest = EMPTY_DIGEST

    # Extract main class (handles :gen-class :name if present)
    main_class = extract_main_class(main_namespace, main_source_file)

    # Compute JAR prefixes for provided third-party dependencies
    # Format: "groupId_artifactId_" (matches Coursier JAR naming)
    provided_jar_prefixes = tuple(
        f"{group}_{artifact}_"
        for group, artifact in provided_deps.coordinates
    )

    # Build uberjar with tools.build
    result = await Get(
        ToolsBuildUberjarResult,
        ToolsBuildUberjarRequest(
            main_namespace=main_namespace,
            main_class=main_class,
            compile_classpath=compile_classpath,
            runtime_classpath=runtime_classpath,
            source_digest=runtime_source_digest,
            provided_source_digest=provided_source_digest,
            provided_namespaces=provided_namespaces,
            provided_jar_prefixes=provided_jar_prefixes,
            jdk=field_set.jdk,
        ),
    )

    # Rename output JAR to desired filename
    if result.jar_path == output_filename:
        # No rename needed
        final_digest = result.digest
    else:
        # Read the JAR contents and write with new name
        jar_contents = await Get(DigestContents, Digest, result.digest)
        if jar_contents:
            final_digest = await Get(
                Digest,
                CreateDigest([FileContent(output_filename, jar_contents[0].content)]),
            )
        else:
            raise Exception("tools.build produced no output")

    return BuiltPackage(
        digest=final_digest,
        artifacts=(BuiltPackageArtifact(relpath=output_filename),),
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, ClojureDeployJarFieldSet),
    ]
