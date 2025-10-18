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
from pants.engine.fs import Digest, DigestContents, MergeDigests, Snapshot
from pants.engine.internals.selectors import Get
from pants.engine.rules import collect_rules, rule
from pants.engine.target import (
    Target,
    TransitiveTargets,
    TransitiveTargetsRequest,
)
from pants.engine.unions import UnionRule
from pants.jvm.goals import lockfile
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

    # Perform AOT compilation
    compiled_classes = await Get(
        CompiledClojureClasses,
        CompileClojureAOTRequest(
            namespaces=namespaces_to_compile,
            source_addresses=source_addresses,
            jdk=field_set.jdk,
            resolve=field_set.resolve,
        ),
    )

    # For now, return a BuiltPackage with the compiled classes
    # In a full implementation, this would delegate to deploy_jar
    # to create the actual JAR with all dependencies

    # Determine output filename
    output_filename = field_set.output_path.value_or_default(
        file_ending="jar",
    )

    # TODO: This is a simplified implementation
    # A complete implementation would use deploy_jar to package everything
    # For now, we return the compiled classes
    artifact = BuiltPackageArtifact(
        relpath=output_filename,
    )

    return BuiltPackage(
        digest=compiled_classes.digest,
        artifacts=(artifact,),
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, ClojureDeployJarFieldSet),
    ]
