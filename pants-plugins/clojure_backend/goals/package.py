from __future__ import annotations

import io
import os
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
from pants.engine.addresses import Addresses
from pants.engine.fs import CreateDigest, Digest, DigestContents, FileContent, MergeDigests, Snapshot
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.rules import collect_rules, rule
from pants.engine.target import (
    Target,
    TransitiveTargets,
    TransitiveTargetsRequest,
)
from pants.engine.unions import UnionRule
from pants.jvm.classpath import Classpath
from pants.jvm.goals import lockfile
from pants.jvm.jdk_rules import JdkEnvironment, JdkRequest, JvmProcess
from pants.jvm.package.deploy_jar import (
    DeployJarFieldSet,
)
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.target_types import JvmJdkField, JvmResolveField
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel

from clojure_backend.aot_compile import (
    CompileClojureAOTRequest,
    CompiledClojureClasses,
)
from clojure_backend.namespace_analysis import (
    ClojureNamespaceAnalysis,
    ClojureNamespaceAnalysisRequest,
)
from clojure_backend.provided_dependencies import (
    ProvidedDependencies,
    ResolveProvidedDependenciesRequest,
)
from clojure_backend.target_types import (
    ClojureAOTNamespacesField,
    ClojureProvidedDependenciesField,
    ClojureDeployJarTarget,
    ClojureMainNamespaceField,
    ClojureSourceField,
    ClojureTestSourceField,
)


@dataclass(frozen=True)
class ClojureDeployJarFieldSet(PackageFieldSet):
    """FieldSet for packaging a clojure_deploy_jar target."""

    required_fields = (
        ClojureMainNamespaceField,
        JvmResolveField,
    )

    main: ClojureMainNamespaceField
    aot: ClojureAOTNamespacesField
    provided: ClojureProvidedDependenciesField
    jdk: JvmJdkField
    resolve: JvmResolveField
    output_path: OutputPathField


