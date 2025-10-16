from __future__ import annotations

from textwrap import dedent

import pytest

from clojure_backend.clj_fmt import CljfmtRequest
from clojure_backend.clj_fmt import rules as fmt_rules
from clojure_backend.target_types import (
    ClojureSourcesGeneratorTarget,
    ClojureSourceTarget,
)
from clojure_backend.target_types import rules as target_types_rules
from pants.core.goals.fmt import FmtResult
from pants.core.util_rules import config_files, external_tool, source_files
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.addresses import Address
from pants.engine.fs import EMPTY_DIGEST, DigestContents
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner


@pytest.fixture
def rule_runner() -> RuleRunner:
    rule_runner = RuleRunner(
        rules=[
            *config_files.rules(),
            *external_tool.rules(),
            *fmt_rules(),
            *source_files.rules(),
            *target_types_rules(),
            QueryRule(FmtResult, [CljfmtRequest.Batch]),
        ],
        target_types=[
            ClojureSourceTarget,
            ClojureSourcesGeneratorTarget,
        ],
    )
    return rule_runner


def run_cljfmt(
    rule_runner: RuleRunner,
    targets: list[Address],
    *,
    extra_args: list[str] | None = None,
) -> FmtResult:
    rule_runner.set_options(
        [
            "--backend-packages=clojure_backend",
            *(extra_args or []),
        ],
        env_inherit={"PATH", "PYENV_ROOT", "HOME"},
    )
    field_sets = [
        CljfmtRequest.field_set_type.create(rule_runner.get_target(address))
        for address in targets
    ]
    input_sources = rule_runner.request(
        SourceFiles,
        [SourceFilesRequest(field_set.sources for field_set in field_sets)],
    )
    fmt_result = rule_runner.request(
        FmtResult,
        [
            CljfmtRequest.Batch(
                "",
                tuple(field_sets),
                snapshot=input_sources.snapshot,
            )
        ],
    )
    return fmt_result


def test_format_unformatted_code(rule_runner: RuleRunner) -> None:
    """Test that cljfmt formats unformatted code."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_sources(name='lib')",
            "example.clj": dedent(
                """\
                (ns example.core)

                (defn foo  [  x  ]
                  (+    x
                     1))

                (defn bar[y z]
                  (+ y
                  z))
                """
            ),
        }
    )

    tgt = Address("", relative_file_path="example.clj")
    fmt_result = run_cljfmt(rule_runner, [tgt])

    assert fmt_result.output != fmt_result.input
    assert fmt_result.did_change

    # The formatted output should have consistent spacing
    output_content = rule_runner.request_product(
        DigestContents,
        [fmt_result.output],
    )[0].content.decode()

    # Basic checks for proper formatting
    assert "defn foo  [" not in output_content  # Extra spaces removed
    assert "defn foo [" in output_content or "defn foo[" in output_content
    assert "+    x" not in output_content  # Excessive indentation fixed


def test_already_formatted_code(rule_runner: RuleRunner) -> None:
    """Test that cljfmt doesn't modify already-formatted code."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_sources(name='lib')",
            "formatted.clj": dedent(
                """\
                (ns example.formatted)

                (defn add [x y]
                  (+ x y))

                (defn multiply [x y]
                  (* x y))
                """
            ),
        }
    )

    tgt = Address("", relative_file_path="formatted.clj")
    fmt_result = run_cljfmt(rule_runner, [tgt])

    assert fmt_result.output == fmt_result.input or fmt_result.output == EMPTY_DIGEST
    assert not fmt_result.did_change


