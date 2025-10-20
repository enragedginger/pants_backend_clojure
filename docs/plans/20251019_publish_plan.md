# Publishing Plan: pants-backend-clojure to PyPI
## Deep Analysis & Implementation Strategy

**Date:** October 19, 2025
**Goal:** Publish the Clojure Pants backend to PyPI for use in other Pants projects until it's merged into Pants core
**Current State:** 3,143 LOC, 15 Python modules, Pants 2.28.0, Python 3.11

---

## Executive Summary

This plan covers publishing a production-ready Pants plugin to PyPI. The plugin is currently structured as an in-repo plugin and needs restructuring for PyPI distribution while maintaining backwards compatibility for existing projects.

**Key Challenges:**
1. Restructuring from in-repo to standalone package
2. Ensuring compatibility across Pants versions (2.20.0 - 2.28.0+)
3. Testing the plugin without breaking existing projects
4. Managing versioning and updates
5. Zero-downtime migration path

**Estimated Timeline:** 10-16 hours (including thorough testing)

---

## Phase 0: Pre-Flight Analysis

### Current State Assessment

**Strengths:**
- ✅ No external dependencies beyond Pants
- ✅ Well-structured with clear separation (subsystems, rules, targets)
- ✅ Already follows Pants plugin conventions (register.py with target_types() and rules())
- ✅ Currently works with Pants 2.28.0
- ✅ ~3,100 lines of production code

**Risks:**
- ⚠️ Import path changes could break during migration
- ⚠️ Testing Pants plugins is non-trivial (requires real Pants environment)
- ⚠️ Pants plugin API could change between versions
- ⚠️ No existing unit tests to verify correctness during migration
- ⚠️ Multiple projects currently depend on in-repo version

### Compatibility Analysis

**Pants Version Support Strategy:**

```
Minimum: 2.20.0  (first version with stable JVM backend)
Tested:  2.28.0  (current version in use)
Target:  2.20.0 - 2.29.x (next release)
Maximum: <3.0    (conservative upper bound)
```

**Why 2.20.0?**
- Stable JVM backend APIs
- `pants.backend.experimental.java` available
- Coursier support mature
- Plugin development APIs stable

**Testing Matrix:**
| Pants Version | Python Version | Priority | Notes |
|--------------|---------------|----------|-------|
| 2.20.0 | 3.9 | High | Minimum supported |
| 2.24.0 | 3.10 | Medium | Mid-range |
| 2.28.0 | 3.11 | High | Current production |
| 2.29.x | 3.12 | Medium | Latest stable |

---

## Phase 1: Repository Structure Design

### Decision: Monorepo vs Separate Repo

**Option A: Extract to Separate Repository (Recommended)**
```
pants-backend-clojure/  (new repo)
├── .github/
│   └── workflows/
│       ├── ci.yml
│       ├── release.yml
│       └── test-compatibility.yml
├── src/
│   └── pants_backend_clojure/
│       ├── __init__.py
│       ├── register.py
│       ├── target_types.py
│       ├── compile_clj.py
│       ├── clj_test_runner.py
│       ├── aot_compile.py
│       ├── clj_lint.py
│       ├── clj_fmt.py
│       ├── clj_repl.py
│       ├── dependency_inference.py
│       ├── generate_deps_edn.py
│       ├── package_clojure_deploy_jar.py
│       └── subsystems/
│           ├── __init__.py
│           ├── clj_kondo.py
│           └── cljfmt.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_target_types.py
│   ├── test_namespace_parsing.py
│   ├── test_dependency_inference.py
│   ├── test_aot_compilation.py
│   └── integration/
│       ├── simple_project/
│       ├── multi_resolve_project/
│       └── deploy_jar_project/
├── examples/
│   ├── README.md
│   ├── simple-library/
│   ├── multi-project/
│   └── deploy-jar/
├── docs/
│   ├── README.md
│   ├── installation.md
│   ├── configuration.md
│   ├── resolves.md
│   ├── repl.md
│   └── migration.md
├── pyproject.toml
├── README.md
├── LICENSE
├── CHANGELOG.md
├── .gitignore
└── .python-version
```

**Pros:**
- Clean separation from your projects
- Independent CI/CD
- Easier to share and maintain
- Standard PyPI package structure
- Can version independently

**Cons:**
- Need to set up new repo
- Lose git history (unless explicitly preserved)
- Need to maintain two repos during transition

**Option B: Keep in Monorepo with PyPI Publishing**

Keep current structure but add publishing tooling. Not recommended because:
- Confusing for contributors
- Harder to version
- Mixed concerns (your projects + plugin)

