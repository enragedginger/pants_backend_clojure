from __future__ import annotations

import tomllib
from dataclasses import dataclass

from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.console import Console
from pants.engine.fs import (
    CreateDigest,
    Digest,
    DigestContents,
    FileContent,
    PathGlobs,
    Workspace,
)
from pants.engine.goal import Goal, GoalSubsystem
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.rules import collect_rules, goal_rule
from pants.engine.target import AllTargets
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.target_types import JvmResolveField
from pants.option.option_types import StrOption
from pants.util.logging import LogLevel

from clojure_backend.target_types import (
    ClojureSourceField,
    ClojureSourceTarget,
    ClojureTestSourceField,
    ClojureTestTarget,
)
from clojure_backend.utils.namespace_parser import parse_namespace
from clojure_backend.utils.source_roots import determine_source_root as _determine_source_root


class GenerateDepsEdnSubsystem(GoalSubsystem):
    """Generate deps.edn file from Pants lock files for IDE integration."""

    name = "generate-deps-edn"
    help = """Generate a deps.edn file from Pants dependency information for a specific resolve.

This allows traditional Clojure tooling (IDEs like Cursive and Calva) to work with
Pants-managed projects. The generated deps.edn includes all Clojure sources and
third-party dependencies for the specified resolve.

## What's Included

- **Clojure sources**: All clojure_source and clojure_test targets in the resolve
- **Third-party dependencies**: All JVM dependencies from the lock file
- **Aliases**: Pre-configured :test, :nrepl, and :rebel aliases

## What's NOT Included (By Design)

**Java and Scala sources are intentionally excluded** from the generated deps.edn.

Rationale:
- deps.edn is designed for Clojure source files, which can be loaded directly
- Java/Scala sources must be compiled to .class files before use
- Pants compiles Java/Scala automatically when building classpaths
- For mixed JVM codebases, use `pants repl` which handles compilation correctly
- Most Clojure IDEs don't provide compile-on-save for Java/Scala anyway

If you need Java/Scala interop in your REPL:
- Use `pants repl` instead of `clj` - it includes compiled Java/Scala on the classpath
- Or manually compile Java/Scala and add the JARs to deps.edn :extra-paths

See docs/plans/20251015_repl_redesign.md for more details.
"""

    resolve = StrOption(
        default=None,
        help="The JVM resolve to generate deps.edn for (e.g., 'java21', 'java17'). "
        "If not specified, uses the default resolve.",
    )

    output_path = StrOption(
        default="deps.edn",
        help="Output path for the generated deps.edn file, relative to the build root.",
    )


class GenerateDepsEdn(Goal):
    subsystem_cls = GenerateDepsEdnSubsystem
    environment_behavior = Goal.EnvironmentBehavior.LOCAL_ONLY


@dataclass(frozen=True)
class LockFileEntry:
    """Parsed entry from a Pants JVM lock file."""

    group: str
    artifact: str
    version: str
    packaging: str = "jar"


def parse_lock_file(lock_content: str) -> list[LockFileEntry]:
    """Parse a Pants TOML lock file and extract dependency coordinates.

    Returns a list of LockFileEntry objects representing each dependency.
    """
    try:
        lock_data = tomllib.loads(lock_content)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(
            f"Failed to parse lock file: {e}\n\n"
            f"Common causes:\n"
            f"  - Invalid TOML syntax in lock file\n"
            f"  - Corrupted or manually edited lock file\n"
            f"  - Lock file format version mismatch\n\n"
            f"Troubleshooting:\n"
            f"  1. Regenerate the lock file: pants generate-lockfiles\n"
            f"  2. Check for manual edits to the lock file\n"
            f"  3. Verify lock file is valid TOML format\n"
        )

    entries = []
    for entry in lock_data.get("entries", []):
        coord = entry.get("coord", {})
        if "group" in coord and "artifact" in coord and "version" in coord:
            entries.append(
                LockFileEntry(
                    group=coord["group"],
                    artifact=coord["artifact"],
                    version=coord["version"],
                    packaging=coord.get("packaging", "jar"),
                )
            )

    return entries


def format_deps_edn_deps(entries: list[LockFileEntry]) -> str:
    """Format lock file entries as deps.edn :deps map.

    Each dependency includes :exclusions [*] to prevent transitive resolution,
    since Pants lock files already have all transitives flattened.

    Defensively deduplicates entries by (group, artifact) - if duplicates exist,
    the first one encountered (after sorting by group/artifact) is kept.
    """
    if not entries:
        return "{}"

    # Deduplicate by (group, artifact), keeping first entry after sorting
    seen: dict[tuple[str, str], LockFileEntry] = {}
    for entry in sorted(entries, key=lambda e: (e.group, e.artifact)):
        key = (entry.group, entry.artifact)
        if key not in seen:
            seen[key] = entry

    dep_lines = []
    for entry in seen.values():
        dep_key = f"{entry.group}/{entry.artifact}"
        dep_value = f'{{:mvn/version "{entry.version}" :exclusions [*]}}'
        dep_lines.append(f"   {dep_key} {dep_value}")

    # Sort for consistent output ordering
    dep_lines.sort()

    return "{\n" + "\n".join(dep_lines) + "}"