@rule(desc="Package Clojure deploy jar", level=LogLevel.DEBUG)
async def package_clojure_deploy_jar(
    field_set: ClojureDeployJarFieldSet,
    jvm: JvmSubsystem,
) -> BuiltPackage:
    """Package a Clojure application into an executable JAR with AOT compilation.

    This rule:
    1. Determines which namespaces to AOT compile based on the `aot` field
    2. Performs AOT compilation using CompileClojureAOTRequest
    3. Validates the main namespace has (:gen-class)
    4. Delegates to deploy_jar for final packaging
    """

    main_namespace = field_set.main.value
    aot_field = field_set.aot

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

    # Get all source files for analysis
    source_fields = []
    for tgt in clojure_source_targets:
        if tgt.has_field(ClojureSourceField):
            source_fields.append(tgt[ClojureSourceField])
        elif tgt.has_field(ClojureTestSourceField):
            source_fields.append(tgt[ClojureTestSourceField])

    # Get source files and analyze namespaces using clj-kondo
    if source_fields:
        source_files = await Get(
            SourceFiles,
            SourceFilesRequest(source_fields),
        )
        # Analyze all source files in batch using clj-kondo
        namespace_analysis = await Get(
            ClojureNamespaceAnalysis,
            ClojureNamespaceAnalysisRequest(source_files.snapshot),
        )
        digest_contents = await Get(DigestContents, Digest, source_files.snapshot.digest)
    else:
        namespace_analysis = ClojureNamespaceAnalysis(
            namespaces=FrozenDict({}),
            requires=FrozenDict({}),
            imports=FrozenDict({}),
        )
        digest_contents = []

    # Determine which namespaces to compile
    namespaces_to_compile: tuple[str, ...]

    if ":all" in aot_field.value:
        # Compile all Clojure namespaces in the project
        namespaces_to_compile = tuple(sorted(set(namespace_analysis.namespaces.values())))
    elif not aot_field.value:
        # Default: compile just the main namespace (transitive)
        namespaces_to_compile = (main_namespace,)
    else:
        # Explicit list of namespaces
        namespaces_to_compile = tuple(aot_field.value)

    # Find the source file for main namespace using the analysis result
    # Build reverse mapping: namespace -> file path
    namespace_to_file = {ns: path for path, ns in namespace_analysis.namespaces.items()}
    main_source_path = namespace_to_file.get(main_namespace)
    main_source_file = None

    if main_source_path:
        # Find the file content for the main source
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
    # Use a more robust check that looks for gen-class in the ns form, not just anywhere
    # This regex looks for (ns ...) followed by gen-class before the closing paren
    # It handles multi-line ns declarations with multiple clauses
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

    # Get the main class name from the gen-class declaration
    # Look for (:gen-class :name CustomClassName)
    gen_class_name_match = re.search(
        r'\(:gen-class\s+:name\s+([a-zA-Z][\w.]*)',
        main_source_file,
        re.DOTALL,
    )

    if gen_class_name_match:
        main_class_name = gen_class_name_match.group(1)
    else:
        # Default: namespace is the class name
        main_class_name = main_namespace

    # Get JDK request from field
    jdk_request = JdkRequest.from_field(field_set.jdk)

    # Get provided dependencies to exclude from the JAR
    # Pass the resolve name so Maven transitives can be looked up in the lockfile
    resolve_name = field_set.resolve.normalized_value(jvm)
    provided_deps = await Get(
        ProvidedDependencies,
        ResolveProvidedDependenciesRequest(field_set.provided, resolve_name),
    )

    # Build full address set for AOT compilation (includes everything)
    all_source_addresses = Addresses(tgt.address for tgt in clojure_source_targets)

    # Build runtime address set for JAR packaging (excludes provided and their transitives)
    runtime_source_addresses = Addresses(
        addr for addr in all_source_addresses
        if addr not in provided_deps.addresses
    )

    # Get JDK environment, runtime classpath, and compiled classes
    # Note: AOT compilation uses ALL addresses (including provided)
    #       JAR packaging uses RUNTIME addresses (excluding provided)
    jdk_env, runtime_classpath, compiled_classes = await MultiGet(
        Get(JdkEnvironment, JdkRequest, jdk_request),
        Get(Classpath, Addresses, runtime_source_addresses),
        Get(
            CompiledClojureClasses,
            CompileClojureAOTRequest(
                namespaces=namespaces_to_compile,
                source_addresses=all_source_addresses,  # AOT needs all deps
                jdk=field_set.jdk,
                resolve=field_set.resolve,
            ),
        ),
    )

    # Determine output filename
    output_filename = field_set.output_path.value_or_default(
        file_ending="jar",
    )

    # Build set of namespaces for provided dependencies to exclude from JAR
    # Collect all source fields for provided targets
    provided_source_fields = []
    for tgt in clojure_source_targets:
        if tgt.address in provided_deps.addresses:
            if tgt.has_field(ClojureSourceField):
                provided_source_fields.append(tgt[ClojureSourceField])
            elif tgt.has_field(ClojureTestSourceField):
                provided_source_fields.append(tgt[ClojureTestSourceField])

    # Get all source files and analyze namespaces
    provided_namespaces: set[str] = set()
    if provided_source_fields:
        provided_source_files = await Get(
            SourceFiles,
            SourceFilesRequest(provided_source_fields),
        )
        if provided_source_files.files:
            # Analyze provided source files using clj-kondo
            provided_analysis = await Get(
                ClojureNamespaceAnalysis,
                ClojureNamespaceAnalysisRequest(provided_source_files.snapshot),
            )
            provided_namespaces = set(provided_analysis.namespaces.values())

    # Build set of project namespace paths for filtering AOT classes
    # These are all the namespaces from our analyzed source files (excluding provided)
    # Third-party classes from transitive AOT compilation should come from original JARs
    project_namespace_paths = set()
    for namespace in namespace_analysis.namespaces.values():
        if namespace not in provided_namespaces:
            # Convert namespace to path (e.g., "my.app.core" -> "my/app/core")
            namespace_path = namespace.replace('.', '/').replace('-', '_')
            project_namespace_paths.add(namespace_path)

    def is_project_class(arcname: str) -> bool:
        """Check if a class file belongs to a project namespace.

        Handles:
        - Direct namespace classes: my/app/core.class
        - Inner classes: my/app/core$fn__123.class
        - Method implementation: my/app/core$_main.class
        - Init classes: my/app/core__init.class
        """
        # Remove .class extension
        class_path = arcname[:-6]  # len('.class') == 6

        # Handle inner classes (split on $) and __init classes
        base_class_path = class_path.split('$')[0]
        if base_class_path.endswith('__init'):
            base_class_path = base_class_path[:-6]  # len('__init') == 6

        # Check for exact match
        if base_class_path in project_namespace_paths:
            return True

        # Check if this is a class in a subpackage of a project namespace
        # e.g., my/app/core/impl.class is under my/app/core
        for ns_path in project_namespace_paths:
            if base_class_path.startswith(ns_path + '/'):
                return True

        return False

    # Create JAR manifest with main class
    manifest_content = f"""\
Manifest-Version: 1.0
Main-Class: {main_class_name}
Created-By: Pants Build System
"""

    # Get the contents of compiled classes and dependency JARs
    # Note: Uses runtime_classpath which excludes compile-only dependencies
    all_digests = [compiled_classes.digest, *runtime_classpath.digests()]
    merged_digest = await Get(Digest, MergeDigests(all_digests))
    digest_contents = await Get(DigestContents, Digest, merged_digest)

    # Create the uberjar in memory using Python's zipfile module
    jar_buffer = io.BytesIO()
    with zipfile.ZipFile(jar_buffer, 'w', zipfile.ZIP_DEFLATED) as jar:
        # Write manifest first (uncompressed as per JAR spec)
        jar.writestr('META-INF/MANIFEST.MF', manifest_content, compress_type=zipfile.ZIP_STORED)

        # Track what we've added to avoid duplicates
        added_entries = {'META-INF/MANIFEST.MF'}

        # Build set of artifact prefixes to exclude based on coordinates
        # Pants/Coursier JAR filenames follow: {group}_{artifact}_{version}.jar pattern
        # e.g., "org.clojure_clojure_1.11.0.jar" for org.clojure:clojure:1.11.0
        excluded_artifact_prefixes = set()
        for group, artifact in provided_deps.coordinates:
            excluded_artifact_prefixes.add(f"{group}_{artifact}_")

        # Extract and add all dependency JARs (except provided ones)
        for file_content in digest_contents:
            if file_content.path.endswith('.jar'):
                # Check if this JAR should be excluded based on coordinates
                jar_filename = os.path.basename(file_content.path)
                should_exclude = False
                for prefix in excluded_artifact_prefixes:
                    if jar_filename.startswith(prefix):
                        should_exclude = True
                        break

                if should_exclude:
                    # Skip this JAR - it's a provided dependency
                    continue

                try:
                    jar_bytes = io.BytesIO(file_content.content)
                    with zipfile.ZipFile(jar_bytes, 'r') as dep_jar:
                        for item in dep_jar.namelist():
                            # Skip META-INF files from dependencies and duplicates
                            if not item.startswith('META-INF/') and item not in added_entries:
                                try:
                                    data = dep_jar.read(item)
                                    jar.writestr(item, data)
                                    added_entries.add(item)
                                except Exception:
                                    # Skip bad entries or duplicates
                                    pass
                except Exception:
                    # Skip invalid JAR files
                    pass

        # Add compiled classes (they're in the classes/ directory)
        # Only include classes from project namespaces, not transitively compiled third-party
        # This ensures third-party library classes come from their original JARs,
        # which is critical for protocol extensions to work correctly.
        for file_content in digest_contents:
            if file_content.path.startswith('classes/') and file_content.path.endswith('.class'):
                # Remove 'classes/' prefix to get the archive name
                arcname = file_content.path[8:]  # len('classes/') == 8

                # Only include classes from project namespaces
                # Third-party classes should come from their original JARs
                if not is_project_class(arcname):
                    continue

                # Only add if not already in JAR (from dependency JARs)
                if arcname not in added_entries:
                    jar.writestr(arcname, file_content.content)
                    added_entries.add(arcname)

    # Create the output digest with the JAR file
    jar_bytes = jar_buffer.getvalue()
    output_digest = await Get(
        Digest,
        CreateDigest([FileContent(output_filename, jar_bytes)]),
    )

    # Return the built JAR
    artifact = BuiltPackageArtifact(
        relpath=output_filename,
    )

    return BuiltPackage(
        digest=output_digest,
        artifacts=(artifact,),
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, ClojureDeployJarFieldSet),
    ]