**Recommendation: Option A**

### Package Naming Strategy

**PyPI Package Name:** `pants-backend-clojure`
- Follows convention: lowercase, hyphens
- Clear, searchable
- Namespace: third-party backend

**Python Module Name:** `pants_backend_clojure`
- Python-compatible: underscores
- Import: `import pants_backend_clojure`
- Matches: `pants.backend.python` naming pattern

**Pants Backend Name:** `pants_backend_clojure`
```toml
[GLOBAL]
backend_packages = ["pants_backend_clojure"]
```

**Critical:** This is a breaking change from current `clojure_backend`

---

## Phase 2: Package Configuration

### Modern pyproject.toml (Comprehensive)

```toml
[build-system]
requires = ["hatchling>=1.21.0"]
build-backend = "hatchling.build"

[project]
name = "pants-backend-clojure"
dynamic = ["version"]
description = "A Pants backend for Clojure: compilation, testing, REPL, linting, formatting, and packaging"
readme = "README.md"
license = "Apache-2.0"
license-files = { paths = ["LICENSE"] }
authors = [
    { name = "Your Name", email = "your.email@example.com" },
]
maintainers = [
    { name = "Your Name", email = "your.email@example.com" },
]
keywords = [
    "pantsbuild",
    "pants",
    "clojure",
    "build",
    "backend",
    "jvm",
    "leiningen",
    "tools.deps",
]
classifiers = [
    "Development Status :: 4 - Beta",
    "Framework :: Pants",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: Apache Software License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.9",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Software Development :: Build Tools",
    "Topic :: Software Development :: Compilers",
    "Topic :: Software Development :: Testing",
]
requires-python = ">=3.9"
dependencies = [
    # Match the Pants version range you've tested against
    "pantsbuild.pants>=2.20.0,<3.0",
]

[project.urls]
Homepage = "https://github.com/yourusername/pants-backend-clojure"
Documentation = "https://github.com/yourusername/pants-backend-clojure/blob/main/README.md"
Repository = "https://github.com/yourusername/pants-backend-clojure"
Issues = "https://github.com/yourusername/pants-backend-clojure/issues"
Changelog = "https://github.com/yourusername/pants-backend-clojure/blob/main/CHANGELOG.md"

[project.optional-dependencies]
# Development dependencies
dev = [
    "pytest>=7.4.0",
    "pytest-xdist>=3.3.0",  # Parallel test execution
    "mypy>=1.7.0",
    "ruff>=0.1.6",
]

[tool.hatch.version]
# Single source of truth for version
path = "src/pants_backend_clojure/__init__.py"

[tool.hatch.build]
# Only include necessary files in distributions
include = [
    "/src/pants_backend_clojure",
    "/README.md",
    "/LICENSE",
]
exclude = [
    "*.pyc",
    "__pycache__",
    "*.so",
    "*.dylib",
]

[tool.hatch.build.targets.wheel]
packages = ["src/pants_backend_clojure"]

[tool.hatch.build.targets.sdist]
include = [
    "/src",
    "/tests",
    "/README.md",
    "/LICENSE",
    "/CHANGELOG.md",
    "/pyproject.toml",
]

# Testing configuration
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "-v",
    "--strict-markers",
    "--strict-config",
    "-ra",  # Show summary of all test outcomes
]

# Linting configuration
[tool.ruff]
line-length = 100
target-version = "py39"

[tool.ruff.lint]
select = [
    "E",   # pycodestyle errors
    "F",   # pyflakes
    "I",   # isort
    "UP",  # pyupgrade
    "B",   # flake8-bugbear
]
ignore = [
    "E501",  # Line too long (handled by formatter)
]

# Type checking configuration
[tool.mypy]
python_version = "3.9"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = false  # Pants plugin code often uses untyped
check_untyped_defs = true

[[tool.mypy.overrides]]
module = "pants.*"
ignore_missing_imports = true
```

### Key Configuration Decisions

1. **Dynamic Versioning:** Version read from `__init__.py` (single source of truth)
2. **Hatchling:** Modern, fast, PEP 517-compliant build backend
3. **Broad Python Support:** 3.9-3.12 (matches Pants requirements)
4. **Conservative Pants Version:** >=2.20.0,<3.0 (can tighten after testing)
5. **Dev Dependencies:** Testing and quality tools in optional-dependencies

---

## Phase 3: Source Code Migration

### Import Path Update Strategy

**Challenge:** 15 Python files with imports need updating

**Current Import Pattern:**
```python
from clojure_backend.target_types import ClojureSourceTarget
from clojure_backend.subsystems.clj_kondo import CljKondo
```

