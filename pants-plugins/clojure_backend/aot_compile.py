from __future__ import annotations

from dataclasses import dataclass

from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.core.util_rules.stripped_source_files import StrippedSourceFiles
from pants.engine.addresses import Addresses
from pants.engine.fs import CreateDigest, Digest, FileContent, MergeDigests
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import Targets
from pants.jvm.classpath import classpath as classpath_get
from pants.jvm.compile import ClasspathEntry
from pants.jvm.jdk_rules import JdkEnvironment, JdkRequest, JvmProcess
from pants.jvm.target_types import JvmJdkField, JvmResolveField
from pants.util.logging import LogLevel

from clojure_backend.target_types import ClojureSourceField, ClojureTestSourceField


@dataclass(frozen=True)
class CompileClojureAOTRequest:
    """Request to AOT compile Clojure namespaces to .class files.

    This is used for creating uberjars where the compiled bytecode is needed
    instead of source files.
    """

    namespaces: tuple[str, ...]  # Namespaces to compile (e.g., "my.app.core")
    source_addresses: Addresses  # Addresses of source targets containing the namespaces
    jdk: JvmJdkField | None = None
    resolve: JvmResolveField | None = None


@dataclass(frozen=True)
class CompiledClojureClasses:
    """Result of AOT compilation containing .class files.

    This can be used as a classpath entry for packaging into a JAR.
    """

    digest: Digest  # Contains .class files in proper package structure
    classpath_entry: ClasspathEntry  # For passing to deploy_jar


@rule(desc="AOT compile Clojure namespaces", level=LogLevel.DEBUG)
async def aot_compile_clojure(
    request: CompileClojureAOTRequest,
) -> CompiledClojureClasses:
    """AOT compile Clojure namespaces to JVM .class files.

    Process:
    1. Get classpath for dependencies (needed during compilation)
    2. Get source files for the namespaces
    3. Create a compile script that sets *compile-path* and calls (compile 'ns)
    4. Execute using clojure.main
    5. Capture the generated .class files
    6. Return as a ClasspathEntry for deploy_jar
    """
    # Get JDK environment
    jdk_request = JdkRequest.from_field(request.jdk) if request.jdk else JdkRequest.SOURCE_DEFAULT

    # Get targets and their source files
    # Note: We rely on the user's classpath containing Clojure. This avoids scheduler
    # conflicts when a clojure_source depends directly on jvm_artifact(clojure).
    jdk, classpath, targets = await MultiGet(
        Get(JdkEnvironment, JdkRequest, jdk_request),
        classpath_get(**implicitly(request.source_addresses)),
        Get(Targets, Addresses, request.source_addresses),
    )

    # Extract source fields from targets
    source_fields = []
    for tgt in targets:
        if tgt.has_field(ClojureSourceField):
            source_fields.append(tgt[ClojureSourceField])
        elif tgt.has_field(ClojureTestSourceField):
            source_fields.append(tgt[ClojureTestSourceField])

    # Get source files
    source_files = await Get(
        SourceFiles,
        SourceFilesRequest(
            sources_fields=source_fields,
            for_sources_types=(ClojureSourceField, ClojureTestSourceField),
            enable_codegen=True,
        ),
    )

    # Strip source roots so files are at proper paths for Clojure's namespace resolution
    stripped_sources = await Get(StrippedSourceFiles, SourceFiles, source_files)

    # Create the output directory for compiled classes
    classes_dir = "classes"

    # Build the Clojure compilation script
    # This script:
    # 1. Creates the output directory if it doesn't exist
    # 2. Sets *compile-path* to the output directory
    # 3. Ensures the directory is on the classpath (required by Clojure)
    # 4. Compiles each namespace in sequence
    compile_statements = "\n    ".join(
        f"(compile '{namespace})" for namespace in request.namespaces
    )

    compile_script = f"""
(do
  ;; Create classes directory if it doesn't exist
  (.mkdirs (java.io.File. "{classes_dir}"))

  ;; Compile namespaces
  (binding [*compile-path* "{classes_dir}"]
    {compile_statements}))
"""

    # Create a temporary file with the compile script
    compile_script_file = FileContent("__compile_script.clj", compile_script.encode("utf-8"))
    compile_script_digest = await Get(Digest, CreateDigest([compile_script_file]))

    # Merge all inputs: sources, classpath, and compile script
    input_digest = await Get(
        Digest,
        MergeDigests([
            stripped_sources.snapshot.digest,
            *classpath.digests(),
            compile_script_digest,
        ]),
    )

    # Build classpath: current directory (for sources) + dependencies + classes output dir
    # The classes_dir must be in the classpath for Clojure's compile to work
    # Note: Clojure must be present in the user's classpath for AOT compilation to work
    classpath_entries = [
        ".",
        classes_dir,
        *classpath.args(),
    ]

    # Create the JVM process to run the compilation
    process = JvmProcess(
        jdk=jdk,
        classpath_entries=classpath_entries,
        argv=[
            "clojure.main",
            "__compile_script.clj",
        ],
        input_digest=input_digest,
        extra_env={},
        extra_jvm_options=(),
        extra_nailgun_keys=(),
        output_directories=(classes_dir,),
        output_files=(),
        description=f"AOT compile Clojure: {', '.join(request.namespaces)}",
        timeout_seconds=300,  # 5 minutes should be enough for most compilations
        level=LogLevel.DEBUG,
        cache_scope=None,  # Use default caching
        use_nailgun=False,
    )

    # Execute the compilation
    process_obj = await Get(Process, JvmProcess, process)
    process_result = await Get(FallibleProcessResult, Process, process_obj)

    if process_result.exit_code != 0:
        stdout = process_result.stdout.decode('utf-8')
        stderr = process_result.stderr.decode('utf-8')

        # Build helpful error message with troubleshooting hints
        error_message = (
            f"AOT compilation failed for namespaces {', '.join(request.namespaces)}.\n\n"
            f"Common causes:\n"
            f"  - Syntax errors in namespace code\n"
            f"  - Missing dependencies\n"
            f"  - Circular namespace dependencies\n"
            f"  - Missing (:gen-class) for main namespace\n"
            f"  - Java interop types not on classpath\n\n"
            f"Stdout:\n{stdout}\n\n"
            f"Stderr:\n{stderr}\n\n"
            f"Troubleshooting:\n"
            f"  1. Check the namespace compiles: pants check {request.source_addresses}\n"
            f"  2. Verify dependencies: pants dependencies {request.source_addresses}\n"
            f"  3. Try compiling directly with Clojure CLI if available\n"
        )
        raise Exception(error_message)

    # Extract the compiled classes from the output
    # The process captures everything in the classes_dir
    compiled_classes_digest = process_result.output_digest

    # Create a ClasspathEntry for the compiled classes
    # This will be used by deploy_jar to package the classes
    classpath_entry = ClasspathEntry(
        digest=compiled_classes_digest,
        filenames=(),  # We don't track individual class files
        dependencies=(),  # No dependencies needed for the compiled classes themselves
    )

    return CompiledClojureClasses(
        digest=compiled_classes_digest,
        classpath_entry=classpath_entry,
    )


def rules():
    return [
        *collect_rules(),
    ]
