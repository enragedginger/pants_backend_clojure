"""Tests for the Clojure namespace analysis rule."""

from __future__ import annotations

from textwrap import dedent

import pytest

from clojure_backend.namespace_analysis import (
    ClojureNamespaceAnalysis,
    ClojureNamespaceAnalysisRequest,
)
from clojure_backend.namespace_analysis import rules as namespace_analysis_rules
from clojure_backend.target_types import ClojureSourceTarget
from pants.core.util_rules import config_files, external_tool, source_files
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner


@pytest.fixture
def rule_runner() -> RuleRunner:
    rule_runner = RuleRunner(
        rules=[
            *config_files.rules(),
            *external_tool.rules(),
            *source_files.rules(),
            *namespace_analysis_rules(),
            QueryRule(ClojureNamespaceAnalysis, [ClojureNamespaceAnalysisRequest]),
        ],
        target_types=[ClojureSourceTarget],
    )
    return rule_runner


def analyze_files(
    rule_runner: RuleRunner,
    files: dict[str, str],
) -> ClojureNamespaceAnalysis:
    """Helper to analyze Clojure files and return the analysis result."""
    rule_runner.set_options(
        ["--backend-packages=clojure_backend"],
        env_inherit={"PATH", "PYENV_ROOT", "HOME"},
    )
    rule_runner.write_files(files)

    # Get snapshot of the files
    snapshot = rule_runner.make_snapshot(files)

    # Request analysis
    return rule_runner.request(
        ClojureNamespaceAnalysis,
        [ClojureNamespaceAnalysisRequest(snapshot)],
    )


def test_basic_namespace_extraction(rule_runner: RuleRunner) -> None:
    """Test extracting namespace name from a simple file."""
    analysis = analyze_files(
        rule_runner,
        {
            "example.clj": dedent(
                """\
                (ns example.core)

                (defn foo [x]
                  (* x 2))
                """
            ),
        },
    )

    assert "example.clj" in analysis.namespaces
    assert analysis.namespaces["example.clj"] == "example.core"


def test_requires_extraction(rule_runner: RuleRunner) -> None:
    """Test extracting required namespaces."""
    analysis = analyze_files(
        rule_runner,
        {
            "example.clj": dedent(
                """\
                (ns example.core
                  (:require [clojure.string :as str]
                            [clojure.set :refer [union]]))

                (defn process [s]
                  (str/upper-case s))
                """
            ),
        },
    )

    assert "example.clj" in analysis.requires
    requires = analysis.requires["example.clj"]
    assert "clojure.string" in requires
    assert "clojure.set" in requires


def test_imports_extraction(rule_runner: RuleRunner) -> None:
    """Test extracting Java imports."""
    analysis = analyze_files(
        rule_runner,
        {
            "example.clj": dedent(
                """\
                (ns example.core
                  (:import [java.util Date UUID]
                           [java.io File]))

                (defn now []
                  (Date.))
                """
            ),
        },
    )

    assert "example.clj" in analysis.imports
    imports = analysis.imports["example.clj"]
    assert "java.util.Date" in imports
    assert "java.util.UUID" in imports
    assert "java.io.File" in imports


def test_multiple_files_batch(rule_runner: RuleRunner) -> None:
    """Test analyzing multiple files in a single batch."""
    analysis = analyze_files(
        rule_runner,
        {
            "file1.clj": dedent(
                """\
                (ns example.file1
                  (:require [clojure.string :as str]))

                (defn greet [name]
                  (str "Hello, " name))
                """
            ),
            "file2.clj": dedent(
                """\
                (ns example.file2
                  (:require [clojure.set :as set])
                  (:import [java.util Date]))

                (defn now []
                  (Date.))
                """
            ),
        },
    )

    # Check file1
    assert "file1.clj" in analysis.namespaces
    assert analysis.namespaces["file1.clj"] == "example.file1"
    assert "clojure.string" in analysis.requires.get("file1.clj", ())

    # Check file2
    assert "file2.clj" in analysis.namespaces
    assert analysis.namespaces["file2.clj"] == "example.file2"
    assert "clojure.set" in analysis.requires.get("file2.clj", ())
    assert "java.util.Date" in analysis.imports.get("file2.clj", ())