**New Import Pattern:**
```python
from pants_backend_clojure.target_types import ClojureSourceTarget
from pants_backend_clojure.subsystems.clj_kondo import CljKondo
```

**Automated Migration Script:**

```bash
#!/bin/bash
# migrate_imports.sh

# Find all Python files and update imports
find src/pants_backend_clojure -name "*.py" -type f -exec sed -i '' \
    's/from clojure_backend/from pants_backend_clojure/g' {} +

find src/pants_backend_clojure -name "*.py" -type f -exec sed -i '' \
    's/import clojure_backend/import pants_backend_clojure/g' {} +

echo "Import migration complete. Please review changes with: git diff"
```

**Manual Review Required:**
- Verify all imports are correct
- Check for any string literals that reference module names
- Ensure no broken imports remain

### Files Requiring Updates

**Critical Files (Must Update):**
1. `register.py` - Imports all modules
2. `compile_clj.py` - May import other modules
3. `clj_test_runner.py` - May import other modules
4. `aot_compile.py` - May import other modules
5. `clj_lint.py` - Imports subsystems
6. `clj_fmt.py` - Imports subsystems
7. `clj_repl.py` - May import other modules
8. `dependency_inference.py` - May import target types
9. `generate_deps_edn.py` - May import target types
10. `package_clojure_deploy_jar.py` - May import target types
11. `subsystems/clj_kondo.py` - Self-contained but check
12. `subsystems/cljfmt.py` - Self-contained but check

**Version Module:**

Create `src/pants_backend_clojure/__init__.py`:
```python
"""Pants backend for Clojure.

Provides first-class Clojure support for the Pants build system, including:
- Compilation (AOT and JIT)
- Testing with clojure.test
- REPL support (standard, nREPL, Rebel Readline)
- Linting with clj-kondo
- Formatting with cljfmt
- Deploy JAR (uberjar) packaging
- Automatic dependency inference
- Multiple JVM resolve support
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
```

---

## Phase 4: Testing Strategy (CRITICAL)

### Why Testing Pants Plugins is Hard

1. **Environment Dependent:** Plugins run within Pants' execution environment
2. **Rule Graph:** Rules must be registered and form valid dependency graph
3. **Subsystems:** Need proper setup and configuration
4. **Integration:** Many behaviors emerge from interaction with Pants core

### Multi-Level Testing Approach

#### Level 1: Unit Tests (Python Logic Only)

Test pure Python functions that don't depend on Pants:

```python
# tests/test_namespace_parsing.py
from pants_backend_clojure.dependency_inference import (
    extract_namespace,
    namespace_to_path,
    parse_requires,
)

def test_extract_namespace_simple():
    content = "(ns myapp.core)"
    assert extract_namespace(content) == "myapp.core"

def test_extract_namespace_with_requires():
    content = """(ns myapp.core
      (:require [clojure.string :as str]))"""
    assert extract_namespace(content) == "myapp.core"

def test_namespace_to_path():
    assert namespace_to_path("myapp.core") == "myapp/core.clj"
    assert namespace_to_path("my-app.core") == "my_app/core.clj"
    assert namespace_to_path("my_app.core") == "my_app/core.clj"

def test_parse_requires():
    content = """(ns myapp.core
      (:require
        [clojure.string :as str]
        [myapp.util :refer [helper]]
        [myapp.data]))"""
    requires = parse_requires(content)
    assert "clojure.string" in requires
    assert "myapp.util" in requires
    assert "myapp.data" in requires
```

#### Level 2: Rule Tests (Pants Test Harness)

Use Pants' `rule_runner` test harness:

```python
# tests/test_target_types.py
import pytest
from pants.testutil.rule_runner import RuleRunner

from pants_backend_clojure.target_types import (
    ClojureSourceTarget,
    ClojureTestTarget,
    rules as target_type_rules,
)

@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=target_type_rules(),
        target_types=[ClojureSourceTarget, ClojureTestTarget],
    )

def test_clojure_source_target_has_required_fields(rule_runner: RuleRunner):
    rule_runner.write_files({
        "src/BUILD": "clojure_source(name='lib', source='core.clj')",
        "src/core.clj": "(ns myapp.core)",
    })
    target = rule_runner.get_target(address="src:lib")
    assert target.alias == "clojure_source"
    assert target.residence_dir == "src"

def test_clojure_test_target_timeout_field(rule_runner: RuleRunner):
    rule_runner.write_files({
        "test/BUILD": "clojure_test(name='test', source='core_test.clj', timeout=60)",
        "test/core_test.clj": "(ns myapp.core-test)",
    })
    target = rule_runner.get_target(address="test:test")
    assert target[TestTimeoutField].value == 60
```

