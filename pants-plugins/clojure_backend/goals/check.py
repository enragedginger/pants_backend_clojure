"""Check goal for Clojure sources."""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.check import CheckRequest, CheckResult, CheckResults
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.core.util_rules.stripped_source_files import StrippedSourceFiles
from pants.engine.addresses import Addresses
from pants.engine.fs import CreateDigest, Digest, DigestContents, FileContent, MergeDigests
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.process import FallibleProcessResult, Process, ProcessResult
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import FieldSet
from pants.engine.unions import UnionRule
from pants.jvm.classpath import Classpath, classpath
from pants.jvm.jdk_rules import JdkEnvironment, JdkRequest, JvmProcess
from pants.jvm.resolve.common import ArtifactRequirement, ArtifactRequirements, Coordinate
from pants.jvm.resolve.coursier_fetch import ToolClasspath, ToolClasspathRequest
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.target_types import JvmJdkField, JvmResolveField
from pants.util.logging import LogLevel

from clojure_backend.config import DEFAULT_CLOJURE_VERSION
from clojure_backend.subsystems.clojure_check import ClojureCheckSubsystem
from clojure_backend.target_types import ClojureSourceField
from clojure_backend.utils.namespace_parser import parse_namespace


@dataclass(frozen=True)
class ClojureCheckFieldSet(FieldSet):
    """FieldSet for checking Clojure source files."""

    required_fields = (ClojureSourceField,)

    sources: ClojureSourceField
    resolve: JvmResolveField
    jdk_version: JvmJdkField


class ClojureCheckRequest(CheckRequest):
    field_set_type = ClojureCheckFieldSet
    tool_name = "Clojure check"




def _create_loader_script(namespaces: list[str], config: ClojureCheckSubsystem) -> str:
    """Generate a Clojure script that loads all namespaces and reports errors."""

    ns_symbols = " ".join(f"'{ns}" for ns in namespaces)
    ns_count = len(namespaces)

    # Note: Using actual checkmarks and X symbols for output
    return f'''(require 'clojure.main)

(def failed (atom false))
(def error-messages (atom []))

(defn check-namespace [ns-sym]
  (try
    (require ns-sym)
    (println (str "✓ Loaded: " ns-sym))
    (catch Exception e
      (reset! failed true)
      (let [msg (str "✗ Failed to load " ns-sym ": " (.getMessage e))]
        (swap! error-messages conj msg)
        (println msg)
        (when-let [cause (.getCause e)]
          (println "  Caused by:" (.getMessage cause)))))))

(println "Checking Clojure compilation...")
(println "Namespaces to check: {ns_count}")
(println)

(doseq [ns-sym [{ns_symbols}]]
  (check-namespace ns-sym))

(println)
(if @failed
  (do
    (println "Check FAILED")
    (println "Errors:")
    (doseq [msg @error-messages]
      (println "  " msg))
    (System/exit 1))
  (do
    (println "Check PASSED - All namespaces loaded successfully")
    (System/exit 0)))
'''


@rule(desc="Check Clojure compilation", level=LogLevel.DEBUG)
async def check_clojure(
    request: ClojureCheckRequest,
    jvm: JvmSubsystem,
    clojure_check: ClojureCheckSubsystem,
) -> CheckResults:
    """Validate Clojure sources by loading all namespaces."""

    if clojure_check.skip:
        return CheckResults([], checker_name="Clojure check")

    results = []

    for field_set in request.field_sets:
        # Get JDK and classpath for this target
        jdk_request = JdkRequest.from_field(field_set.jdk_version)

        # Fetch Clojure runtime (needed for namespace loading)
        clojure_artifact = ArtifactRequirement(
            coordinate=Coordinate(
                group="org.clojure",
                artifact="clojure",
                version=DEFAULT_CLOJURE_VERSION,
            )
        )

        jdk, clspath, clojure_classpath = await MultiGet(
            Get(JdkEnvironment, JdkRequest, jdk_request),
            classpath(**implicitly(Addresses([field_set.address]))),
            Get(
                ToolClasspath,
                ToolClasspathRequest(
                    artifact_requirements=ArtifactRequirements([clojure_artifact]),
                ),
            ),
        )

        # Get source files and extract namespaces
        sources = await Get(SourceFiles, SourceFilesRequest([field_set.sources]))

        # Strip source roots so files are at proper paths for Clojure's namespace resolution
        stripped_sources = await Get(StrippedSourceFiles, SourceFiles, sources)

        # Read the file contents to extract namespaces
        digest_contents = await Get(DigestContents, Digest, sources.snapshot.digest)

        namespaces = []
        for file_content in digest_contents:
            content = file_content.content.decode('utf-8')
            namespace = parse_namespace(content)
            if namespace:
                namespaces.append(namespace)

        if not namespaces:
            # No namespaces to check, skip this target
            continue

        # Create loader script
        loader_script = _create_loader_script(namespaces, clojure_check)

        # Prepare digest with the loader script
        loader_digest = await Get(
            Digest,
            CreateDigest([FileContent("check_loader.clj", loader_script.encode())]),
        )

        # Merge loader script with sources and classpath digests
        input_digest = await Get(
            Digest,
            MergeDigests([
                loader_digest,
                stripped_sources.snapshot.digest,
                *clspath.digests(),
                clojure_classpath.digest,
            ])
        )

        # Build JVM command with additional args if provided
        extra_jvm_args = list(clojure_check.args) if clojure_check.args else []

        # Build classpath: current directory (for sources) + dependencies + Clojure runtime
        classpath_entries = [
            ".",
            *clspath.args(),
            *clojure_classpath.classpath_entries(),
        ]

        # Create JVM process to run the check
        jvm_process = JvmProcess(
            jdk=jdk,
            classpath_entries=classpath_entries,
            argv=["clojure.main", "check_loader.clj"],
            input_digest=input_digest,
            description=f"Check Clojure compilation: {field_set.address}",
            level=LogLevel.DEBUG,
            extra_jvm_options=extra_jvm_args,
        )

        result = await Get(FallibleProcessResult, Process, await Get(Process, JvmProcess, jvm_process))

        results.append(
            CheckResult(
                exit_code=result.exit_code,
                stdout=result.stdout.decode(),
                stderr=result.stderr.decode(),
                partition_description=str(field_set.address),
            )
        )

    return CheckResults(results, checker_name="Clojure check")


def rules():
    return [
        *collect_rules(),
        UnionRule(CheckRequest, ClojureCheckRequest),
    ]