def determine_source_root(file_path: str, source_content: str) -> str | None:
    """Determine the source root directory for a Clojure file.

    For a file like projects/foo/src/example/core.clj with namespace example.core,
    the source root is projects/foo/src.

    Args:
        file_path: The actual path to the Clojure source file (from DigestContents)
        source_content: The file content for namespace parsing

    Returns None if the namespace can't be parsed.
    """
    namespace = parse_namespace(source_content)
    if not namespace:
        return None

    # Use the shared utility function with the actual file path
    return _determine_source_root(file_path, namespace)


@dataclass(frozen=True)
class ClojureSourcesInfo:
    """Information about Clojure sources for a resolve."""

    source_paths: set[str]
    test_paths: set[str]


async def gather_clojure_sources_for_resolve(
    all_targets: AllTargets, jvm: JvmSubsystem, resolve_name: str
) -> ClojureSourcesInfo:
    """Gather all Clojure source and test paths for a specific resolve.

    Note: This function intentionally only includes Clojure sources, not Java or Scala.

    Design Decision: Java and Scala sources are excluded because:
    1. They must be compiled to .class files before use (unlike Clojure source files)
    2. deps.edn's :paths is designed for source files that can be loaded directly
    3. Pants automatically compiles Java/Scala when building classpaths
    4. For mixed JVM codebases, `pants repl` is the recommended workflow

    If users need compiled Java/Scala classes:
    - Use `pants repl` which includes compiled Java/Scala JARs in the classpath
    - Or manually add compiled JARs to deps.edn :extra-paths alias
    """

    source_targets = []
    test_targets = []

    for target in all_targets:
        # Check if target has a resolve field and matches our resolve
        if not target.has_field(JvmResolveField):
            continue

        target_resolve = target[JvmResolveField].normalized_value(jvm)
        if target_resolve != resolve_name:
            continue

        # Categorize by target type
        # NOTE: Only collecting Clojure targets here - Java/Scala targets are intentionally skipped
        if isinstance(target, ClojureSourceTarget) and target.has_field(ClojureSourceField):
            source_targets.append(target)
        elif isinstance(target, ClojureTestTarget) and target.has_field(ClojureTestSourceField):
            test_targets.append(target)

    # Fetch source files for all targets in parallel
    source_files_requests = []
    for target in source_targets:
        source_files_requests.append(
            Get(SourceFiles, SourceFilesRequest([target[ClojureSourceField]]))
        )
    for target in test_targets:
        source_files_requests.append(
            Get(SourceFiles, SourceFilesRequest([target[ClojureTestSourceField]]))
        )

    all_source_files = await MultiGet(source_files_requests)

    # Get digest contents to read file contents for namespace parsing
    digest_requests = [
        Get(DigestContents, Digest, sf.snapshot.digest) for sf in all_source_files
    ]
    all_digest_contents = await MultiGet(digest_requests)

    # Determine source roots
    source_roots = set()
    test_roots = set()

    # Process source targets
    for i, target in enumerate(source_targets):
        digest_contents = all_digest_contents[i]
        if not digest_contents:
            # Fallback: use target directory (only when no files match)
            source_roots.add(target.address.spec_path or ".")
            continue

        source_content = digest_contents[0].content.decode("utf-8")
        file_path = digest_contents[0].path  # Use actual file path from digest
        source_root = determine_source_root(file_path, source_content)
        if source_root:
            source_roots.add(source_root)
        else:
            # Fallback: use directory containing the file
            source_roots.add("/".join(file_path.split("/")[:-1]) or ".")

    # Process test targets
    test_offset = len(source_targets)
    for i, target in enumerate(test_targets):
        digest_contents = all_digest_contents[test_offset + i]
        if not digest_contents:
            # Fallback: use target directory (only when no files match)
            test_roots.add(target.address.spec_path or ".")
            continue

        source_content = digest_contents[0].content.decode("utf-8")
        file_path = digest_contents[0].path  # Use actual file path from digest
        source_root = determine_source_root(file_path, source_content)
        if source_root:
            test_roots.add(source_root)
        else:
            # Fallback: use directory containing the file
            test_roots.add("/".join(file_path.split("/")[:-1]) or ".")

    return ClojureSourcesInfo(source_paths=source_roots, test_paths=test_roots)


