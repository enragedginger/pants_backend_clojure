from __future__ import annotations

from collections.abc import Iterable

from clojure_backend.target_types import (
    ClojureSourceField,
    ClojureSourceTarget,
    ClojureTestSourceField,
    ClojureTestTarget,
)
from clojure_backend.utils.namespace_parser import parse_namespace
from clojure_backend.utils.source_roots import determine_source_root
from pants.core.goals.repl import ReplImplementation, ReplRequest
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.core.util_rules.system_binaries import BashBinary
from pants.engine.addresses import Address, Addresses
from pants.engine.fs import Digest, DigestContents, MergeDigests
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import AllTargets, SourcesField, TransitiveTargets, TransitiveTargetsRequest
from pants.engine.unions import UnionRule
from pants.jvm.classpath import classpath as classpath_get
from pants.jvm.jdk_rules import JdkEnvironment, JdkRequest
from pants.jvm.resolve.common import ArtifactRequirement, ArtifactRequirements, Coordinate
from pants.jvm.resolve.coursier_fetch import ToolClasspath, ToolClasspathRequest
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.target_types import JvmJdkField, JvmResolveField
from pants.option.option_types import BoolOption, IntOption, StrOption
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


class ClojureReplSubsystem(Subsystem):
    """Configuration for Clojure REPL behavior."""

    options_scope = "clojure-repl"
    name = "Clojure REPL"
    help = "Configuration for Clojure REPL behavior."

    load_resolve_sources = BoolOption(
        default=True,
        help=(
            "Load all Clojure sources in the same resolve, not just transitive dependencies.\n\n"
            "When enabled (default), the REPL includes dependencies for ALL Clojure targets "
            "in the same resolve as the target you're running the REPL for. This allows you "
            "to require any namespace in the resolve without having to add explicit dependencies.\n\n"
            "When disabled (hermetic mode), only transitive dependencies of the specified "
            "target are loaded. This is faster but requires explicit dependencies in BUILD files.\n\n"
            "Example:\n"
            "  pants repl projects/foo/src/foo.clj  # All java21 sources available\n"
            "  pants repl --no-clojure-repl-load-resolve-sources projects/foo/src/foo.clj  # Only foo's deps"
        ),
    )


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


async def _get_all_clojure_targets_in_resolve(
    all_targets: AllTargets, jvm: JvmSubsystem, resolve_name: str
) -> tuple[Address, ...]:
    """Get all Clojure source and test targets in a specific resolve.

    This is used to load all sources in a resolve, enabling you to require any
    namespace without having to add explicit dependencies.
    """
    targets_in_resolve = []

    for target in all_targets:
        # Check if target has a resolve field and is a Clojure target
        if not target.has_field(JvmResolveField):
            continue

        target_resolve = target[JvmResolveField].normalized_value(jvm)
        if target_resolve != resolve_name:
            continue

        # Include both source and test targets
        if isinstance(target, (ClojureSourceTarget, ClojureTestTarget)):
            targets_in_resolve.append(target.address)

    return tuple(targets_in_resolve)


async def _gather_source_roots(addresses: Addresses) -> set[str]:
    """Gather all source root directories for the given addresses.

    Returns a set of source root paths that should be added to the classpath.
    """

    transitive_targets = await Get(TransitiveTargets, TransitiveTargetsRequest(addresses))
    source_roots = set()

    # Gather source files for all Clojure targets
    source_files_requests = []
    clojure_targets = []

    for tgt in transitive_targets.closure:
        if isinstance(tgt, ClojureSourceTarget) and tgt.has_field(ClojureSourceField):
            source_files_requests.append(
                Get(SourceFiles, SourceFilesRequest([tgt[ClojureSourceField]]))
            )
            clojure_targets.append(tgt)
        elif isinstance(tgt, ClojureTestTarget) and tgt.has_field(ClojureTestSourceField):
            source_files_requests.append(
                Get(SourceFiles, SourceFilesRequest([tgt[ClojureTestSourceField]]))
            )
            clojure_targets.append(tgt)

    if not source_files_requests:
        return set()

    all_source_files = await MultiGet(source_files_requests)

    # Get file contents to parse namespaces
    digest_requests = [
        Get(DigestContents, Digest, sf.snapshot.digest) for sf in all_source_files
    ]
    all_digest_contents = await MultiGet(digest_requests)

    # Determine source roots from namespaces
    for i, digest_contents in enumerate(all_digest_contents):
        if not digest_contents:
            # Fallback: use target directory
            source_roots.add(clojure_targets[i].address.spec_path or ".")
            continue

        # Parse first file's namespace
        file_content = digest_contents[0].content.decode("utf-8")
        namespace = parse_namespace(file_content)

        if namespace:
            file_path = digest_contents[0].path
            source_root = determine_source_root(file_path, namespace)
            if source_root:
                source_roots.add(source_root)
        else:
            # Fallback: use target directory
            source_roots.add(clojure_targets[i].address.spec_path or ".")

    return source_roots


class ClojureRepl(ReplImplementation):
    """Standard clojure.main REPL."""

    name = "clojure"
    supports_args = True


