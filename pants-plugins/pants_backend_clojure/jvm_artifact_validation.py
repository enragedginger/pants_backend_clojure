"""Validate local `jar=` paths before invoking Coursier.

Coursier silently fails when a `jvm_artifact` `jar=` file is missing, which
surfaces inside pants as a cryptic `IndexError` from
`pants/jvm/resolve/coursier_fetch.py` (it indexes into the fetch report that
was never produced). This helper catches the problem upfront with a precise
error naming the missing path, avoiding a broad `IndexError` catch at the
call site.
"""

from __future__ import annotations

import os

from pants.base.glob_match_error_behavior import GlobMatchErrorBehavior
from pants.engine.fs import PathGlobs
from pants.engine.intrinsics import path_globs_to_digest
from pants.jvm.dependency_inference.artifact_mapper import AllJvmArtifactTargets
from pants.jvm.target_types import JvmArtifactJarSourceField


async def validate_local_jar_paths(targets: AllJvmArtifactTargets) -> None:
    jar_paths = [
        os.path.join(tgt.address.spec_path, jar_value) for tgt in targets if (jar_value := tgt.get(JvmArtifactJarSourceField).value)
    ]
    if not jar_paths:
        return

    await path_globs_to_digest(
        PathGlobs(
            jar_paths,
            glob_match_error_behavior=GlobMatchErrorBehavior.error,
            description_of_origin="the `jar=` field of `jvm_artifact` targets",
        ),
    )
