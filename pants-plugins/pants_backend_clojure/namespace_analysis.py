"""Clojure namespace analysis using clj-kondo.

This module provides a Pants rule that uses clj-kondo to analyze Clojure source files
and extract namespace metadata (namespace name, requires, and imports). It properly
uses the Pants-managed clj-kondo ExternalTool rather than relying on a system-installed
binary.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from pants.core.util_rules.config_files import ConfigFiles, ConfigFilesRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.fs import Digest, MergeDigests, Snapshot
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import collect_rules, Get, rule
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel
from pants.util.strutil import pluralize

from pants_backend_clojure.subsystems.clj_kondo import CljKondo


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClojureNamespaceAnalysisRequest:
    """Request to analyze Clojure source files for namespace metadata.

    Uses Snapshot instead of just Digest to preserve file paths in the analysis result.
    clj-kondo is run in batch mode on all files at once for efficiency.
    """

    snapshot: Snapshot  # Source files to analyze (includes paths and digest)


@dataclass(frozen=True)
class ClojureNamespaceAnalysis:
    """Result of clj-kondo analysis on Clojure source files.

    All file paths are relative paths matching those in the input Snapshot.
    """

    # Maps file path -> namespace name
    namespaces: FrozenDict[str, str]
    # Maps file path -> tuple of required namespaces
    requires: FrozenDict[str, tuple[str, ...]]
    # Maps file path -> tuple of imported Java classes
    imports: FrozenDict[str, tuple[str, ...]]


@rule(desc="Analyze Clojure namespaces with clj-kondo", level=LogLevel.DEBUG)
async def analyze_clojure_namespaces(
    request: ClojureNamespaceAnalysisRequest,
    clj_kondo: CljKondo,
    platform: Platform,
) -> ClojureNamespaceAnalysis:
    """Analyze Clojure source files to extract namespace metadata.

    Uses clj-kondo in batch mode to analyze all files in a single invocation.
    File paths in the result match those in the input Snapshot.

    Error Handling:
    - If clj-kondo fails to parse a file, that file is omitted from results
    - clj-kondo non-zero exit codes (from lint warnings) are ignored
    - Empty or malformed JSON output returns empty analysis
    """
    if not request.snapshot.files:
        return ClojureNamespaceAnalysis(
            namespaces=FrozenDict({}),
            requires=FrozenDict({}),
            imports=FrozenDict({}),
        )

    # Download clj-kondo binary
    downloaded = await Get(
        DownloadedExternalTool,
        ExternalToolRequest,
        clj_kondo.get_request(platform),
    )

    # Find config files if discovery is enabled
    config_files = await Get(
        ConfigFiles,
        ConfigFilesRequest(
            discovery=clj_kondo.config_discovery,
            check_existence=[".clj-kondo/config.edn"],
        ),
    )

    # Merge source files, clj-kondo binary, and config files
    input_digest = await Get(
        Digest,
        MergeDigests([
            request.snapshot.digest,
            downloaded.digest,
            config_files.snapshot.digest,
        ]),
    )

    # Run clj-kondo analysis in batch mode on all files
    result = await Get(
        FallibleProcessResult,
        Process(
            argv=[
                downloaded.exe,
                "--lint",
                *request.snapshot.files,
                "--config",
                "{:output {:analysis {:java-class-usages true} :format :json}}",
            ],
            input_digest=input_digest,
            description=f"Analyze {pluralize(len(request.snapshot.files), 'Clojure file')} with clj-kondo",
            level=LogLevel.DEBUG,
        ),
    )

    # Parse JSON output - clj-kondo may return non-zero for lint warnings, that's ok
    try:
        stdout = result.stdout.decode()
        if not stdout.strip():
            # Empty output - return empty analysis
            return ClojureNamespaceAnalysis(
                namespaces=FrozenDict({}),
                requires=FrozenDict({}),
                imports=FrozenDict({}),
            )
        parsed = json.loads(stdout)
        # clj-kondo nests the analysis data under an "analysis" key
        analysis = parsed.get("analysis", {})
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"Failed to parse clj-kondo output: {e}")
        return ClojureNamespaceAnalysis(
            namespaces=FrozenDict({}),
            requires=FrozenDict({}),
            imports=FrozenDict({}),
        )

    # Build result mappings using relative file paths
    namespaces: dict[str, str] = {}
    requires_dict: dict[str, list[str]] = {}
    imports_dict: dict[str, list[str]] = {}

    for ns_def in analysis.get("namespace-definitions", []):
        # clj-kondo returns paths relative to working directory
        path = ns_def["filename"]
        namespaces[path] = ns_def["name"]

    for ns_usage in analysis.get("namespace-usages", []):
        path = ns_usage["filename"]
        requires_dict.setdefault(path, []).append(ns_usage["to"])

    for java_usage in analysis.get("java-class-usages", []):
        if java_usage.get("import"):
            path = java_usage["filename"]
            imports_dict.setdefault(path, []).append(java_usage["class"])

    return ClojureNamespaceAnalysis(
        namespaces=FrozenDict(namespaces),
        requires=FrozenDict({k: tuple(sorted(set(v))) for k, v in requires_dict.items()}),
        imports=FrozenDict({k: tuple(sorted(set(v))) for k, v in imports_dict.items()}),
    )


def rules():
    return [
        *collect_rules(),
    ]
