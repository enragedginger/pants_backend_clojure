from __future__ import annotations

import re
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
from pants.engine.process import Process, ProcessResult
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
from clojure_backend.dependency_inference import parse_clojure_namespace
from clojure_backend.target_types import (
    ClojureAOTNamespacesField,
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
            namespace = parse_clojure_namespace(content)
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
        namespace = parse_clojure_namespace(content)
        if namespace == main_namespace:
            main_source_file = content
            break

    if not main_source_file:
        raise ValueError(
            f"Could not find source file for main namespace '{main_namespace}'. "
            f"Make sure the namespace is defined in the dependencies."
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

    # Get source addresses for AOT compilation
    source_addresses = Addresses(tgt.address for tgt in clojure_source_targets)

    # Get JDK request from field
    jdk_request = JdkRequest.from_field(field_set.jdk)

    # Get JDK environment and classpath for dependencies
    jdk_env, classpath, compiled_classes = await MultiGet(
        Get(JdkEnvironment, JdkRequest, jdk_request),
        Get(Classpath, Addresses, source_addresses),
        Get(
            CompiledClojureClasses,
            CompileClojureAOTRequest(
                namespaces=namespaces_to_compile,
                source_addresses=source_addresses,
                jdk=field_set.jdk,
                resolve=field_set.resolve,
            ),
        ),
    )

    # Determine output filename
    output_filename = field_set.output_path.value_or_default(
        file_ending="jar",
    )

    # Create JAR manifest with main class
    manifest_content = f"""\
Manifest-Version: 1.0
Main-Class: {main_class_name}
Created-By: Pants Build System
"""

    # Create a Python script to build the uberjar
    # JARs are ZIP files, so we use Python's zipfile module
    build_jar_script = f'''\
#!/usr/bin/env python3
import zipfile
import os
import sys
from pathlib import Path

output_jar = "{output_filename}"
manifest_content = """{manifest_content}"""

# Create the JAR file (which is just a ZIP)
with zipfile.ZipFile(output_jar, 'w', zipfile.ZIP_DEFLATED) as jar:
    # Write manifest first (uncompressed as per JAR spec)
    jar.writestr('META-INF/MANIFEST.MF', manifest_content, compress_type=zipfile.ZIP_STORED)

    # Extract and add all dependency JARs
    for jar_file in Path('.').glob('*.jar'):
        try:
            with zipfile.ZipFile(jar_file, 'r') as dep_jar:
                for item in dep_jar.namelist():
                    # Skip META-INF files from dependencies to avoid conflicts
                    if not item.startswith('META-INF/'):
                        try:
                            data = dep_jar.read(item)
                            jar.writestr(item, data)
                        except Exception:
                            pass  # Skip duplicates or bad entries
        except Exception as e:
            print(f"Warning: Could not process {{jar_file}}: {{e}}", file=sys.stderr)

    # Add compiled classes
    classes_dir = Path('classes')
    if classes_dir.exists():
        for class_file in classes_dir.rglob('*.class'):
            arcname = str(class_file.relative_to(classes_dir))
            jar.write(class_file, arcname)

print(f"Created {{output_jar}}")
'''

    build_script_file = FileContent("__build_jar.py", build_jar_script.encode("utf-8"))
    build_script_digest = await Get(Digest, CreateDigest([build_script_file]))

    # Merge compiled classes and dependencies with build script
    build_input_digest = await Get(
        Digest,
        MergeDigests([
            compiled_classes.digest,
            *classpath.digests(),
            build_script_digest,
        ]),
    )

    # Execute the jar building script using Python
    process_result = await Get(
        ProcessResult,
        Process(
            argv=["/usr/bin/python3", "__build_jar.py"],
            input_digest=build_input_digest,
            output_files=(output_filename,),
            description=f"Creating Clojure uberjar: {output_filename}",
        ),
    )

    # Return the built JAR
    artifact = BuiltPackageArtifact(
        relpath=output_filename,
    )

    return BuiltPackage(
        digest=process_result.output_digest,
        artifacts=(artifact,),
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, ClojureDeployJarFieldSet),
    ]
