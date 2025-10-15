from __future__ import annotations

from clojure_backend.target_types import ClojureSourceField, ClojureTestSourceField
from pants.core.goals.repl import ReplImplementation, ReplRequest
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.core.util_rules.system_binaries import BashBinary
from pants.engine.addresses import Addresses
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import SourcesField, TransitiveTargets, TransitiveTargetsRequest
from pants.engine.unions import UnionRule
from pants.jvm.classpath import classpath as classpath_get
from pants.jvm.jdk_rules import JdkEnvironment, JdkRequest
from pants.jvm.resolve.common import ArtifactRequirement, ArtifactRequirements, Coordinate
from pants.jvm.resolve.coursier_fetch import ToolClasspath, ToolClasspathRequest
from pants.jvm.target_types import JvmJdkField
from pants.option.option_types import IntOption, StrOption
from pants.option.subsystem import Subsystem
from pants.util.logging import LogLevel


class NReplSubsystem(Subsystem):
    """Configuration for nREPL server."""

    options_scope = "nrepl"
    name = "nREPL"
    help = "nREPL server configuration for Clojure REPL."

    version = StrOption(
        default="1.4.0",
        help="nREPL version to use.",
    )

    port = IntOption(
        default=7888,
        help="Port for nREPL server to bind to.",
    )

    host = StrOption(
        default="127.0.0.1",
        help="Host for nREPL server to bind to.",
    )


class ClojureRepl(ReplImplementation):
    """Standard clojure.main REPL."""

    name = "clojure"
    supports_args = True


@rule(desc="Create Clojure REPL", level=LogLevel.DEBUG)
async def create_clojure_repl_request(repl: ClojureRepl, bash: BashBinary) -> ReplRequest:
    """Create ReplRequest for standard Clojure REPL."""

    # Get classpath and transitive targets
    classpath, transitive_targets = await MultiGet(
        classpath_get(**implicitly({repl.addresses: Addresses})),
        Get(TransitiveTargets, TransitiveTargetsRequest(repl.addresses)),
    )

    # Extract JDK version from first target that has it, or use default
    jdk_request = JdkRequest.SOURCE_DEFAULT
    for tgt in transitive_targets.roots:
        if tgt.has_field(JvmJdkField):
            jdk_request = JdkRequest.from_field(tgt[JvmJdkField])
            break

    # Get JDK environment and source files in parallel
    jdk, source_files = await MultiGet(
        Get(JdkEnvironment, JdkRequest, jdk_request),
        Get(
            SourceFiles,
            SourceFilesRequest(
                (tgt.get(SourcesField) for tgt in transitive_targets.closure),
                for_sources_types=(ClojureSourceField, ClojureTestSourceField),
                enable_codegen=False,
            ),
        ),
    )

    # Merge classpath JARs with all source files
    input_digest = await Get(
        Digest,
        MergeDigests([*classpath.digests(), source_files.snapshot.digest]),
    )

    # Build command for clojure.main REPL
    classpath_entries = [".", *classpath.args()]
    argv = [
        *jdk.args(bash, classpath_entries),
        "clojure.main",
        "--repl",
    ]

    return ReplRequest(
        digest=input_digest,
        args=argv,
        extra_env=jdk.env,
        immutable_input_digests=jdk.immutable_input_digests,
        append_only_caches=jdk.append_only_caches,
        run_in_workspace=True,
    )


class ClojureNRepl(ReplImplementation):
    """nREPL server for editor integration."""

    name = "nrepl"
    supports_args = True


@rule(desc="Create nREPL server", level=LogLevel.DEBUG)
async def create_nrepl_request(
    repl: ClojureNRepl, bash: BashBinary, nrepl_subsystem: NReplSubsystem
) -> ReplRequest:
    """Create ReplRequest for nREPL server."""

    # Get classpath and transitive targets
    classpath, transitive_targets = await MultiGet(
        classpath_get(**implicitly({repl.addresses: Addresses})),
        Get(TransitiveTargets, TransitiveTargetsRequest(repl.addresses)),
    )

    # Extract JDK version from first target that has it, or use default
    jdk_request = JdkRequest.SOURCE_DEFAULT
    for tgt in transitive_targets.roots:
        if tgt.has_field(JvmJdkField):
            jdk_request = JdkRequest.from_field(tgt[JvmJdkField])
            break

    # Get nREPL artifact requirement
    nrepl_artifact = ArtifactRequirement(
        coordinate=Coordinate(
            group="nrepl",
            artifact="nrepl",
            version=nrepl_subsystem.version,
        )
    )

    # Get JDK environment, source files, and nREPL classpath in parallel
    jdk, source_files, nrepl_classpath = await MultiGet(
        Get(JdkEnvironment, JdkRequest, jdk_request),
        Get(
            SourceFiles,
            SourceFilesRequest(
                (tgt.get(SourcesField) for tgt in transitive_targets.closure),
                for_sources_types=(ClojureSourceField, ClojureTestSourceField),
                enable_codegen=False,
            ),
        ),
        Get(
            ToolClasspath,
            ToolClasspathRequest(
                artifact_requirements=ArtifactRequirements([nrepl_artifact]),
            ),
        ),
    )

    # Merge all digests: project classpath, source files, and nREPL classpath
    input_digest = await Get(
        Digest,
        MergeDigests([
            *classpath.digests(),
            source_files.snapshot.digest,
            nrepl_classpath.digest,
        ]),
    )

    # Build nREPL server startup command
    port = nrepl_subsystem.port
    host = nrepl_subsystem.host

    classpath_entries = [
        ".",
        *classpath.args(),
        *nrepl_classpath.classpath_entries(),
    ]

    # Command to start nREPL server
    nrepl_start_code = (
        f'(require (quote nrepl.server)) '
        f'(nrepl.server/start-server :bind "{host}" :port {port})'
    )

    argv = [
        *jdk.args(bash, classpath_entries),
        "clojure.main",
        "-e",
        nrepl_start_code,
    ]

    return ReplRequest(
        digest=input_digest,
        args=argv,
        extra_env=jdk.env,
        immutable_input_digests=jdk.immutable_input_digests,
        append_only_caches=jdk.append_only_caches,
        run_in_workspace=True,
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(ReplImplementation, ClojureRepl),
        UnionRule(ReplImplementation, ClojureNRepl),
    ]