def format_deps_edn(
    sources_info: ClojureSourcesInfo,
    deps_entries: list[LockFileEntry],
    resolve_name: str,
) -> str:
    """Format complete deps.edn file content."""

    # Format :paths (source paths)
    paths = sorted(sources_info.source_paths)
    if paths:
        paths_str = '["' + '"\n         "'.join(paths) + '"]'
    else:
        paths_str = "[]"

    # Format :deps (dependencies with :exclusions [*])
    deps_str = format_deps_edn_deps(deps_entries)

    # Format :aliases
    test_paths = sorted(sources_info.test_paths)
    aliases_content = []

    # Add test alias - always include it, even if there are no test paths
    # This makes it easier for users to add tests later
    if test_paths:
        test_paths_str = '["' + '"\n                         "'.join(test_paths) + '"]'
    else:
        test_paths_str = "[]"
    aliases_content.append(f'  :test {{:extra-paths {test_paths_str}}}')

    # Add nREPL alias
    aliases_content.append(
        '  :nrepl {:extra-deps {nrepl/nrepl {:mvn/version "1.4.0" :exclusions [*]}}}'
    )

    # Add rebel-readline alias
    aliases_content.append(
        '  :rebel {:extra-deps {com.bhauman/rebel-readline {:mvn/version "0.1.4" :exclusions [*]}}}'
    )

    aliases_str = "{\n" + "\n".join(aliases_content) + "}"

    # Assemble full deps.edn
    content = f""";; Generated by Pants (pants generate-deps-edn --resolve={resolve_name})
;; DO NOT EDIT - This file is auto-generated
;;
;; To regenerate: pants generate-deps-edn --resolve={resolve_name}
;;
;; This deps.edn file includes all Clojure sources and dependencies for the '{resolve_name}' resolve.
;; Use with standard Clojure tooling (clj, Cursive, Calva, etc.)

{{:paths {paths_str}

 :deps {deps_str}

 :aliases {aliases_str}}}
"""

    return content


@goal_rule
async def generate_deps_edn_goal(
    console: Console,
    workspace: Workspace,
    subsystem: GenerateDepsEdnSubsystem,
    all_targets: AllTargets,
    jvm: JvmSubsystem,
) -> GenerateDepsEdn:
    """Generate a deps.edn file for IDE integration."""

    # Determine which resolve to use
    resolve_name = subsystem.resolve
    if not resolve_name:
        # Use default resolve
        resolve_name = jvm.default_resolve
        console.print_stdout(f"Using default resolve: {resolve_name}")

    # Validate resolve exists
    if resolve_name not in jvm.resolves:
        available = ", ".join(jvm.resolves.keys())
        console.print_stderr(
            f"Error: Resolve '{resolve_name}' not found. Available resolves: {available}"
        )
        return GenerateDepsEdn(exit_code=1)

    console.print_stdout(f"Generating deps.edn for resolve: {resolve_name}")

    # Get lock file path and read its contents
    lock_file_path = jvm.resolves[resolve_name]
    console.print_stdout(f"Reading lock file: {lock_file_path}")

    try:
        # Read lock file using PathGlobs
        lock_digest = await Get(Digest, PathGlobs([lock_file_path]))
        lock_contents = await Get(DigestContents, Digest, lock_digest)

        if not lock_contents:
            console.print_stderr(f"Error: Could not read lock file: {lock_file_path}")
            return GenerateDepsEdn(exit_code=1)

        lock_content = lock_contents[0].content.decode("utf-8")
        lock_entries = parse_lock_file(lock_content)
        console.print_stdout(f"Found {len(lock_entries)} dependencies in lock file")

    except Exception as e:
        console.print_stderr(f"Error reading lock file: {e}")
        return GenerateDepsEdn(exit_code=1)

    # Gather Clojure sources for this resolve
    console.print_stdout(f"Gathering Clojure sources for resolve '{resolve_name}'...")
    sources_info = await gather_clojure_sources_for_resolve(
        all_targets, jvm, resolve_name
    )

    console.print_stdout(
        f"Found {len(sources_info.source_paths)} source roots and "
        f"{len(sources_info.test_paths)} test roots"
    )

    # Format deps.edn content
    deps_edn_content = format_deps_edn(sources_info, lock_entries, resolve_name)

    # Write to file
    output_path = subsystem.output_path
    console.print_stdout(f"Writing deps.edn to: {output_path}")

    # Create digest with the file content
    file_content = FileContent(output_path, deps_edn_content.encode("utf-8"))
    output_digest = await Get(Digest, CreateDigest([file_content]))

    # Write to workspace
    workspace.write_digest(output_digest)

    console.print_stdout(f"\nSuccessfully generated {output_path}")
    console.print_stdout(f"\nYou can now use standard Clojure tooling:")
    console.print_stdout(f"  clj -M:nrepl -m nrepl.server      # Start nREPL server")
    console.print_stdout(f"  clj -M:rebel                      # Start Rebel Readline REPL")
    console.print_stdout(f"  # Or open project in Cursive/Calva")

    return GenerateDepsEdn(exit_code=0)


def rules():
    return [
        *collect_rules(),
    ]