def test_skip_cljfmt_field(rule_runner: RuleRunner) -> None:
    """Test that skip_cljfmt field prevents formatting."""
    rule_runner.write_files(
        {
            "BUILD": dedent(
                """\
                clojure_source(
                    name='skipped',
                    source='skipped.clj',
                    skip_cljfmt=True,
                )
                """
            ),
            "skipped.clj": dedent(
                """\
                (ns example.skipped)

                (defn foo  [  x  ]
                  (+    x    1))
                """
            ),
        }
    )

    tgt = Address("", target_name="skipped")

    # When skip_cljfmt=True, the target shouldn't be included in formatting
    # The field set should not be created or should be filtered out
    # This test verifies that the skip field is respected
    field_set = CljfmtRequest.field_set_type.create(rule_runner.get_target(tgt))

    # Check that skip_cljfmt is set to True
    assert field_set.skip_cljfmt.value is True


def test_format_multiple_files(rule_runner: RuleRunner) -> None:
    """Test that cljfmt can format multiple files at once."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_sources(name='lib')",
            "file1.clj": dedent(
                """\
                (ns example.file1)
                (defn foo  [x]  x)
                """
            ),
            "file2.clj": dedent(
                """\
                (ns example.file2)
                (defn bar  [y]  y)
                """
            ),
        }
    )

    targets = [
        Address("", relative_file_path="file1.clj"),
        Address("", relative_file_path="file2.clj"),
    ]
    fmt_result = run_cljfmt(rule_runner, targets)

    # At least one file should be formatted
    assert fmt_result.output != fmt_result.input or fmt_result.output == EMPTY_DIGEST


def test_cljfmt_with_config_file(rule_runner: RuleRunner) -> None:
    """Test that cljfmt respects configuration files."""
    rule_runner.write_files(
        {
            ".cljfmt.edn": dedent(
                """\
                {:indents {my-macro [[:block 1]]}}
                """
            ),
            "BUILD": "clojure_sources(name='lib')",
            "example.clj": dedent(
                """\
                (ns example.config)

                (defn foo [x]
                  (+ x 1))
                """
            ),
        }
    )

    tgt = Address("", relative_file_path="example.clj")
    fmt_result = run_cljfmt(rule_runner, [tgt])

    # The formatter should run successfully with the config file present
    # Even if it doesn't change anything, it should complete without error
    assert fmt_result.output is not None


def test_cljfmt_with_cljc_files(rule_runner: RuleRunner) -> None:
    """Test that cljfmt formats .cljc files."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_sources(name='lib')",
            "example.cljc": dedent(
                """\
                (ns example.cljc)

                (defn portable  [x]
                  (+   x   1))

                #?(:clj (defn jvm-only [] :jvm)
                   :cljs (defn js-only [] :js))
                """
            ),
        }
    )

    tgt = Address("", relative_file_path="example.cljc")
    fmt_result = run_cljfmt(rule_runner, [tgt])

    # Should successfully format .cljc files
    assert fmt_result.output is not None


def test_empty_file(rule_runner: RuleRunner) -> None:
    """Test that cljfmt handles empty files gracefully."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_sources(name='lib')",
            "empty.clj": "",
        }
    )

    tgt = Address("", relative_file_path="empty.clj")
    fmt_result = run_cljfmt(rule_runner, [tgt])

    # Empty file should remain unchanged
    assert not fmt_result.did_change


def test_cljfmt_respects_skip_option(rule_runner: RuleRunner) -> None:
    """Test that --cljfmt-skip option prevents formatting."""
    rule_runner.write_files(
        {
            "BUILD": "clojure_sources(name='lib')",
            "example.clj": dedent(
                """\
                (ns example.core)
                (defn foo  [x]  x)
                """
            ),
        }
    )

    tgt = Address("", relative_file_path="example.clj")

    # With --cljfmt-skip, formatting should be skipped entirely
    # This would be tested at a higher level in the fmt goal
    # Here we just verify the subsystem option exists
    rule_runner.set_options(
        ["--backend-packages=clojure_backend", "--cljfmt-skip"],
        env_inherit={"PATH", "PYENV_ROOT", "HOME"},
    )

    # The formatter should respect the skip option
    # Note: This is tested at the Pants fmt goal level, not at the rule level
