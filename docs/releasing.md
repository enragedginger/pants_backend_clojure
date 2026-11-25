# Releasing pants-backend-clojure

## Overview

Releases are managed via GitHub Actions workflows:

1. **Create a draft release** via the `Create draft release` workflow
2. **Review and finalize** the draft release on GitHub
3. **Publish to PyPI** via the `Publish to PyPI` workflow

Both workflows key off a git tag named `releases/vX.Y.Z`, so that tag must
exist on the remote *before* you run either one.

## Step-by-step

### 1. Bump the version

Update `PLUGIN_VERSION` in `pants-plugins/BUILD`:

```python
PLUGIN_VERSION = "0.2.0"
```

(The `/update-release-version` skill does this and updates the README
references for you.) Also update `CHANGELOG.md`. Commit and push to `main`,
and ensure CI passes.

### 2. Create a git tag

Tags use the `releases/vX.Y.Z` naming convention — **not** a bare `vX.Y.Z`.
Both workflows check out and download by this exact tag, so getting the name
right matters:

```bash
git tag releases/v0.2.0
git push origin releases/v0.2.0
```

### 3. Trigger the draft release workflow

1. Go to **Actions** > **Create draft release** in the GitHub repo
2. Click **Run workflow**
3. Fill in:
   - **ref**: the tag name (e.g., `releases/v0.2.0`)
   - **version**: the version string without any prefix (e.g., `0.2.0`).
     This must exactly match `PLUGIN_VERSION`, or the package step fails
     when it can't find `dist/pants_backend_clojure-<version>-...`.
4. Click **Run workflow**

Or from the CLI:

```bash
gh workflow run create-draft-release.yaml \
  -f ref=releases/v0.2.0 \
  -f version=0.2.0
```

The workflow will:
- Run the full test suite
- Build the wheel and source archive
- Attest build provenance
- Create a draft GitHub release with the artifacts attached

### 4. Review the draft release

1. Go to **Releases** in the GitHub repo
2. Find the draft release
3. Review the attached artifacts (wheel and source archive)
4. Edit the release notes as needed
5. When satisfied, click **Publish release** (this removes the draft status)

### 5. Publish to PyPI

Once the release is finalized (**no longer a draft** — the publish workflow's
validation step fails fast if the release is still a draft), trigger the
`Publish to PyPI` workflow **once**, with `publish_scope=full`.

- **release_tag**: the tag name (e.g., `releases/v0.2.0`)
- **publish_scope**:
  - `full` — uploads to TestPyPI **and then** production PyPI. The PyPI job
    only runs after the TestPyPI job succeeds, so TestPyPI still acts as the
    check before the real upload.
  - `test-only` — uploads to TestPyPI only. Do not use it as a first step
    before `full` for the same version (see below).

```bash
gh workflow run publish.yaml -f release_tag=releases/v0.2.0 -f publish_scope=full
```

**Do not run `test-only` and then `full` for the same version.** TestPyPI and
PyPI accept each file only once, and the workflow does not skip files that are
already there. A `full` run after a `test-only` run fails at its TestPyPI step,
and the PyPI job is then skipped. The same applies to re-running `full` after
the TestPyPI upload has succeeded. `test-only` is for trying out the workflow
itself with a version you do not intend to publish.

If you want to look at the artifacts before publishing, download them from the
GitHub release and check the wheel's metadata:

```bash
gh release download releases/v0.2.0 --dir /tmp/release-check --pattern 'pants_backend_clojure*'
unzip -p /tmp/release-check/*.whl '*/METADATA' | head -20
```

An upload to PyPI is permanent: a version number can never be uploaded again,
even after deleting the release there.

Publishing uses trusted publishing via the `testpypi` / `pypi` GitHub
environments, so there are no tokens to pass.

## Multi-Pants-version support

The plugin is tested against multiple Pants versions using per-version Python resolves.
The `pants_version` in `pants.toml` is set to the latest supported version (used to run
the build system itself), while the plugin code is tested against all supported versions
via parametrized resolves.

### Adding support for a new Pants version

1. Create `3rdparty/python/pants-X.YZ.txt` with `pantsbuild.pants==X.YZ.P`,
   `pantsbuild.pants.testutil==X.YZ.P` (the latest patch release, e.g. `2.33.2`), `pytest`,
   and `packaging`