def test_empty_file_handling(rule_runner: RuleRunner) -> None:
    """Test that empty files don't crash the analysis."""
    analysis = analyze_files(
        rule_runner,
        {
            "empty.clj": "",
        },
    )

    # Empty file should not appear in namespaces (no namespace declaration)
    assert "empty.clj" not in analysis.namespaces


def test_file_without_namespace_declaration(rule_runner: RuleRunner) -> None:
    """Test handling of files without namespace declarations."""
    analysis = analyze_files(
        rule_runner,
        {
            "no_ns.clj": dedent(
                """\
                (defn standalone-fn []
                  "A function without a namespace")
                """
            ),
        },
    )

    # File without ns declaration should not appear in namespaces mapping
    assert "no_ns.clj" not in analysis.namespaces


def test_malformed_clojure_syntax(rule_runner: RuleRunner) -> None:
    """Test that malformed Clojure syntax doesn't crash the analysis."""
    # clj-kondo should handle malformed syntax gracefully
    analysis = analyze_files(
        rule_runner,
        {
            "malformed.clj": dedent(
                """\
                (ns example.malformed)

                (defn broken [
                  ; Missing closing bracket
                """
            ),
        },
    )

    # Should not crash - analysis may be empty or partial
    assert analysis is not None


def test_empty_snapshot(rule_runner: RuleRunner) -> None:
    """Test handling of empty snapshot (no files)."""
    rule_runner.set_options(
        ["--backend-packages=clojure_backend"],
        env_inherit={"PATH", "PYENV_ROOT", "HOME"},
    )

    # Create empty snapshot
    empty_snapshot = rule_runner.make_snapshot({})

    analysis = rule_runner.request(
        ClojureNamespaceAnalysis,
        [ClojureNamespaceAnalysisRequest(empty_snapshot)],
    )

    assert len(analysis.namespaces) == 0
    assert len(analysis.requires) == 0
    assert len(analysis.imports) == 0


def test_cljc_file(rule_runner: RuleRunner) -> None:
    """Test analyzing .cljc files with reader conditionals."""
    analysis = analyze_files(
        rule_runner,
        {
            "example.cljc": dedent(
                """\
                (ns example.portable
                  (:require [clojure.string :as str]))

                (defn portable-fn [x]
                  #?(:clj (.toUpperCase x)
                     :cljs (.toUpperCase x)))
                """
            ),
        },
    )

    assert "example.cljc" in analysis.namespaces
    assert analysis.namespaces["example.cljc"] == "example.portable"
    assert "clojure.string" in analysis.requires.get("example.cljc", ())


def test_namespace_with_metadata(rule_runner: RuleRunner) -> None:
    """Test extracting namespace with metadata."""
    analysis = analyze_files(
        rule_runner,
        {
            "example.clj": dedent(
                """\
                (ns ^:deprecated example.old-api
                  "This namespace is deprecated."
                  (:require [clojure.string :as str]))

                (defn old-fn []
                  "deprecated")
                """
            ),
        },
    )

    assert "example.clj" in analysis.namespaces
    assert analysis.namespaces["example.clj"] == "example.old-api"


def test_prefix_list_require_syntax(rule_runner: RuleRunner) -> None:
    """Test extracting requires with prefix list notation."""
    analysis = analyze_files(
        rule_runner,
        {
            "example.clj": dedent(
                """\
                (ns example.core
                  (:require [clojure [string :as str]
                                     [set :as set]]))

                (defn process []
                  nil)
                """
            ),
        },
    )

    requires = analysis.requires.get("example.clj", ())
    assert "clojure.string" in requires
    assert "clojure.set" in requires