#### Level 3: Integration Tests (Real Projects)

Test with actual Clojure projects:

```python
# tests/integration/test_compile.py
import pytest
from pants.testutil.rule_runner import RuleRunner

from pants_backend_clojure import rules as clojure_rules

@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=clojure_rules(),
        target_types=clojure_target_types(),
    )

def test_compile_simple_namespace(rule_runner: RuleRunner):
    rule_runner.write_files({
        "src/BUILD": """
clojure_source(
    name="lib",
    source="core.clj",
    dependencies=["3rdparty/jvm:clojure"],
)
""",
        "src/core.clj": """
(ns myapp.core)

(defn hello [name]
  (str "Hello, " name "!"))
""",
        "3rdparty/jvm/BUILD": """
jvm_artifact(
    name="clojure",
    group="org.clojure",
    artifact="clojure",
    version="1.11.1",
)
""",
    })

    result = rule_runner.run_goal_rule("compile", args=["src:lib"])
    assert result.exit_code == 0
```

#### Level 4: End-to-End Tests (Full Workflow)

Test complete workflows in isolated environments:

```bash
#!/bin/bash
# tests/e2e/test_full_workflow.sh

set -e

# Create temporary directory
TMPDIR=$(mktemp -d)
cd "$TMPDIR"

# Install plugin
pip install pants-backend-clojure

# Create minimal Pants project
cat > pants.toml <<EOF
[GLOBAL]
pants_version = "2.28.0"
backend_packages = [
    "pants.backend.experimental.java",
    "pants_backend_clojure",
]
EOF

# Create project structure
mkdir -p src 3rdparty/jvm
cat > src/BUILD <<EOF
clojure_sources(name="lib", dependencies=["3rdparty/jvm:clojure"])
EOF

cat > src/core.clj <<EOF
(ns myapp.core)
(defn hello [] "Hello, World!")
EOF

cat > 3rdparty/jvm/BUILD <<EOF
jvm_artifact(
    name="clojure",
    group="org.clojure",
    artifact="clojure",
    version="1.11.1",
)
EOF

# Run Pants goals
pants compile src::
pants test src::
pants lint src::
pants fmt --check src::

echo "E2E test passed!"
cd -
rm -rf "$TMPDIR"
```

### Test Before Publishing Checklist

- [ ] Unit tests pass locally
- [ ] Rule tests pass locally
- [ ] Integration tests pass locally
- [ ] E2E tests pass in clean environment
- [ ] Test with minimum Pants version (2.20.0)
- [ ] Test with current Pants version (2.28.0)
- [ ] Test with latest Pants version
- [ ] Test on Linux (GitHub Actions)
- [ ] Test on macOS (if available)
- [ ] Test with Python 3.9, 3.10, 3.11, 3.12
- [ ] Test in project with existing in-repo plugin (migration)
- [ ] Test in fresh project (new user experience)

---

## Phase 5: Documentation

### README.md Structure

```markdown
# Pants Backend for Clojure

A production-ready Pants backend providing comprehensive Clojure support.

## Features

- **Compilation:** JIT and AOT compilation with dependency resolution
- **Testing:** clojure.test integration with parallel execution
- **REPL:** Standard, nREPL, and Rebel Readline support
- **Linting:** clj-kondo integration with config discovery
- **Formatting:** cljfmt integration
- **Packaging:** Executable uberjar (deploy JAR) generation
- **Dependency Inference:** Automatic from namespace requires
- **Multiple Resolves:** Different JVM dependencies per project
- **IDE Integration:** Generate deps.edn for Cursive/Calva

## Requirements

- Pants >= 2.20.0
- Python >= 3.9
- JDK >= 11

## Installation

Add to your `pants.toml`:

```toml
[GLOBAL]
backend_packages.add = [
  "pants.backend.experimental.java",
  "pants_backend_clojure",
]

plugins = [
  "pants-backend-clojure==0.1.0",
]
```

Then run:
```bash
pants --version  # Pants will automatically install the plugin
```

## Quick Start

[Full quick start guide with examples...]

## Documentation

- [Installation Guide](docs/installation.md)
- [Configuration](docs/configuration.md)
- [Multiple Resolves](docs/resolves.md)
- [REPL Usage](docs/repl.md)
- [Deploy JARs](docs/packaging.md)
- [Migration Guide](docs/migration.md)

## Examples

See the [examples/](examples/) directory for complete example projects.

## Compatibility

| Plugin Version | Pants Version | Python Version |
|---------------|---------------|----------------|
| 0.1.x | 2.20.0 - 2.29.x | 3.9 - 3.12 |

## Contributing

[Contributing guidelines...]

## License

Apache License 2.0 - same as Pants
```