2. Add a `python_requirements` target to `3rdparty/python/BUILD` with `resolve="pants-X.YZ"`
3. Add `"pants-X.YZ" = "3rdparty/python/pants-X.YZ.lock"` to `[python.resolves]` in `pants.toml`
4. Add `**parametrize("pants-X.YZ", resolve="pants-X.YZ")` to all parametrized targets in
   `pants-plugins/BUILD`
5. Generate the lock file: `pants generate-lockfiles --resolve=pants-X.YZ`
6. Bump `pants_version` and `default_resolve` in `pants.toml` to the new version
7. Run tests, fix any API incompatibilities
8. If the new version needs a different Python, also update `[python] interpreter_constraints`
   in `pants.toml`, `python_requires` and the Python classifier in `pants-plugins/BUILD`,
   `target-version` in `pyproject.toml`, and `actions/setup-python` plus the `gha-cache-key`
   in `.github/workflows/ci.yaml` and `create-draft-release.yaml`

### Preview resolves for unreleased Pants versions

A resolve can track a release candidate (currently `pants-2.34`, pinned to `2.34.0rc0`) so
that upcoming API changes show up in CI early. For a preview resolve, skip step 6 above:
`pants_version` and `default_resolve` stay on the latest stable version, and the
`python_distribution` stays where it is (on the lowest supported resolve). Re-pin the resolve to the final release when it ships.

The Pants version that runs this repo launches tests with its own bundled pytest, so a
resolve for a *newer* Pants may need a pytest cap to stay compatible with it. The
`pants-2.34` requirements pin `pytest<9` for this reason (Pants 2.33 bundles pytest 8; without
the cap the resolve locks pytest 9 and every test fails at startup with
`module '_pytest.config' has no attribute '_console_main'`). Pants 2.34 still bundles
pytest 8, so keep the cap until the `pants_version` in `pants.toml` is a release whose
default `[pytest]` requirements allow pytest 9.

### Dropping support for an old Pants version

1. Remove the resolve from `[python.resolves]` in `pants.toml`
2. Remove the requirements file (`3rdparty/python/pants-X.YZ.txt`) and lock file
3. Remove the `python_requirements` target from `3rdparty/python/BUILD`
4. Remove the `**parametrize("pants-X.YZ", ...)` entry from all targets in `pants-plugins/BUILD`
5. Clean up any version-conditional code that's no longer needed
6. If the dropped version was used by `python_distribution`, update it to a new lower bound
   (and `python_requires` and the classifiers, if the supported Python changed)

### Python interpreter for the resolves

Pants 2.32 and newer run on CPython 3.14, and their `pantsbuild.pants` wheels require exactly
3.14, so the global `[python] interpreter_constraints` is `["==3.14.*"]`. Pants 2.30 and 2.31 ran
on CPython 3.11 and are no longer supported by this repo; a resolve for them would need its
own interpreter constraints.

- **Use the regular (GIL) build of 3.14.** The Pants 2.32 and 2.33 wheels are `cp314` only.
  If a free-threaded interpreter (`3.14t`) is also installed, Pex may select it and fail with
  "Failed to resolve compatible artifacts ... cp314-cp314t". Work around it with a local,
  gitignored `.pants.rc` that narrows the interpreter search path:

  ```toml
  [python-bootstrap]
  search_path = ["/path/to/a/regular/python3.14/bin"]
  ```

- Pants 2.34 publishes both `cp314` and `cp314t` wheels, so for a 2.34 resolve a
  free-threaded pick no longer fails and would go unnoticed. If it matters, check which
  interpreter the resolve's tests ran on (for example by printing `sys._is_gil_enabled()`
  from a test).
- Separately, Pants 2.34 itself runs on the free-threaded interpreter by default, so the
  plugin is loaded under `3.14t` in any repo that sets `pants_version` to 2.34 or newer.
- The `pants` launcher (scie-pants) must be 0.13.2 or newer for Pants 2.32 and 2.33, and
  0.13.4 or newer for 2.34. CI installs the latest launcher.

### Relationship between `pants_version` and supported versions

- `pants_version` (in `pants.toml`): The version of Pants used to run the build system
  (lint, test, package). Set to the latest supported version.
- Supported versions (resolves): The versions the plugin is tested against. The wheel is
  built using the lowest supported version's resolve, with `pantsbuild.pants` excluded
  from `install_requires`.

## Version conventions

- Release versions: `0.2.0`, `1.0.0`
- Pre-release versions: `0.2.0rc1`, `0.2.0dev1`
  - Versions containing "dev" or "rc" are automatically marked as pre-releases on GitHub