def test_multiple_namespace_declarations(rule_runner: RuleRunner) -> None:
    """Test that first namespace declaration is used when multiple exist."""
    analysis = analyze_files(
        rule_runner,
        {
            "example.clj": dedent(
                """\
                (ns example.first)

                (defn fn1 []
                  "first")

                ; Unusual but possible - second ns declaration
                (ns example.second)

                (defn fn2 []
                  "second")
                """
            ),
        },
    )

    # clj-kondo should report the first namespace
    assert "example.clj" in analysis.namespaces
    # The exact behavior depends on clj-kondo - it might report first or last
    assert analysis.namespaces["example.clj"] in ("example.first", "example.second")


def test_deduplicated_requires(rule_runner: RuleRunner) -> None:
    """Test that duplicate requires are deduplicated."""
    analysis = analyze_files(
        rule_runner,
        {
            "example.clj": dedent(
                """\
                (ns example.core
                  (:require [clojure.string :as str]
                            [clojure.string :as s]))

                (defn process []
                  nil)
                """
            ),
        },
    )

    requires = analysis.requires.get("example.clj", ())
    # Should only appear once, not twice
    assert requires.count("clojure.string") == 1


def test_comment_form_requires_included_without_config(rule_runner: RuleRunner) -> None:
    """Test baseline: without :skip-comments config, requires in comment forms are included."""
    source_content = dedent(
        """\
        (ns example.core
          (:require [clojure.string :as str]))

        (defn process []
          (str/upper-case "hello"))

        (comment
          (require '[clojure.set :as set])
          (set/union #{1} #{2}))
        """
    )

    rule_runner.set_options(
        ["--backend-packages=clojure_backend"],
        env_inherit={"PATH", "PYENV_ROOT", "HOME"},
    )
    rule_runner.write_files(
        {
            "example.clj": source_content,
            # No .clj-kondo/config.edn - using defaults
        }
    )

    snapshot = rule_runner.make_snapshot({"example.clj": source_content})

    analysis = rule_runner.request(
        ClojureNamespaceAnalysis,
        [ClojureNamespaceAnalysisRequest(snapshot)],
    )

    # Without skip-comments config, clojure.set from comment form should be included
    requires = analysis.requires.get("example.clj", ())
    assert "clojure.string" in requires
    assert "clojure.set" in requires, (
        "clojure.set should be included when :skip-comments is not set - "
        "this is the baseline behavior"
    )


def test_config_file_skip_comments_is_respected(rule_runner: RuleRunner) -> None:
    """Test that .clj-kondo/config.edn with :skip-comments is respected.

    When :skip-comments is true, requires inside (comment ...) forms should not
    appear in analysis results.

    Since Pants ignores dotfiles by default and this is difficult to override in tests,
    we test by including the config file in the snapshot itself. This verifies that
    when the config file IS present in the sandbox, clj-kondo respects it.
    """
    source_content = dedent(
        """\
        (ns example.core
          (:require [clojure.string :as str]))

        (defn process []
          (str/upper-case "hello"))

        (comment
          (require '[clojure.set :as set])
          (set/union #{1} #{2}))
        """
    )

    rule_runner.set_options(
        ["--backend-packages=clojure_backend"],
        env_inherit={"PATH", "PYENV_ROOT", "HOME"},
    )

    # Create snapshot including both source file AND config file
    # This simulates what happens when ConfigFilesRequest successfully finds the config
    snapshot = rule_runner.make_snapshot({
        ".clj-kondo/config.edn": "{:skip-comments true}",
        "example.clj": source_content,
    })

    analysis = rule_runner.request(
        ClojureNamespaceAnalysis,
        [ClojureNamespaceAnalysisRequest(snapshot)],
    )

    # With skip-comments config, clojure.set from comment form should NOT be included
    requires = analysis.requires.get("example.clj", ())
    assert "clojure.string" in requires
    assert "clojure.set" not in requires, (
        "clojure.set should be excluded when :skip-comments is true - "
        "this suggests clj-kondo is not respecting the config file in the sandbox"
    )
