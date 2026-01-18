from __future__ import annotations

from pants_backend_clojure.target_types import (
    ClojureFieldSet,
    ClojureGeneratorFieldSet,
    ClojureSourceField,
    ClojureTestFieldSet,
    ClojureTestGeneratorFieldSet,
    ClojureTestSourceField,
)
from pants.core.util_rules.source_files import (
    SourceFiles,
    SourceFilesRequest,
    determine_source_files,
)
from pants.core.util_rules.stripped_source_files import StrippedSourceFiles, strip_source_roots
from pants.engine.fs import Digest, MergeDigests
from pants.engine.intrinsics import merge_digests
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.unions import UnionRule
from pants.jvm.compile import (
    ClasspathDependenciesRequest,
    ClasspathEntry,
    ClasspathEntryRequest,
    CompileResult,
    FallibleClasspathEntry,
    compile_classpath_entries,
)
from pants.util.logging import LogLevel


class CompileClojureSourceRequest(ClasspathEntryRequest):
    field_sets = (
        ClojureFieldSet,
        ClojureGeneratorFieldSet,
        ClojureTestFieldSet,
        ClojureTestGeneratorFieldSet,
    )


@rule(desc="Compile Clojure sources (runtime compilation)", level=LogLevel.DEBUG)
async def compile_clojure_source(
    request: CompileClojureSourceRequest,
) -> FallibleClasspathEntry:
    """Provide classpath entry for Clojure sources.

    Since Clojure supports runtime compilation, we don't need AOT compilation.
    Instead, we:
    1. Pass through all dependencies
    2. Include the raw .clj source files in the classpath

    This allows clojure.main to load and compile the sources at runtime.
    """
    # Get compiled dependencies
    fallible_result = await compile_classpath_entries(
        **implicitly(ClasspathDependenciesRequest(request))
    )

    direct_dependency_classpath_entries = fallible_result.if_all_succeeded()

    if direct_dependency_classpath_entries is None:
        return FallibleClasspathEntry(
            description=str(request.component),
            result=CompileResult.DEPENDENCY_FAILED,
            output=None,
            exit_code=1,
        )

    # For generator targets with no sources, just pass through dependencies
    members_with_sources = [
        t for t in request.component.members
        if t.has_field(ClojureSourceField) or t.has_field(ClojureTestSourceField)
    ]

    if not members_with_sources:
        # Generator target - merge all dependency digests
        merged_digest = await merge_digests(
            MergeDigests([cpe.digest for cpe in direct_dependency_classpath_entries]),
        )
        classpath_entry = ClasspathEntry.merge(
            merged_digest, direct_dependency_classpath_entries
        )
        return FallibleClasspathEntry(
            description=str(request.component),
            result=CompileResult.SUCCEEDED,
            output=classpath_entry,
            exit_code=0,
        )

    # Get source files for targets with sources
    source_files = await determine_source_files(
        SourceFilesRequest(
            (t.get(ClojureSourceField) if t.has_field(ClojureSourceField)
             else t.get(ClojureTestSourceField)
             for t in members_with_sources),
            for_sources_types=(ClojureSourceField, ClojureTestSourceField),
            enable_codegen=True,
        ),
    )

    # Strip source roots so files are at proper paths for Clojure's namespace resolution
    # e.g., projects/example/project-a/test/example/project_a/core_test.clj
    # becomes example/project_a/core_test.clj
    stripped_sources = await strip_source_roots(source_files)

    # Merge stripped source files with dependency digests
    merged_digest = await merge_digests(
        MergeDigests([
            stripped_sources.snapshot.digest,
            *(cpe.digest for cpe in direct_dependency_classpath_entries),
        ]),
    )

    # Create classpath entry with sources and dependencies
    # The sources will be available at runtime for dynamic compilation
    classpath_entry = ClasspathEntry(
        merged_digest,
        filenames=stripped_sources.snapshot.files,
        dependencies=direct_dependency_classpath_entries,
    )

    return FallibleClasspathEntry(
        description=str(request.component),
        result=CompileResult.SUCCEEDED,
        output=classpath_entry,
        exit_code=0,
    )


def rules():
    from pants.jvm.compile import rules as jvm_compile_rules

    return [
        *collect_rules(),
        *jvm_compile_rules(),
        UnionRule(ClasspathEntryRequest, CompileClojureSourceRequest),
    ]