@rule(desc="Create Clojure REPL", level=LogLevel.DEBUG)
async def create_clojure_repl_request(
    repl: ClojureRepl,
    bash: BashBinary,
    clojure_repl_subsystem: ClojureReplSubsystem,
    jvm: JvmSubsystem,
) -> ReplRequest:
    """Create ReplRequest for standard Clojure REPL."""

    # Determine addresses to load
    addresses_to_load = repl.addresses

    # If load_resolve_sources is enabled, expand to all targets in the resolve
    if clojure_repl_subsystem.load_resolve_sources and repl.addresses:
        # Get transitive targets to determine the resolve
        initial_transitive = await Get(
            TransitiveTargets, TransitiveTargetsRequest(repl.addresses)
        )

        # Find the resolve from the first root target
        resolve_name = None
        for tgt in initial_transitive.roots:
            if tgt.has_field(JvmResolveField):
                resolve_name = tgt[JvmResolveField].normalized_value(jvm)
                break

        # If we found a resolve, get all Clojure targets in that resolve
        if resolve_name:
            all_targets = await Get(AllTargets)
            resolve_addresses = await _get_all_clojure_targets_in_resolve(
                all_targets, jvm, resolve_name
            )
            # Merge with original addresses to ensure they're included
            addresses_to_load = Addresses(sorted(set(repl.addresses) | set(resolve_addresses)))

    # Get classpath, transitive targets, and source roots using the (possibly expanded) addresses
    classpath, transitive_targets, source_roots = await MultiGet(
        classpath_get(**implicitly({addresses_to_load: Addresses})),
        Get(TransitiveTargets, TransitiveTargetsRequest(addresses_to_load)),
        _gather_source_roots(addresses_to_load),
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
    # Source roots are added to classpath so Clojure can find source files
    classpath_entries = [*sorted(source_roots), *classpath.args()]
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
    repl: ClojureNRepl,
    bash: BashBinary,
    clojure_repl_subsystem: ClojureReplSubsystem,
    nrepl_subsystem: NReplSubsystem,
    jvm: JvmSubsystem,
) -> ReplRequest:
    """Create ReplRequest for nREPL server."""

    # Determine addresses to load
    addresses_to_load = repl.addresses

    # If load_resolve_sources is enabled, expand to all targets in the resolve
    if clojure_repl_subsystem.load_resolve_sources and repl.addresses:
        # Get transitive targets to determine the resolve
        initial_transitive = await Get(
            TransitiveTargets, TransitiveTargetsRequest(repl.addresses)
        )

        # Find the resolve from the first root target
        resolve_name = None
        for tgt in initial_transitive.roots:
            if tgt.has_field(JvmResolveField):
                resolve_name = tgt[JvmResolveField].normalized_value(jvm)
                break

        # If we found a resolve, get all Clojure targets in that resolve
        if resolve_name:
            all_targets = await Get(AllTargets)
            resolve_addresses = await _get_all_clojure_targets_in_resolve(
                all_targets, jvm, resolve_name
            )
            # Merge with original addresses to ensure they're included
            addresses_to_load = Addresses(sorted(set(repl.addresses) | set(resolve_addresses)))

    # Get classpath, transitive targets, and source roots using the (possibly expanded) addresses
    classpath, transitive_targets, source_roots = await MultiGet(
        classpath_get(**implicitly({addresses_to_load: Addresses})),
        Get(TransitiveTargets, TransitiveTargetsRequest(addresses_to_load)),
        _gather_source_roots(addresses_to_load),
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

    # Source roots are added to classpath so Clojure can find source files
    classpath_entries = [
        *sorted(source_roots),
        *classpath.args(),
        *nrepl_classpath.classpath_entries(),
    ]

    # Command to start nREPL server and keep it running
    # The server is stored in an atom and we use deref (@) to block indefinitely
    nrepl_start_code = (
        f'(require (quote nrepl.server)) '
        f'(let [server (nrepl.server/start-server :bind "{host}" :port {port})] '
        f'(println server) '
        f'(println "nREPL server started on port {port}") '
        f'@(promise))'  # Block forever
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
    repl: ClojureRebelRepl,
    bash: BashBinary,
    clojure_repl_subsystem: ClojureReplSubsystem,
    rebel_subsystem: RebelSubsystem,
    jvm: JvmSubsystem,
) -> ReplRequest:
    """Create ReplRequest for Rebel Readline REPL."""

    # Determine addresses to load
    addresses_to_load = repl.addresses

    # If load_resolve_sources is enabled, expand to all targets in the resolve
    if clojure_repl_subsystem.load_resolve_sources and repl.addresses:
        # Get transitive targets to determine the resolve
        initial_transitive = await Get(
            TransitiveTargets, TransitiveTargetsRequest(repl.addresses)
        )

        # Find the resolve from the first root target
        resolve_name = None
        for tgt in initial_transitive.roots:
            if tgt.has_field(JvmResolveField):
                resolve_name = tgt[JvmResolveField].normalized_value(jvm)
                break

        # If we found a resolve, get all Clojure targets in that resolve
        if resolve_name:
            all_targets = await Get(AllTargets)
            resolve_addresses = await _get_all_clojure_targets_in_resolve(
                all_targets, jvm, resolve_name
            )
            # Merge with original addresses to ensure they're included
            addresses_to_load = Addresses(sorted(set(repl.addresses) | set(resolve_addresses)))

    # Get classpath, transitive targets, and source roots using the (possibly expanded) addresses
    classpath, transitive_targets, source_roots = await MultiGet(
        classpath_get(**implicitly({addresses_to_load: Addresses})),
        Get(TransitiveTargets, TransitiveTargetsRequest(addresses_to_load)),
        _gather_source_roots(addresses_to_load),
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
    # Source roots are added to classpath so Clojure can find source files
    classpath_entries = [
        *sorted(source_roots),
        *classpath.args(),
        *rebel_classpath.classpath_entries(),
    ]

    # Rebel Readline is a Clojure namespace, invoked via clojure.main -m
    argv = [
        *jdk.args(bash, classpath_entries),
        "clojure.main",
        "-m",
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
