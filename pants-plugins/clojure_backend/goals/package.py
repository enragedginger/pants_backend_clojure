from __future__ import annotations

import io
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
from pants.engine.fs import CreateDigest, Digest, DigestContents, FileContent, MergeDigests
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
from pants.jvm.target_types import JvmJdkField, JvmResolveField
from pants.util.logging import LogLevel

from clojure_backend.aot_compile import (
    CompileClojureAOTRequest,
    CompiledClojureClasses,
)
from clojure_backend.compile_dependencies import CompileOnlyDependencies
from clojure_backend.target_types import (
    ClojureAOTNamespacesField,
    ClojureCompileDependenciesField,
    ClojureDeployJarTarget,
    ClojureMainNamespaceField,
    ClojureSourceField,
    ClojureTestSourceField,
)
from clojure_backend.utils.namespace_parser import parse_namespace


@dataclass(frozen=True)
class ClojureDeployJarFieldSet(PackageFieldSet):
    """FieldSet for packaging a clojure_deploy_jar target."""

    required_fields = (
        ClojureMainNamespaceField,
        JvmResolveField,
    )

    main: ClojureMainNamespaceField
    aot: ClojureAOTNamespacesField
    compile_dependencies: ClojureCompileDependenciesField
    jdk: JvmJdkField
    resolve: JvmResolveField
    output_path: OutputPathField


@rule(desc="Package Clojure deploy jar", level=LogLevel.DEBUG)
async def package_clojure_deploy_jar(
    field_set: ClojureDeployJarFieldSet,
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

    # Get source files and their contents
    if source_fields:
        source_files = await Get(
            SourceFiles,
            SourceFilesRequest(source_fields),
        )
        digest_contents = await Get(DigestContents, Digest, source_files.snapshot.digest)
    else:
        digest_contents = []

    # Determine which namespaces to compile
    namespaces_to_compile: tuple[str, ...]

    if ":all" in aot_field.value:
        # Compile all Clojure namespaces in the project
        namespaces = []
        for file_content in digest_contents:
            content = file_content.content.decode("utf-8")
            namespace = parse_namespace(content)
            if namespace:
                namespaces.append(namespace)
        namespaces_to_compile = tuple(sorted(set(namespaces)))
    elif not aot_field.value:
        # Default: compile just the main namespace (transitive)
        namespaces_to_compile = (main_namespace,)
    else:
        # Explicit list of namespaces
        namespaces_to_compile = tuple(aot_field.value)

    # Validate main namespace has (:gen-class) and get main class name
    main_source_file = None
    for file_content in digest_contents:
        content = file_content.content.decode("utf-8")
        namespace = parse_namespace(content)
        if namespace == main_namespace:
            main_source_file = content
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

    # Get compile-only dependencies to exclude from the JAR
    compile_only_deps = await Get(
        CompileOnlyDependencies,
        ClojureCompileDependenciesField,
        field_set.compile_dependencies,
    )

    # Build full address set for AOT compilation (includes everything)
    all_source_addresses = Addresses(tgt.address for tgt in clojure_source_targets)

    # Build runtime address set for JAR packaging (excludes compile-only and their transitives)
    runtime_source_addresses = Addresses(
        addr for addr in all_source_addresses
        if addr not in compile_only_deps.addresses
    )

    # Get JDK environment, runtime classpath, and compiled classes
    # Note: AOT compilation uses ALL addresses (including compile-only)
    #       JAR packaging uses RUNTIME addresses (excluding compile-only)
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

    # Build set of namespaces for compile-only dependencies to exclude from JAR
    # Collect all source fields for compile-only targets
    compile_only_source_fields = []
    for tgt in clojure_source_targets:
        if tgt.address in compile_only_deps.addresses:
            if tgt.has_field(ClojureSourceField):
                compile_only_source_fields.append(tgt[ClojureSourceField])
            elif tgt.has_field(ClojureTestSourceField):
                compile_only_source_fields.append(tgt[ClojureTestSourceField])

    # Get all source files in parallel using MultiGet
    compile_only_namespaces = set()
    if compile_only_source_fields:
        all_source_requests = await MultiGet(
            Get(SourceFiles, SourceFilesRequest([field]))
            for field in compile_only_source_fields
        )

        # Get all source contents in parallel
        source_digests = [req.snapshot.digest for req in all_source_requests if req.files]
        if source_digests:
            all_contents = await MultiGet(
                Get(DigestContents, Digest, digest)
                for digest in source_digests
            )

            # Parse namespaces from all source files
            for content_result in all_contents:
                for file_content in content_result:
                    content = file_content.content.decode("utf-8")
                    namespace = parse_namespace(content)
                    if namespace:
                        compile_only_namespaces.add(namespace)

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

        # Extract and add all dependency JARs
        for file_content in digest_contents:
            if file_content.path.endswith('.jar'):
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
        # Exclude classes from compile-only dependencies
        for file_content in digest_contents:
            if file_content.path.startswith('classes/') and file_content.path.endswith('.class'):
                # Remove 'classes/' prefix to get the archive name
                arcname = file_content.path[8:]  # len('classes/') == 8

                # Check if this class file belongs to a compile-only namespace
                is_compile_only = False
                for namespace in compile_only_namespaces:
                    # Convert namespace to path (e.g., "api.interface" -> "api/interface")
                    namespace_path = namespace.replace('.', '/').replace('-', '_')
                    if arcname.startswith(namespace_path):
                        is_compile_only = True
                        break

                # Only add if not from a compile-only dependency
                if not is_compile_only and arcname not in added_entries:
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