### Additional Documentation Files

Create comprehensive guides:
- `docs/installation.md` - Detailed setup
- `docs/configuration.md` - All configuration options
- `docs/resolves.md` - Multiple resolve usage
- `docs/repl.md` - REPL workflows
- `docs/packaging.md` - Deploy JAR creation
- `docs/migration.md` - Migrating from in-repo plugin

---

## Phase 6: Building and Local Testing

### Pre-Build Checklist

- [ ] All imports updated to `pants_backend_clojure`
- [ ] Version set in `__init__.py`
- [ ] `pyproject.toml` complete and valid
- [ ] README.md written
- [ ] LICENSE file present (Apache 2.0)
- [ ] CHANGELOG.md created
- [ ] `.gitignore` includes build artifacts

### Build Process

```bash
# Install build tools
pip install build twine hatch

# Validate pyproject.toml
hatch version
# Should output: 0.1.0

# Clean previous builds
rm -rf dist/ build/ *.egg-info

# Build distributions
python -m build

# Verify build outputs
ls -lh dist/
# Should see:
# - pants_backend_clojure-0.1.0-py3-none-any.whl
# - pants-backend-clojure-0.1.0.tar.gz
```

### Inspect Built Package

```bash
# List wheel contents
unzip -l dist/pants_backend_clojure-0.1.0-py3-none-any.whl

# Expected contents:
# pants_backend_clojure/__init__.py
# pants_backend_clojure/register.py
# pants_backend_clojure/target_types.py
# pants_backend_clojure/compile_clj.py
# ... (all other modules)
# pants_backend_clojure/subsystems/__init__.py
# pants_backend_clojure/subsystems/clj_kondo.py
# pants_backend_clojure/subsystems/cljfmt.py
# pants_backend_clojure-0.1.0.dist-info/METADATA
# pants_backend_clojure-0.1.0.dist-info/LICENSE

# Verify no unwanted files (tests, .pyc, etc.)
```

### Local Installation Test

```bash
# Create clean test environment
python -m venv test-env
source test-env/bin/activate

# Install from local wheel
pip install dist/pants_backend_clojure-0.1.0-py3-none-any.whl

# Verify installation
python -c "import pants_backend_clojure; print(pants_backend_clojure.__version__)"
# Should output: 0.1.0

# Verify module structure
python -c "from pants_backend_clojure.register import target_types, rules; print(len(target_types()), len(rules()))"
# Should output target and rule counts

# Deactivate
deactivate
```

### Integration Test in Real Project

```bash
# Navigate to a test Pants project
cd /tmp/test-pants-project

# Create pants.toml
cat > pants.toml <<EOF
[GLOBAL]
pants_version = "2.28.0"
backend_packages = [
    "pants.backend.experimental.java",
    "pants_backend_clojure",
]

# Install from local wheel for testing
plugins = [
    "pants-backend-clojure @ file:///path/to/pants-backend-clojure/dist/pants_backend_clojure-0.1.0-py3-none-any.whl",
]
EOF

# Create minimal Clojure project
mkdir -p src 3rdparty/jvm

cat > src/BUILD <<EOF
clojure_sources(name="lib")
EOF

cat > src/core.clj <<EOF
(ns example.core)
(defn hello [] "Hello!")
EOF

cat > 3rdparty/jvm/BUILD <<EOF
jvm_artifact(
    name="clojure",
    group="org.clojure",
    artifact="clojure",
    version="1.11.1",
)
EOF

# Test all goals
pants compile src::
pants test src::
pants repl src::  # Interactive - exit with Ctrl+D
pants lint src::
pants fmt src::

# Success!
```

---

## Phase 7: Publishing to PyPI

### TestPyPI First (Strongly Recommended)

```bash
# Create TestPyPI account at https://test.pypi.org/account/register/

# Upload to TestPyPI
twine upload --repository testpypi dist/*
# Enter credentials when prompted

# View on TestPyPI
# https://test.pypi.org/project/pants-backend-clojure/

# Test installation from TestPyPI
python -m venv test-testpypi
source test-testpypi/bin/activate
pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    pants-backend-clojure

# The --extra-index-url is needed because pantsbuild.pants is on PyPI, not TestPyPI

# Verify
python -c "import pants_backend_clojure; print(pants_backend_clojure.__version__)"

deactivate
```

### Production PyPI

