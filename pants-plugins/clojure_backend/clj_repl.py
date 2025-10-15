from __future__ import annotations

from collections.abc import Iterable

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


def _prepare_repl_for_workspace(
    argv: Iterable[str], env: dict[str, str], jdk: JdkEnvironment
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Prepare argv and env for run_in_workspace=True by prefixing paths with {chroot}/.

    When run_in_workspace=True, the REPL runs in the user's workspace directory,
    but JDK files are materialized via immutable_input_digests in a sandbox.
    Prefixing JDK and coursier paths with {chroot}/ tells the engine to substitute
    the sandbox path.

    This follows the pattern from pants.jvm.run._post_process_jvm_process.
    """
    def prefixed(arg: str, prefixes: Iterable[str]) -> str:
        # Check if any component of a colon-separated path starts with a prefix
        # (e.g., classpath entries in -cp argument)
        if ":" in arg:
            parts = arg.split(":")
            prefixed_parts = []
            for part in parts:
                # "." refers to workspace directory, don't prefix it
                if part == ".":
                    prefixed_parts.append(part)
                # Prefix JDK/coursier paths and JAR files from digest
                elif any(part.startswith(prefix) for prefix in prefixes) or part.endswith(".jar"):
                    prefixed_parts.append(f"{{chroot}}/{part}")
                else:
                    prefixed_parts.append(part)
            return ":".join(prefixed_parts)
        elif any(arg.startswith(prefix) for prefix in prefixes):
            return f"{{chroot}}/{arg}"
        else:
            return arg

    # Prefix JDK paths in argv
    jdk_prefixes = (jdk.bin_dir, jdk.jdk_preparation_script, jdk.java_home)
    prefixed_argv = tuple(prefixed(arg, jdk_prefixes) for arg in argv)

    # Prefix coursier cache paths in environment variables
    prefixed_env = {
        **env,
        "PANTS_INTERNAL_ABSOLUTE_PREFIX": "{chroot}/",
    }
    for key in list(prefixed_env.keys()):
        if key.startswith("COURSIER"):
            prefixed_env[key] = prefixed(prefixed_env[key], (jdk.coursier.cache_dir,))

    return prefixed_argv, prefixed_env


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


class RebelSubsystem(Subsystem):
    """Configuration for Rebel Readline REPL."""

    options_scope = "rebel-repl"
    name = "Rebel Readline"
    help = "Rebel Readline REPL configuration for enhanced Clojure REPL experience."

    version = StrOption(
        default="0.1.4",
        help="Rebel Readline version to use.",
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

    # Get JDK environment
    jdk = await Get(JdkEnvironment, JdkRequest, jdk_request)

    # For run_in_workspace=True, don't include source files in digest - they'll be
    # loaded from the workspace. Only include classpath JARs.
    input_digest = await Get(
        Digest,
        MergeDigests(classpath.digests()),
    )

    # Build command for clojure.main REPL
    # "." in classpath refers to workspace directory when run_in_workspace=True
    classpath_entries = [".", *classpath.args()]
    argv = [
        *jdk.args(bash, classpath_entries),
        "clojure.main",
        "--repl",
    ]

    # Prepare for run_in_workspace=True by prefixing JDK/coursier paths with {chroot}/
    argv, extra_env = _prepare_repl_for_workspace(argv, jdk.env, jdk)

    return ReplRequest(
        digest=input_digest,
        args=argv,
        extra_env=extra_env,
        immutable_input_digests=jdk.immutable_input_digests,
        append_only_caches=jdk.append_only_caches,
        # run_in_workspace=True allows the REPL to see live file changes in the workspace.
        # Source files are loaded from workspace via "." in classpath.
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

    # Get JDK environment and nREPL classpath in parallel
    jdk, nrepl_classpath = await MultiGet(
        Get(JdkEnvironment, JdkRequest, jdk_request),
        Get(
            ToolClasspath,
            ToolClasspathRequest(
                artifact_requirements=ArtifactRequirements([nrepl_artifact]),
            ),
        ),
    )

    # For run_in_workspace=True, don't include source files in digest - they'll be
    # loaded from the workspace. Only include classpath JARs and nREPL.
    input_digest = await Get(
        Digest,
        MergeDigests([
            *classpath.digests(),
            nrepl_classpath.digest,
        ]),
    )

    # Build nREPL server startup command
    port = nrepl_subsystem.port
    host = nrepl_subsystem.host

    # "." in classpath refers to workspace directory when run_in_workspace=True
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

    # Prepare for run_in_workspace=True by prefixing JDK/coursier paths with {chroot}/
    argv, extra_env = _prepare_repl_for_workspace(argv, jdk.env, jdk)

    return ReplRequest(
        digest=input_digest,
        args=argv,
        extra_env=extra_env,
        immutable_input_digests=jdk.immutable_input_digests,
        append_only_caches=jdk.append_only_caches,
        # run_in_workspace=True allows the REPL to see live file changes in the workspace.
        # Source files are loaded from workspace via "." in classpath.
        run_in_workspace=True,
    )


class ClojureRebelRepl(ReplImplementation):
    """Rebel Readline enhanced REPL."""

    name = "rebel"
    supports_args = True


@rule(desc="Create Rebel Readline REPL", level=LogLevel.DEBUG)
async def create_rebel_repl_request(
    repl: ClojureRebelRepl, bash: BashBinary, rebel_subsystem: RebelSubsystem
) -> ReplRequest:
    """Create ReplRequest for Rebel Readline REPL."""

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

    # Get Rebel Readline artifact requirement
    rebel_artifact = ArtifactRequirement(
        coordinate=Coordinate(
            group="com.bhauman",
            artifact="rebel-readline",
            version=rebel_subsystem.version,
        )
    )

    # Get JDK environment and Rebel classpath in parallel
    jdk, rebel_classpath = await MultiGet(
        Get(JdkEnvironment, JdkRequest, jdk_request),
        Get(
            ToolClasspath,
            ToolClasspathRequest(
                artifact_requirements=ArtifactRequirements([rebel_artifact]),
            ),
        ),
    )

    # For run_in_workspace=True, don't include source files in digest - they'll be
    # loaded from the workspace. Only include classpath JARs and Rebel Readline.
    input_digest = await Get(
        Digest,
        MergeDigests([
            *classpath.digests(),
            rebel_classpath.digest,
        ]),
    )

    # Build Rebel Readline REPL startup command
    # "." in classpath refers to workspace directory when run_in_workspace=True
    classpath_entries = [
        ".",
        *classpath.args(),
        *rebel_classpath.classpath_entries(),
    ]

    # Rebel Readline uses its own main class
    argv = [
        *jdk.args(bash, classpath_entries),
        "rebel-readline.main",
    ]

    # Prepare for run_in_workspace=True by prefixing JDK/coursier paths with {chroot}/
    argv, extra_env = _prepare_repl_for_workspace(argv, jdk.env, jdk)

    return ReplRequest(
        digest=input_digest,
        args=argv,
        extra_env=extra_env,
        immutable_input_digests=jdk.immutable_input_digests,
        append_only_caches=jdk.append_only_caches,
        # run_in_workspace=True allows the REPL to see live file changes in the workspace.
        # Source files are loaded from workspace via "." in classpath.
        run_in_workspace=True,
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(ReplImplementation, ClojureRepl),
        UnionRule(ReplImplementation, ClojureNRepl),
        UnionRule(ReplImplementation, ClojureRebelRepl),
    ]