```bash
# Create PyPI account at https://pypi.org/account/register/

# Create API token:
# 1. Go to https://pypi.org/manage/account/token/
# 2. Create token with name "pants-backend-clojure"
# 3. Scope: Entire account (will narrow after first upload)
# 4. Save token securely

# Configure credentials
cat > ~/.pypirc <<EOF
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-your-api-token-here

[testpypi]
username = __token__
password = pypi-your-testpypi-token-here
EOF

chmod 600 ~/.pypirc

# Upload to PyPI
twine upload dist/*

# Verify upload
# https://pypi.org/project/pants-backend-clojure/

# Test installation
pip install pants-backend-clojure==0.1.0

# Success! Package is now public
```

### Post-Upload Checklist

- [ ] Package appears on PyPI
- [ ] README renders correctly on PyPI page
- [ ] All metadata correct (author, license, keywords)
- [ ] Can install via `pip install pants-backend-clojure`
- [ ] Can use in Pants project via `plugins = ["pants-backend-clojure==0.1.0"]`
- [ ] All documented goals work (compile, test, lint, fmt, repl, package)
- [ ] GitHub release created matching version tag
- [ ] Announcement posted to Pants Slack/community

---

## Phase 8: CI/CD Setup

### GitHub Actions: Comprehensive Testing

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    name: Test Python ${{ matrix.python-version }}, Pants ${{ matrix.pants-version }}
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.9", "3.10", "3.11", "3.12"]
        pants-version: ["2.20.0", "2.24.0", "2.28.0", "2.29.0"]
        exclude:
          # Pants 2.20-2.23 don't support Python 3.12
          - pants-version: "2.20.0"
            python-version: "3.12"
          - pants-version: "2.24.0"
            python-version: "3.12"

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Set up Java
        uses: actions/setup-java@v4
        with:
          distribution: 'temurin'
          java-version: '17'

      - name: Install dependencies
        run: |
          pip install build pytest pytest-xdist
          pip install "pantsbuild.pants==${{ matrix.pants-version }}"

      - name: Install plugin (editable)
        run: pip install -e .

      - name: Run unit tests
        run: pytest tests/ -v -n auto

      - name: Run integration tests
        run: |
          cd tests/integration/simple_project
          pants --pants-version=${{ matrix.pants-version }} test ::

  lint:
    name: Lint and Type Check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          pip install ruff mypy
          pip install -e .

      - name: Run ruff
        run: ruff check src/

      - name: Run mypy
        run: mypy src/

  build:
    name: Build Package
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install build tools
        run: pip install build

      - name: Build distributions
        run: python -m build

      - name: Check distribution
        run: |
          pip install twine
          twine check dist/*

      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: distributions
          path: dist/
```

### GitHub Actions: Automated Releases

```yaml
# .github/workflows/release.yml
name: Release to PyPI

on:
  release:
    types: [published]

jobs:
  publish:
    name: Publish to PyPI
    runs-on: ubuntu-latest
    environment: release
    permissions:
      id-token: write  # For trusted publishing

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install build tools
        run: pip install build

      - name: Build distributions
        run: python -m build

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          # Uses trusted publishing (no token needed if configured)
          # Or set: password: ${{ secrets.PYPI_API_TOKEN }}
          verbose: true
```

### Setting Up Trusted Publishing (Recommended)

1. Go to PyPI project settings
2. Navigate to Publishing
3. Add GitHub as trusted publisher:
   - Owner: `yourusername`
   - Repository: `pants-backend-clojure`
   - Workflow: `release.yml`
   - Environment: `release`

No API tokens needed!

---

## Phase 9: Version Management & Release Process

### Semantic Versioning Strategy

```
MAJOR.MINOR.PATCH

MAJOR: Breaking changes (e.g., rename targets, remove features)
MINOR: New features, backwards compatible (e.g., add new goal)
PATCH: Bug fixes, backwards compatible
```

**Examples:**
- `0.1.0` → `0.1.1`: Fix bug in dependency inference
- `0.1.1` → `0.2.0`: Add ClojureScript support
- `0.2.0` → `1.0.0`: Stable release, API freeze
- `1.0.0` → `2.0.0`: Rename `clojure_deploy_jar` → `clojure_uberjar`

### Release Checklist

**Before Release:**
- [ ] All tests passing on CI
- [ ] CHANGELOG.md updated with changes
- [ ] Version bumped in `src/pants_backend_clojure/__init__.py`
- [ ] Documentation updated
- [ ] Examples tested
- [ ] Migration guide updated (if breaking changes)

**Release Process:**
1. Create PR with version bump and changelog
2. Merge PR after approval
3. Create and push git tag:
   ```bash
   git tag -a v0.1.0 -m "Release v0.1.0"
   git push origin v0.1.0
   ```
4. Create GitHub Release:
   - Go to GitHub → Releases → New Release
   - Tag: `v0.1.0`
   - Title: `v0.1.0`
   - Description: Copy from CHANGELOG.md
5. GitHub Actions auto-publishes to PyPI
6. Verify on PyPI
7. Test installation: `pip install pants-backend-clojure==0.1.0`
8. Announce in Pants Slack #plugins channel

**After Release:**
- [ ] Update compatibility matrix in README
- [ ] Close related GitHub issues
- [ ] Update project board
- [ ] Monitor for bug reports

---

## Phase 10: Migration Path for Existing Projects

### Challenge

Your current projects use:
```toml
backend_packages = ["clojure_backend"]
pythonpath = ["%(buildroot)s/pants-plugins"]
```

New projects will use:
```toml
backend_packages = ["pants_backend_clojure"]
plugins = ["pants-backend-clojure==0.1.0"]
```

### Gradual Migration Strategy

**Step 1: Dual Installation (Temporary)**

Both can coexist during transition:

```toml
[GLOBAL]
# Old in-repo plugin (still works)
pythonpath = ["%(buildroot)s/pants-plugins"]
backend_packages = [
    "pants.backend.experimental.java",
    "clojure_backend",  # Keep temporarily
]

# New PyPI plugin (testing)
plugins = ["pants-backend-clojure==0.1.0"]
# backend_packages += ["pants_backend_clojure"]  # Add when ready to switch
```

**Step 2: Test PyPI Version**

```bash
# In separate test branch
git checkout -b test-pypi-plugin

# Update pants.toml
# Comment out old plugin, enable new one

# Test all workflows
pants compile ::
pants test ::
pants repl project_a::
pants package ::
```

**Step 3: Full Migration**

```toml
[GLOBAL]
backend_packages = [
    "pants.backend.experimental.java",
    "pants_backend_clojure",  # New!
]

plugins = [
    "pants-backend-clojure==0.1.0",
]

# Remove:
# pythonpath = ["%(buildroot)s/pants-plugins"]
# clojure_backend from backend_packages
```

**Step 4: Cleanup**

```bash
# Remove in-repo plugin
git rm -r pants-plugins/clojure_backend

# Commit
git commit -m "Migrate to pants-backend-clojure PyPI package"
```

### No BUILD File Changes Required!

Target definitions remain identical:
```python
# Works with both versions
clojure_sources(name="lib")
clojure_test(name="test", source="core_test.clj")
clojure_deploy_jar(name="app", main="myapp.main")
```

---

## Phase 11: Monitoring and Maintenance

### Tracking Plugin Usage

**PyPI Download Stats:**
- https://pypistats.org/packages/pants-backend-clojure
- Track downloads per version
- Identify most popular versions

**GitHub Metrics:**
- Stars/forks
- Issue frequency
- PR contributions
- Clone/usage stats

### Keeping Up with Pants Changes

**Monitor:**
- Pants release notes: https://www.pantsbuild.org/stable/releases
- Pants Slack #announce channel
- Pants plugin API changes

**Testing Against New Pants Versions:**

```bash
# When Pants 2.30.0 releases
pip install "pantsbuild.pants==2.30.0rc0"

# Test your plugin
cd tests/integration/simple_project
pants --pants-version=2.30.0rc0 test ::

# If broken, fix and release patch version
```

### Deprecation Strategy

When Pants core adopts Clojure support:

1. **Announce Deprecation:**
   - Update README with deprecation notice
   - Pin version: `0.x.y` is final
   - Provide migration guide to official backend

2. **Transition Period:**
   - Maintain for 6 months
   - Critical bug fixes only
   - No new features

3. **Archive:**
   - Mark repository as archived
   - Keep available on PyPI (don't delete)
   - Update README: "Use pants.backend.clojure instead"

---

## Risk Mitigation

### Risk 1: Broken Package Upload

**Mitigation:**
- Always test on TestPyPI first
- Test install in clean environment
- Run full integration test before production upload

**Rollback:**
- Can't delete from PyPI (by design)
- Can "yank" release: `twine yank pants-backend-clojure 0.1.0`
- Immediately release patch version with fix

### Risk 2: Pants API Breaking Changes

**Mitigation:**
- Test against multiple Pants versions in CI
- Monitor Pants pre-releases
- Conservative upper bound: `<3.0`

**Response:**
- Release new version compatible with new Pants
- Maintain compatibility matrix in README

### Risk 3: Import Path Confusion

**Mitigation:**
- Clear documentation
- Migration guide
- Error messages if old import used

### Risk 4: Existing Projects Break

**Mitigation:**
- Dual installation support during transition
- Comprehensive migration guide
- Keep old in-repo plugin until confirmed working

---

## Success Criteria

✅ Package published to PyPI
✅ Can install: `pip install pants-backend-clojure`
✅ Works in fresh Pants project via plugins config
✅ All goals functional: compile, test, lint, fmt, repl, package
✅ Tests pass across Pants 2.20.0 - 2.29.0
✅ Tests pass across Python 3.9 - 3.12
✅ Documentation complete and accurate
✅ Examples work
✅ Existing projects can migrate seamlessly
✅ CI/CD automated
✅ Community announcement posted

---

## Estimated Timeline (Revised)

| Phase | Task | Estimated Time |
|-------|------|----------------|
| 0 | Pre-flight analysis | 1 hour |
| 1 | Set up new repository | 1 hour |
| 2 | Create pyproject.toml, configs | 1 hour |
| 3 | Migrate source code, update imports | 2 hours |
| 4 | Write comprehensive tests | 4-6 hours |
| 5 | Write documentation | 2-3 hours |
| 6 | Build and local testing | 2 hours |
| 7 | Publish to TestPyPI + PyPI | 1 hour |
| 8 | Set up CI/CD | 2 hours |
| 9 | Test migration in existing project | 1 hour |
| 10 | Final verification and announcement | 1 hour |

**Total: 18-24 hours** (more realistic than original estimate)

**Breakdown:**
- **Minimum (experienced, everything works):** 12 hours
- **Expected (normal debugging):** 18 hours
- **Maximum (issues found, thorough testing):** 24 hours

---

## Appendix A: Recommended First Steps

```bash
# 1. Create new repository
mkdir pants-backend-clojure
cd pants-backend-clojure
git init

# 2. Set up structure
mkdir -p src/pants_backend_clojure/subsystems
mkdir -p tests/integration
mkdir -p docs examples

# 3. Copy source files
cp -r /path/to/old/pants-plugins/clojure_backend/* src/pants_backend_clojure/

# 4. Run import migration script
./migrate_imports.sh

# 5. Create pyproject.toml
# (Copy from Phase 2)

# 6. Create __init__.py with version
cat > src/pants_backend_clojure/__init__.py <<EOF
"""Pants backend for Clojure."""
__version__ = "0.1.0"
EOF

# 7. Test local install
pip install -e .
python -c "import pants_backend_clojure; print(pants_backend_clojure.__version__)"

# 8. Write tests
# (See Phase 4)

# 9. Build and test
python -m build
pip install dist/*.whl

# 10. When ready, publish
twine upload --repository testpypi dist/*
```

---

## Appendix B: Troubleshooting

### Issue: "No module named 'pants_backend_clojure'"

**Cause:** Package not installed or import path wrong

**Solution:**
```bash
pip install pants-backend-clojure
# or
pip install -e .  # for development
```

### Issue: "Rule graph error" when loading plugin

**Cause:** Rules or subsystems not properly registered

**Solution:**
- Check `register.py` returns all rules
- Verify imports are correct
- Check Pants version compatibility

### Issue: "Cannot find clojure_sources target"

**Cause:** Backend not loaded in pants.toml

**Solution:**
```toml
[GLOBAL]
backend_packages = ["pants_backend_clojure"]
```

### Issue: Package builds but doesn't include all modules

**Cause:** `pyproject.toml` package discovery misconfigured

**Solution:**
```toml
[tool.hatch.build.targets.wheel]
packages = ["src/pants_backend_clojure"]
```

---

## Appendix C: Resources

- **Pants Plugin Development:** https://www.pantsbuild.org/stable/docs/writing-plugins/overview
- **PyPI Publishing Guide:** https://packaging.python.org/en/latest/tutorials/packaging-projects/
- **Hatchling Documentation:** https://hatch.pypa.io/latest/
- **Pants Slack:** https://www.pantsbuild.org/stable/docs/getting-started/getting-help
- **Semantic Versioning:** https://semver.org/

---

## Final Recommendations

1. **Don't Rush:** Take time to test thoroughly
2. **Start with TestPyPI:** Always test there first
3. **Write Tests First:** Will save hours of debugging
4. **Document Everything:** Future you will thank present you
5. **Automate CI/CD:** Catches issues before users do
6. **Version Conservatively:** Easy to expand support, hard to narrow
7. **Communicate:** Announce in Pants community, gather feedback
8. **Keep Updated:** Monitor Pants releases for API changes

**This is a production-quality publishing process. Follow it carefully and you'll have a robust, maintainable, widely-usable Pants plugin.**
