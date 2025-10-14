---

## Conclusion

This plan provides a comprehensive roadmap for building a Pants plugin for Clojure that solves your specific requirements:

### Key Takeaways

1. **Start In-Repo** - Build as in-repo plugin first, extract to PyPI later (optional)

2. **Reuse JVM Infrastructure** - Leverage existing `jvm_artifact`, `JvmResolveField`, JDK management, Coursier

3. **Minimal New Code** - Only add Clojure-specific features (`clojure_sources`, `clojure_tests`, compilation, test runner)

4. **Multiple Resolves Solve Your Problem** - Test project A against both B's and C's environments with conflicting dependencies

5. **Iterate Through Phases** - Each phase builds on previous, testable immediately in your monorepo

6. **No Timeline Pressure** - Work at your own pace, using features as they're completed

### Your Specific Use Case

This plugin will let you:
- ✅ Define projects A, B, C with `clojure_sources` targets
- ✅ Use different JDK versions (11 vs 17) per project
- ✅ Use different Clojure versions (1.11.1 vs 1.12.0) via `jvm_artifact`
- ✅ Use different third-party library versions (2.0.0 vs 3.5.0) via resolves
- ✅ Test A against both B's and C's classpaths independently
- ✅ Get automatic caching, dependency inference, and all Pants benefits

### Getting Started Today

```bash
# 1. Create plugin directory
mkdir -p pants-plugins/clojure_backend

# 2. Update pants.toml
# Add: pythonpath = ["pants-plugins"]
# Add: backend_packages = ["clojure_backend"]

# 3. Start with Phase 1
# Follow the step-by-step guide in "Getting Started: Your First Steps"

# 4. Join Pants Slack for help
# https://www.pantsbuild.org/docs/getting-help
```

Good luck building your Clojure backend! The Pants community is very helpful if you get stuck.## Resources & Community

### Essential Documentation
- **Pants Docs**: https://www.pantsbuild.org/docs - Start here
- **Plugin Writing Guide**: https://www.pantsbuild.org/docs/writing-plugins - Core concepts
- **Rules API**: https://www.pantsbuild.org/docs/rules-api - How to write rules
- **Target API**: https://www.pantsbuild.org/docs/target-api - How to define targets

### Source Code to Study
- **Scala Backend**: https://github.com/pantsbuild/pants/tree/main/src/python/pants/backend/scala
  - Your primary reference for JVM language integration
- **Java Backend**: https://github.com/pantsbuild/pants/tree/main/src/python/pants/backend/java
  - Good for understanding JVM fundamentals
- **Python Backend**: https://github.com/pantsbuild/pants/tree/main/src/python/pants/backend/python
  - Reference for dependency inference patterns
- **JVM Common**: https://github.com/pantsbuild/pants/tree/main/src/python/pants/jvm
  - Core JVM utilities you'll reuse

### Community & Support
- **Pants Slack**: https://www.pantsbuild.org/docs/getting-help
  - Very active, helpful community
  - Ask questions in #plugins channel
  - Core maintainers often respond
- **GitHub Issues**: https://github.com/pantsbuild/pants/issues
  - Report bugs, request features
  - Search for similar issues first
- **Pants Blog**: https://www.pantsbuild.org/blog
  - Plugin development examples
  - Best practices articles

### Clojure-Specific Resources
- **Clojure CLI Docs**: https://clojure.org/reference/deps_and_cli
  - Understanding tools.deps
- **Coursier**: https://get-coursier.io/
  - How dependency resolution works
- **clojure.test**: https://clojure.github.io/clojure/clojure.test-api.html
  - Test framework API

### Learning Path
1. **Week 1**: Read Pants plugin docs thoroughly
2. **Week 1-2**: Clone Pants, read Scala backend code
3. **Week 2-3**: Set up in-repo plugin, get target types working
4. **Week 3+**: Iterate through phases, ask questions on Slack

### Getting Help
When asking for help (Slack, GitHub):
1. **Describe what you're trying to do** - "I'm building a Clojure backend..."
2. **Show what you've tried** - Code snippets, error messages
3. **Minimal reproduction** - Simplest example that shows the problem
4. **Pants version** - `pants --version`

The Pants community is very welcoming to plugin authors!## Timeline Summary

| Phase | Key Deliverables |
|-------|------------------|
| 1. Foundation | In-repo plugin setup, study JVM backend |
| 2. Target Types | `clojure_sources`, `clojure_tests` targets |# Comprehensive Plan: Building a Pants Plugin for Clojure

## Executive Summary

This plan outlines the development of `pants-backend-clojure`, a custom Pants build system plugin to support Clojure in monorepos with advanced dependency resolution capabilities. The plugin will handle your specific use case: multiple projects (A, B, C) with conflicting dependencies, different JDK versions, and different Clojure versions, while testing project A against both B's and C's runtime environments.

## Project Goals

1. **Primary Goal**: Enable Clojure development in Pants with first-class support for:
   - Multiple JDK versions per target
   - Multiple Clojure versions per target
   - Multiple dependency resolves (similar to Pants' JVM backend)
   - Testing libraries against different dependency contexts

2. **Secondary Goals**:
   - REPL support
   - AOT compilation when needed
   - Integration with clojure.test
   - Dependency inference from namespace declarations

## Repository Structure (In-Repo Plugin Approach)

**Phase 1: Start with your actual monorepo**

```
my-clojure-monorepo/                # Your actual project
├── pants.toml
├── BUILD
├── pants-plugins/                  # In-repo plugin location
│   ├── BUILD
│   └── clojure_backend/
│       ├── __init__.py
│       ├── register.py             # Plugin entry point
│       ├── target_types.py         # clojure_library, clojure_test, etc.
│       ├── subsystems/
│       │   ├── __init__.py
│       │   ├── clojure.py          # Clojure tool subsystem
│       │   └── clojure_infer.py    # Dependency inference
│       ├── compile/
│       │   ├── __init__.py
│       │   └── compile.py          # Compilation rules
│       ├── test/
│       │   ├── __init__.py
│       │   └── test_runner.py      # Test execution rules
│       ├── repl/
│       │   ├── __init__.py
│       │   └── repl.py             # REPL support
│       └── util/
│           ├── __init__.py
│           └── deps_parsing.py     # deps.edn parsing utilities
├── project_a/                      # Your actual Clojure projects
│   ├── BUILD
│   ├── deps.edn
│   ├── src/
│   └── test/
├── project_b/
│   ├── BUILD
│   ├── deps.edn
│   ├── src/
│   └── test/
└── project_c/
    ├── BUILD
    ├── deps.edn
    ├── src/
    └── test/
```

**Phase 2 (Later): Extract to separate repo for publishing**

```
pants-backend-clojure/              # Separate repo when ready to publish
├── README.md
├── LICENSE
├── pyproject.toml
├── setup.py
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── release.yml
├── pants_backend_clojure/          # Same code, different location
│   ├── __init__.py
│   ├── register.py
│   └── ... (same structure as pants-plugins/clojure_backend/)
├── tests/
│   └── pants_backend_clojure/
└── examples/
    └── clojure_monorepo/
```

## Phase 1: Foundation Setup (Week 1)

### 1.1 In-Repo Plugin Setup

**Tasks:**
- Add `pants-plugins/` directory to your existing Clojure monorepo
- Configure Pants to load plugins from this directory
- Set up basic plugin structure

**Create pants-plugins/ structure:**

```bash
mkdir -p pants-plugins/clojure_backend/{subsystems,compile,test,repl,util}
touch pants-plugins/clojure_backend/__init__.py
touch pants-plugins/clojure_backend/register.py
touch pants-plugins/clojure_backend/target_types.py
# etc.
```

**Configure pants.toml:**

```toml
[GLOBAL]
pants_version = "2.26.1"

# Point to your in-repo plugin
pythonpath = ["pants-plugins"]

backend_packages = [
    # Enable plugin development support
    "pants.backend.plugin_development",
    "pants.backend.python",
    
    # Load your custom Clojure backend
    "clojure_backend",  # This loads pants-plugins/clojure_backend/register.py
]

[source]
root_patterns = [
    "pants-plugins",  # Treat plugins as source root
    "project_a",
    "project_b",
    "project_c",
]
```

**Create pants-plugins/BUILD:**

```python
# Tell Pants about dependencies for plugin development
pants_requirements(name="pants")
```

**Benefits of In-Repo Plugin:**
1. ✅ **Instant iteration** - Edit plugin code, immediately test in your real projects
2. ✅ **No packaging overhead** - No need to build/publish/reinstall after each change
3. ✅ **Real-world testing** - Develop against actual use cases from day one
4. ✅ **Simpler setup** - Just one repo to manage initially
5. ✅ **Easy debugging** - Can add print statements, breakpoints in plugin code

### 1.2 Study Reference Implementations

(Same as before - clone Pants repo and study Scala/Java backends)

**Primary References:**
1. **Scala Backend** - Most relevant as it's a JVM language with its own build ecosystem
   - Path: `src/python/pants/backend/scala/`
   - Key learnings: JVM toolchain integration, multiple resolves, compilation

2. **Java Backend** - For JVM fundamentals
   - Path: `src/python/pants/backend/java/`
   - Key learnings: JDK version handling, classpath construction

3. **Python Backend** - For dependency inference patterns
   - Path: `src/python/pants/backend/python/`
   - Key learnings: Dependency inference, testing integration

**Study Checklist:**
- [ ] Clone Pants repository: `git clone https://github.com/pantsbuild/pants.git`
- [ ] Read Scala backend's register.py
- [ ] Study Scala target types
- [ ] Understand JVM subsystem and toolchain usage
- [ ] Review Scala's dependency inference implementation
- [ ] Study how Scala handles multiple resolves/lockfiles

## Phase 2: Core Target Types

### 2.1 Define Basic Target Types

**Key Insight:** Reuse existing JVM infrastructure! Pants already has:
- ✅ `jvm_artifact` - for third-party dependencies (Maven coords)
- ✅ `java_library` - for JVM code with dependencies
- ✅ JVM resolve system - for multiple dependency contexts

**We only need Clojure-specific targets:**
- `clojure_source` - single Clojure file
- `clojure_sources` - collection of Clojure files (library)
- `clojure_tests` - Clojure test files

Create `pants-plugins/clojure_backend/target_types.py`:

```python
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    MultipleSourcesField,
    SingleSourceField,
    Target,
    TriBoolField,
)
from pants.jvm.target_types import JvmResolveField, JvmJdkField


class ClojureSourceField(SingleSourceField):
    """A single Clojure source file (.clj, .cljc)."""
    expected_file_extensions = (".clj", ".cljc")


class ClojureSourcesField(MultipleSourcesField):
    """Multiple Clojure source files (.clj, .cljc)."""
    expected_file_extensions = (".clj", ".cljc")
    default = ("*.clj", "*.cljc")


class ClojureTestSourcesField(MultipleSourcesField):
    """Clojure test files."""
    expected_file_extensions = (".clj", ".cljc")
    default = ("*_test.clj", "*_test.cljc", "test_*.clj", "test_*.cljc")


class ClojureDependenciesField(Dependencies):
    """Dependencies for Clojure targets - can include jvm_artifact, java_library, etc."""
    pass


class AotCompileField(TriBoolField):
    """Whether to AOT compile this target."""
    alias = "aot"
    default = False
    help = "Whether to perform ahead-of-time compilation."


# Single Clojure source file
class ClojureSourceTarget(Target):
    """A single Clojure source file."""
    
    alias = "clojure_source"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ClojureDependenciesField,
        ClojureSourceField,
        JvmResolveField,      # Reuse from JVM backend!
        JvmJdkField,          # Reuse from JVM backend!
        AotCompileField,
    )
    help = "A single Clojure source file."


# Collection of Clojure sources (library)
class ClojureSourcesTarget(Target):
    """A collection of Clojure source files (library)."""
    
    alias = "clojure_sources"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ClojureDependenciesField,
        ClojureSourcesField,
        JvmResolveField,      # Reuse from JVM backend!
        JvmJdkField,          # Reuse from JVM backend!
        AotCompileField,
    )
    help = "A collection of Clojure source files."


# Clojure tests
class ClojureTestsTarget(Target):
    """Clojure tests."""
    
    alias = "clojure_tests"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        ClojureDependenciesField,
        ClojureTestSourcesField,
        JvmResolveField,      # Reuse from JVM backend!
        JvmJdkField,          # Reuse from JVM backend!
    )
    help = "Clojure tests using clojure.test."


# NOTE: We don't need clojure_artifact - use jvm_artifact instead!
```

### 2.2 Register Plugin

Create `pants-plugins/clojure_backend/register.py`:

```python
from clojure_backend.target_types import (
    ClojureSourceTarget,
    ClojureSourcesTarget,
    ClojureTestsTarget,
)


def target_types():
    """Register target types with Pants."""
    return [
        ClojureSourceTarget,
        ClojureSourcesTarget,
        ClojureTestsTarget,
    ]


def rules():
    """Register rules with Pants."""
    # Will be populated as we add subsystems and rules
    return []
```

### 2.3 Testing Initial Setup

**In your actual monorepo:**

**project_a/BUILD:**
```python
# Use clojure_sources for your library code
clojure_sources(
    name="lib",
    sources=["src/**/*.clj"],
    dependencies=[
        # Can depend on jvm_artifact directly!
        "//3rdparty/jvm:clojure",
        "//3rdparty/jvm:some-library",
    ],
)

clojure_tests(
    name="tests",
    sources=["test/**/*.clj"],
    dependencies=[":lib"],
)
```

**3rdparty/jvm/BUILD:**
```python
# Reuse existing jvm_artifact targets!
jvm_artifact(
    name="clojure",
    group="org.clojure",
    artifact="clojure",
    version="1.11.1",
    resolve="default",
)

jvm_artifact(
    name="some-library",
    group="com.example",
    artifact="some-library",
    version="2.0.0",
    resolve="project-b-deps",
)

jvm_artifact(
    name="some-library-v3",
    group="com.example",
    artifact="some-library",
    version="3.5.0",
    resolve="project-c-deps",
)
```

**Test commands:**
```bash
# From your monorepo root
pants list ::
pants help clojure_sources
pants help clojure_tests

# See your actual projects
pants list project_a::
pants list project_b::
```

**Immediate benefits:**
- Leverage existing JVM dependency resolution
- No need to reimplement Maven/Coursier integration
- Multi-resolve support comes for free
- Test plugin changes instantly without reinstalling
- Work with your real code structure

## Phase 3: Clojure Toolchain & Subsystem

### 3.1 Clojure Subsystem

Create `pants-plugins/clojure_backend/subsystems/clojure.py`:

```python
from dataclasses import dataclass
from typing import ClassVar

from pants.core.util_rules.external_tool import (
    TemplatedExternalTool,
)
from pants.option.option_types import ArgsListOption, StrOption


class ClojureCLI(TemplatedExternalTool):
    """The Clojure CLI tool (tools.deps)."""
    
    options_scope = "clojure-cli"
    name = "ClojureCLI"
    help = "The Clojure CLI tool for dependency resolution and execution."
    
    default_version = "1.12.0.1479"
    default_known_versions = [
        "1.12.0.1479|macos_arm64|...|...",  # Fill with actual hashes
        "1.12.0.1479|macos_x86_64|...|...",
        "1.12.0.1479|linux_x86_64|...|...",
    ]
    default_url_template = (
        "https://download.clojure.org/install/clojure-tools-{version}.tar.gz"
    )
    default_url_platform_mapping = {
        "macos_arm64": "macos",
        "macos_x86_64": "macos", 
        "linux_x86_64": "linux",
    }


@dataclass(frozen=True)
class ClojureSubsystem:
    """Configuration for Clojure compilation and execution."""
    
    # JVM options for Clojure processes
    jvm_options: tuple[str, ...]


class ClojureSubsystemOptions:
    """Options for the Clojure subsystem."""
    
    options_scope = "clojure"
    help = "Options for Clojure compilation and execution."
    
    jvm_options = ArgsListOption(
        default=[],
        help="JVM options to pass to Clojure processes.",
    )
```

### 3.2 JDK Management

**Key Insight:** Reuse Pants' existing JVM subsystem! It already handles:
- ✅ Multiple JDK versions via Coursier
- ✅ JDK downloads and caching
- ✅ Per-target JDK selection via `JvmJdkField`

```python
# In your Clojure rules, just import and use:
from pants.jvm.resolve.coursier_fetch import CoursierResolvedLockfile
from pants.jvm.resolve.key import CoursierResolveKey
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.compile import ClasspathEntry

# The JvmSubsystem already does everything you need!
```

### 3.3 Dependency Resolution Strategy

**Key Decision: Reuse JVM Multiple Resolves**

Pants' JVM backend already supports multiple resolves - just use it!

**Configuration in pants.toml:**
```toml
[jvm]
default_resolve = "jvm-default"

[jvm.resolves]
jvm-default = "3rdparty/jvm/default.lock"
project-b-deps = "3rdparty/jvm/project-b.lock"
project-c-deps = "3rdparty/jvm/project-c.lock"

# Map resolves to JDK versions
[jvm.resolves_to_jdk]
jvm-default = "11"
project-b-deps = "11"
project-c-deps = "17"
```

**Example BUILD file usage:**
```python
# Project A - will be tested against both resolves
clojure_sources(
    name="lib",
    sources=["src/**/*.clj"],
    dependencies=[
        "//3rdparty/jvm:clojure",  # jvm_artifact
    ],
)

# Tests for A using B's dependencies
clojure_tests(
    name="tests-b-context",
    sources=["test/**/*.clj"],
    dependencies=[
        ":lib",
        "//3rdparty/jvm:library-v2",
    ],
    resolve="project-b-deps",
    jdk="11",
)

# Tests for A using C's dependencies
clojure_tests(
    name="tests-c-context",
    sources=["test/**/*.clj"],
    dependencies=[
        ":lib",
        "//3rdparty/jvm:library-v3",
    ],
    resolve="project-c-deps",
    jdk="17",
)

# Project B
clojure_sources(
    name="project-b",
    sources=["src/**/*.clj"],
    dependencies=[
        "//project_a:lib",
        "//3rdparty/jvm:library-v2",
        "//3rdparty/jvm:clojure-1-11",
    ],
    resolve="project-b-deps",
    jdk="11",
)

# Project C  
clojure_sources(
    name="project-c",
    sources=["src/**/*.clj"],
    dependencies=[
        "//project_a:lib",
        "//3rdparty/jvm:library-v3",
        "//3rdparty/jvm:clojure-1-12",
    ],
    resolve="project-c-deps",
    jdk="17",
)
```

**In 3rdparty/jvm/BUILD:**
```python
# Different Clojure versions for different resolves
jvm_artifact(
    name="clojure-1-11",
    group="org.clojure",
    artifact="clojure",
    version="1.11.1",
    resolve="project-b-deps",
)

jvm_artifact(
    name="clojure-1-12",
    group="org.clojure",
    artifact="clojure",
    version="1.12.0",
    resolve="project-c-deps",
)

# Different library versions
jvm_artifact(
    name="library-v2",
    group="com.example",
    artifact="some-library",
    version="2.0.0",
    resolve="project-b-deps",
)

jvm_artifact(
    name="library-v3",
    group="com.example",
    artifact="some-library",
    version="3.5.0",
    resolve="project-c-deps",
)
```

## Phase 4: Dependency Resolution

### 4.1 deps.edn Parsing (Optional)

**Key Insight:** You might not even need to parse deps.edn files!

Since you're using `jvm_artifact` targets directly in BUILD files, dependency resolution is already handled by Pants' existing JVM backend via Coursier.

**Option 1: Pure BUILD files (Recommended)**
```python
# 3rdparty/jvm/BUILD
jvm_artifact(
    name="clojure",
    group="org.clojure",
    artifact="clojure",
    version="1.11.1",
)

jvm_artifact(
    name="some-lib",
    group="com.example",
    artifact="some-lib",
    version="2.0.0",
)
```

**Option 2: Generate BUILD from deps.edn (Optional Enhancement)**

If you want to auto-generate `jvm_artifact` targets from existing `deps.edn` files:

Create `pants-plugins/clojure_backend/util/deps_parsing.py`:

```python
import json
from dataclasses import dataclass
from typing import Any

from pants.util.frozendict import FrozenDict


@dataclass(frozen=True)
class DepsEdn:
    """Parsed deps.edn file."""
    
    paths: tuple[str, ...]
    deps: FrozenDict[str, Any]  # Maps lib name to coordinate
    aliases: FrozenDict[str, Any]
    mvn_repos: FrozenDict[str, str]
    
    @classmethod
    def parse(cls, content: str) -> "DepsEdn":
        """Parse a deps.edn file (EDN format)."""
        # Use a Python EDN library like 'edn_format'
        import edn_format
        data = edn_format.loads(content)
        
        return cls(
            paths=tuple(data.get("paths", [])),
            deps=FrozenDict(data.get("deps", {})),
            aliases=FrozenDict(data.get("aliases", {})),
            mvn_repos=FrozenDict(data.get("mvn/repos", {})),
        )


def generate_jvm_artifacts_from_deps_edn(deps_edn: DepsEdn) -> str:
    """Generate BUILD file content with jvm_artifact targets."""
    
    build_content = []
    for lib_name, coord in deps_edn.deps.items():
        # Parse Maven coordinate
        group, artifact = lib_name.split('/')
        version = coord.get('mvn/version')
        
        build_content.append(f"""
jvm_artifact(
    name="{artifact}",
    group="{group}",
    artifact="{artifact}",
    version="{version}",
)
""")
    
    return "\n".join(build_content)
```

### 4.2 Reuse Coursier Integration

**No new code needed!** Pants' JVM backend already:
- ✅ Uses Coursier for Maven resolution
- ✅ Generates lockfiles per resolve
- ✅ Handles transitive dependencies
- ✅ Caches downloaded artifacts

Just use the existing workflow:

```bash
# Generate lockfiles for your resolves
pants generate-lockfiles --resolve=project-b-deps
pants generate-lockfiles --resolve=project-c-deps
```

This creates:
- `3rdparty/jvm/project-b.lock` - pinned deps for project B
- `3rdparty/jvm/project-c.lock` - pinned deps for project C

### 4.3 Lockfile Usage

Lockfiles are automatically used by Pants when building classpaths. Each target uses the lockfile specified by its `resolve` field.

**Benefits:**
- ✅ Reproducible builds
- ✅ Conflict isolation between resolves
- ✅ Explicit about versions
- ✅ Works with CI/CD

## Phase 5: Compilation Rules

### 5.1 Basic Compilation

Create `pants-plugins/clojure_backend/compile/compile.py`:

```python
from dataclasses import dataclass

from pants.engine.fs import Digest, MergeDigests
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, collect_rules, rule
from pants.jvm.resolve.coursier_fetch import CoursierResolvedLockfile
from pants.jvm.resolve.key import CoursierResolveKey
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.compile import ClasspathEntry  # Reuse JVM classpath utilities!

from clojure_backend.target_types import (
    ClojureSourcesField,
    JvmResolveField,
    JvmJdkField,
)


@dataclass(frozen=True)
class ClojureCompileRequest:
    """Request to compile Clojure source files."""
    
    component: "ClojureFieldSet"
    
    
@dataclass(frozen=True) 
class CompiledClojure:
    """Result of compiling Clojure sources."""
    
    output_digest: Digest
    classpath_entries: tuple[ClasspathEntry, ...]  # Reuse JVM type!


@rule
async def compile_clojure_sources(
    request: ClojureCompileRequest,
    jvm: JvmSubsystem,  # Use existing JVM subsystem!
) -> CompiledClojure:
    """Compile Clojure sources to bytecode."""
    
    # 1. Get the source files
    sources_digest = request.component.sources.snapshot.digest
    
    # 2. Resolve dependencies using existing JVM resolution
    resolve = request.component.resolve.value or jvm.default_resolve
    lockfile = await Get(
        CoursierResolvedLockfile,
        CoursierResolveKey(name=resolve),  # Reuse JVM resolve key!
    )
    
    # 3. Build classpath from lockfile (reuse JVM classpath building)
    classpath_entries = await build_classpath_from_lockfile(lockfile)
    classpath_strings = [entry.path for entry in classpath_entries]
    
    # 4. Get appropriate JDK (reuse JVM JDK management)
    jdk_version = request.component.jdk.value or jvm.default_jdk
    jdk = await get_jdk(jdk_version, jvm)
    
    # 5. If AOT compilation requested, run Clojure compiler
    if request.component.aot.value:
        # Create process to run: java -cp <classpath> clojure.main -e "(compile 'ns)"
        compile_process = Process(
            argv=[
                str(jdk.java_home / "bin/java"),
                "-cp", ":".join(classpath_strings),
                "clojure.main",
                "-e", f"(compile '{get_namespace(request.component)})",
            ],
            input_digest=sources_digest,
            description=f"Compile Clojure sources: {request.component}",
        )
        
        result = await Get(ProcessResult, Process, compile_process)
        output_digest = result.output_digest
    else:
        # No AOT - just return sources as part of classpath
        output_digest = sources_digest
    
    return CompiledClojure(
        output_digest=output_digest,
        classpath_entries=classpath_entries,
    )


def rules():
    return collect_rules()
```

### 5.2 Classpath Construction

**Key Insight:** Reuse JVM backend's classpath construction!

The JVM backend already provides utilities for building classpaths:
- `ClasspathEntry` - represents a JAR or directory on classpath
- Classpath building from lockfiles
- Transitive dependency resolution

Study `pants/jvm/compile.py` and `pants/jvm/classpath.py` for patterns to follow.

Key challenge: Build proper classpath for each target based on:
- Its resolve (uses correct lockfile)
- Transitive dependencies (follow dependency graph)
- Source roots
- Compiled outputs (for AOT)

The good news: JVM backend already solves this for Java/Scala!

## Phase 6: Test Execution

### 6.1 Test Runner Implementation

Create `pants-plugins/clojure_backend/test/test_runner.py`:

```python
from dataclasses import dataclass

from pants.core.goals.test import TestRequest, TestResult
from pants.engine.fs import Digest
from pants.engine.process import Process, FallibleProcessResult
from pants.engine.rules import Get, collect_rules, rule
from pants.jvm.subsystems import JvmSubsystem

from clojure_backend.target_types import ClojureTestsTarget


@dataclass(frozen=True)
class ClojureTestFieldSet:
    """Field set for running Clojure tests."""
    
    required_fields = (ClojureTestSourcesField,)
    
    sources: ClojureTestSourcesField
    dependencies: ClojureDependenciesField
    resolve: JvmResolveField  # Reuse!
    jdk: JvmJdkField  # Reuse!


class ClojureTestRequest(TestRequest):
    """Request to run Clojure tests."""
    
    field_set_type = ClojureTestFieldSet
    

@rule
async def run_clojure_tests(
    request: ClojureTestRequest,
    jvm: JvmSubsystem,  # Reuse!
) -> TestResult:
    """Execute Clojure tests using clojure.test."""
    
    field_set = request.field_set
    
    # 1. Compile sources if needed
    compiled = await Get(CompiledClojure, ClojureCompileRequest(field_set))
    
    # 2. Build full classpath including test deps (reuse JVM utilities)
    test_classpath = await build_test_classpath(field_set)
    
    # 3. Discover test namespaces
    test_namespaces = await discover_test_namespaces(field_set)
    
    # 4. Run tests with proper JDK (reuse JVM JDK management)
    jdk_version = field_set.jdk.value or jvm.default_jdk
    jdk = await get_jdk(jdk_version, jvm)
    
    test_process = Process(
        argv=[
            str(jdk.java_home / "bin/java"),
            "-cp", ":".join(test_classpath),
            "clojure.main",
            "-m", "clojure.test",
            *test_namespaces,
        ],
        input_digest=compiled.output_digest,
        description=f"Run Clojure tests: {field_set}",
    )
    
    result = await Get(FallibleProcessResult, Process, test_process)
    
    return TestResult.from_fallible_process_result(
        result,
        addresses=(field_set.address,),
    )


def rules():
    return [
        *collect_rules(),
        ClojureTestRequest.rules(),
    ]
```

### 6.2 Test Discovery

Implement namespace discovery from test files:

```python
async def discover_test_namespaces(field_set) -> list[str]:
    """Find all test namespaces from source files."""
    
    # Read test files
    sources = await Get(Snapshot, PathGlobs, field_set.sources)
    
    namespaces = []
    for file_path in sources.files:
        # Parse (ns ...) form to get namespace
        content = await read_file_content(file_path)
        ns = extract_namespace(content)
        if ns:
            namespaces.append(ns)
    
    return namespaces


def extract_namespace(clj_content: str) -> str | None:
    """Extract namespace from (ns ...) form."""
    # Simple regex or proper parsing
    import re
    match = re.search(r'\(ns\s+([\w\.\-]+)', clj_content)
    return match.group(1) if match else None
```

## Phase 7: Dependency Inference

### 7.1 Infer Dependencies from Requires

Create `pants-plugins/clojure_backend/subsystems/clojure_infer.py`:

```python
from pants.engine.rules import collect_rules, rule
from pants.engine.target import InferredDependencies, InferDependenciesRequest
from pants.engine.unions import UnionRule

from clojure_backend.target_types import (
    ClojureDependenciesField,
    ClojureSourcesField,
)


class InferClojureDependencies(InferDependenciesRequest):
    """Infer dependencies from require/import forms."""
    
    infer_from = ClojureDependenciesField


@rule
async def infer_clojure_dependencies(
    request: InferClojureDependencies,
) -> InferredDependencies:
    """Infer Clojure dependencies from (:require ...) forms."""
    
    # 1. Read source files
    sources = await Get(Snapshot, PathGlobs, request.sources_field)
    
    # 2. Parse each file for requires
    required_namespaces = set()
    for file_path in sources.files:
        content = await read_file_content(file_path)
        ns_requires = parse_requires(content)
        required_namespaces.update(ns_requires)
    
    # 3. Map namespaces to targets
    inferred_targets = []
    for ns in required_namespaces:
        target_addr = await resolve_namespace_to_target(ns)
        if target_addr:
            inferred_targets.append(target_addr)
    
    return InferredDependencies(inferred_targets)


def parse_requires(clj_content: str) -> set[str]:
    """Extract required namespaces from (:require ...) forms."""
    # Parse the (ns ...) form's :require section
    # Example:
    # (ns myapp.core
    #   (:require [clojure.string :as str]
    #             [myapp.util :as util]))
    # 
    # Should extract: {"clojure.string", "myapp.util"}
    
    import re
    
    # Find the ns form
    ns_match = re.search(r'\(ns\s+[\w\.\-]+\s+(.*?)\)', clj_content, re.DOTALL)
    if not ns_match:
        return set()
    
    ns_body = ns_match.group(1)
    
    # Find :require section
    require_match = re.search(r':require\s+\[(.*?)\]', ns_body, re.DOTALL)
    if not require_match:
        return set()
    
    require_body = require_match.group(1)
    
    # Extract namespace names (simple approach)
    namespaces = set()
    for match in re.finditer(r'\[([\w\.\-]+)', require_body):
        namespaces.add(match.group(1))
    
    return namespaces


def rules():
    return [
        *collect_rules(),
        UnionRule(InferDependenciesRequest, InferClojureDependencies),
    ]
```

### 7.2 Namespace to Target Mapping

Build a mapping of Clojure namespaces to targets:

```python
@dataclass(frozen=True)
class ClojureNamespaceMapping:
    """Mapping of namespaces to targets."""
    
    namespace_to_target: FrozenDict[str, Address]


@rule
async def build_namespace_mapping() -> ClojureNamespaceMapping:
    """Build a global mapping of namespaces to targets."""
    
    # Get all Clojure targets
    all_targets = await Get(Targets, AddressSpecs([DescendantAddresses("")]))
    
    mapping = {}
    for target in all_targets:
        if not target.has_field(ClojureSourcesField):
            continue
            
        # Parse namespace from sources
        sources = await Get(Snapshot, PathGlobs, target[ClojureSourcesField])
        for file_path in sources.files:
            content = await read_file_content(file_path)
            ns = extract_namespace(content)
            if ns:
                mapping[ns] = target.address
    
    return ClojureNamespaceMapping(FrozenDict(mapping))
```

## Phase 8: REPL Support

### 8.1 Basic REPL

Create `pants-plugins/clojure_backend/repl/repl.py`:

```python
from pants.core.goals.repl import ReplImplementation, ReplRequest
from pants.engine.rules import Get, collect_rules, rule
from pants.jvm.subsystems import JvmSubsystem


class ClojureRepl(ReplImplementation):
    """Clojure REPL implementation."""
    
    name = "clojure"
    

@rule
async def create_clojure_repl(
    request: ReplRequest,
    jvm: JvmSubsystem,  # Reuse!
) -> ClojureRepl:
    """Start a Clojure REPL with appropriate classpath."""
    
    # 1. Determine resolve from targets
    resolve = determine_resolve_from_targets(request.targets)
    
    # 2. Build classpath (reuse JVM utilities)
    classpath = await build_classpath_for_repl(request, resolve)
    
    # 3. Get appropriate JDK (reuse JVM management)
    jdk_version = determine_jdk_from_targets(request.targets)
    jdk = await get_jdk(jdk_version, jvm)
    
    # 4. Start REPL process
    repl_process = InteractiveProcess(
        argv=[
            str(jdk.java_home / "bin/java"),
            "-cp", ":".join(classpath),
            "clojure.main",  # This starts a Clojure REPL
        ],
    )
    
    return ClojureRepl(repl_process)


def rules():
    return collect_rules()
```

**Usage:**
```bash
# Start REPL with specific target's classpath
pants repl project_a/src/main.clj

# Start REPL with all dependencies from project_a
pants repl project_a::
```

## Phase 9: Polish & Documentation

### 9.1 Documentation

Create comprehensive documentation:

**README.md in pants-plugins/clojure_backend/:**
```markdown
# Clojure Backend for Pants

In-repo Pants plugin for Clojure support.

## Setup

1. Enable in `pants.toml`:
```toml
[GLOBAL]
pythonpath = ["pants-plugins"]
backend_packages = ["clojure_backend"]
```

2. Add JVM backend (required):
```toml
backend_packages = [
    "pants.backend.experimental.java",  # For jvm_artifact
    "clojure_backend",
]
```

## Target Types

### `clojure_sources`
A collection of Clojure source files.

### `clojure_tests`
Clojure test files using clojure.test.

## BUILD File Examples

See project_a/BUILD, project_b/BUILD, project_c/BUILD for examples.

## Multiple Resolves

Use JVM resolves to isolate conflicting dependencies:

```toml
[jvm.resolves]
project-b-deps = "3rdparty/jvm/project-b.lock"
project-c-deps = "3rdparty/jvm/project-c.lock"
```

Then specify resolve per target:
```python
clojure_sources(
    name="lib",
    resolve="project-b-deps",
)
```
```

**docs/resolves.md:**
- Detailed explanation of multiple resolves
- How to handle conflicting dependencies
- Testing against multiple contexts

**docs/targets.md:**
- Complete reference for all target types
- Field descriptions
- Examples

### 9.2 Example Projects

Your actual projects (A, B, C) serve as examples! Document them:

**project_a/README.md:**
```markdown
# Project A

This project is tested against both project B and project C environments.

See BUILD file for how we use multiple test targets with different resolves.
```

### 9.3 Testing Infrastructure

**Unit Tests:**
Create `pants-plugins/clojure_backend/tests/`:

```python
# test_target_types.py
def test_clojure_sources_target():
    """Test clojure_sources target definition."""
    # ...

# test_namespace_parsing.py  
def test_extract_namespace():
    """Test namespace extraction from Clojure files."""
    content = "(ns myapp.core (:require [clojure.string]))"
    assert extract_namespace(content) == "myapp.core"

# test_dependency_inference.py
def test_infer_from_requires():
    """Test dependency inference from :require forms."""
    # ...
```

**Integration Tests:**
Use your actual projects as integration tests:

```bash
# Test that everything works end-to-end
pants test project_a::
pants test project_b::
pants test project_c::

# Test multiple resolve scenario
pants test project_a:tests-b-context
pants test project_a:tests-c-context
```

## Phase 10: Publishing (When Ready - Later)

### 10.0 When to Extract and Publish

**Extract to separate repo when:**
- ✅ Plugin works well for your use cases
- ✅ Basic features are stable (compile, test, REPL)
- ✅ You want to share with other teams/projects
- ✅ You need version control independent of your monorepo

**Until then, keep it in-repo!**

### 10.1 Extract to Separate Repository

**Steps:**
1. Create new repo: `pants-backend-clojure`
2. Copy `pants-plugins/clojure_backend/` → `src/pants_backend_clojure/`
3. Add packaging files (setup.py, pyproject.toml)
4. Set up tests, CI/CD
5. Add examples directory with sample projects

**Migration path for users:**

Before (in-repo):
```toml
# pants.toml
pythonpath = ["pants-plugins"]
backend_packages = ["clojure_backend"]
```

After (published):
```toml
# pants.toml
[GLOBAL]
plugins = ["pants-backend-clojure==0.1.0"]
backend_packages = ["pants_backend_clojure"]
```

### 10.2 Prepare for PyPI

**setup.py:**
```python
from setuptools import setup, find_packages

setup(
    name="pants-backend-clojure",
    version="0.1.0",
    author="Your Name",
    author_email="your.email@example.com",
    description="Clojure backend for Pants build system",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/pants-backend-clojure",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3.11",
        "Topic :: Software Development :: Build Tools",
    ],
    python_requires=">=3.11",
    install_requires=[
        "pantsbuild.pants>=2.26.0,<2.27.0",
        "edn-format>=0.7.0",  # For parsing deps.edn
    ],
)
```

**pyproject.toml:**
```toml
[build-system]
requires = ["setuptools>=45", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "pants-backend-clojure"
version = "0.1.0"
description = "Clojure backend for the Pants build system"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "pantsbuild.pants>=2.26.0,<2.27.0",
    "edn-format>=0.7.0",
]
```

### 10.3 Release Process

1. **Version bump** in `setup.py` and `pyproject.toml`
2. **Tag release** in git: `git tag v0.1.0`
3. **Build package**: `python -m build`
4. **Upload to PyPI**: `twine upload dist/*`

**.github/workflows/release.yml:**
```yaml
name: Release

on:
  push:
    tags:
      - 'v*'

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Build package
        run: |
          pip install build
          python -m build
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          password: ${{ secrets.PYPI_API_TOKEN }}
```

## Key Technical Decisions

### 1. Reuse JVM Infrastructure ✅

**Decision:** Leverage Pants' existing JVM subsystem maximally.

**What we reuse:**
- ✅ `jvm_artifact` targets for third-party dependencies
- ✅ `JvmResolveField` for resolve selection
- ✅ `JvmJdkField` for JDK version selection
- ✅ JVM subsystem for JDK management
- ✅ Coursier for dependency resolution
- ✅ JVM lockfile system
- ✅ `ClasspathEntry` for classpath building
- ✅ `java_library` targets can be dependencies

**What we add:**
- `clojure_source` - single Clojure file target
- `clojure_sources` - collection of Clojure files
- `clojure_tests` - test files
- Clojure compilation rules
- Clojure test runner
- Namespace-based dependency inference

**Rationale:** Don't reinvent the wheel. The JVM backend already solves multi-JDK, dependency resolution, and remote caching. We just add Clojure-specific features on top.

### 2. Multiple Resolves Strategy ✅

**Decision:** Use JVM backend's existing multiple resolve support.

**How it works:**
Each resolve is completely isolated:
- Own lockfile (e.g., `project-b.lock`, `project-c.lock`)
- Own JDK version
- Own set of dependencies
- Own classpath

**Example:**
```toml
# pants.toml
[jvm.resolves]
project-b-deps = "3rdparty/jvm/project-b.lock"
project-c-deps = "3rdparty/jvm/project-c.lock"

[jvm.resolves_to_jdk]
project-b-deps = "11"
project-c-deps = "17"
```

```python
# Project A tested against B's context
clojure_tests(
    name="tests-b",
    resolve="project-b-deps",  # Uses B's lockfile + JDK 11
)

# Project A tested against C's context
clojure_tests(
    name="tests-c",
    resolve="project-c-deps",  # Uses C's lockfile + JDK 17
)
```

**Rationale:** This directly solves your requirement - testing project A against both B's and C's runtime environments with conflicting dependencies.

### 3. No Custom Dependency Format

**Decision:** Use `jvm_artifact` targets in BUILD files, not deps.edn parsing (initially).

**Rationale:**
- Simpler implementation
- Reuses existing Coursier integration
- deps.edn parsing can be added later as enhancement
- BUILD files are already the Pants idiom

**Optional Enhancement:** Add a goal to generate `jvm_artifact` targets from deps.edn files.

### 4. Minimal Target Types

**Decision:** Only create Clojure-specific targets, reuse everything else from JVM backend.

**Clojure-specific:**
- `clojure_source` / `clojure_sources` - for source files
- `clojure_tests` - for test files

**Reused from JVM backend:**
- `jvm_artifact` - for third-party deps
- `java_library` - can be Clojure dependencies
- All JVM configuration options

**Rationale:** Minimal surface area = less code to maintain, fewer bugs, easier to understand.

### 5. Test Framework

**Decision:** Support clojure.test initially, add others later.

**Rationale:**
- clojure.test is built-in, most common
- Simple namespace-based discovery
- Can add support for other frameworks (test.check, Midje) incrementally

### 6. Compilation Strategy

**Decision:** Support both non-AOT (source on classpath) and AOT (compiled bytecode) via `aot` field.

**Default:** No AOT compilation (faster, more flexible)
**Optional:** AOT via `aot=True` on targets that need it

**Rationale:**
- Most Clojure code doesn't need AOT
- AOT is slower, creates more artifacts
- Some scenarios require AOT (gen-class, performance-critical code)

## Timeline Summary

| Phase | Key Deliverables |
|-------|------------------|
| 1. Foundation | In-repo plugin setup, study JVM backend |
| 2. Target Types | `clojure_sources`, `clojure_tests` targets |
| 3. Toolchain | Clojure subsystem, reuse JVM/JDK management |
| 4. Dependency Resolution | Reuse JVM resolves, lockfiles, Coursier |
| 5. Compilation | Compilation rules, classpath building |
| 6. Testing | Test runner, namespace discovery |
| 7. Inference | Dependency inference from `:require` |
| 8. REPL | Basic REPL support |
| 9. Polish | Documentation, tests, refinement |
| 10. Extract & Publish | Separate repo, PyPI (optional, later) |

**Key Point:** Work through phases iteratively. Each phase builds on the previous and can be tested immediately in your real monorepo.

## Success Criteria

Your plugin is successful when you can:

1. ✅ **Define three projects (A, B, C) in your monorepo**
   ```python
   # project_a/BUILD
   clojure_sources(name="lib", ...)
   
   # project_b/BUILD
   clojure_sources(name="lib", dependencies=["//project_a:lib"], ...)
   
   # project_c/BUILD
   clojure_sources(name="lib", dependencies=["//project_a:lib"], ...)
   ```

2. ✅ **B and C use different dependency versions**
   ```python
   # 3rdparty/jvm/BUILD
   jvm_artifact(
       name="lib-v2",
       artifact="com.example:some-library",
       version="2.0.0",
       resolve="project-b-deps",
   )
   
   jvm_artifact(
       name="lib-v3",
       artifact="com.example:some-library",
       version="3.5.0",
       resolve="project-c-deps",
   )
   ```

3. ✅ **B and C use different JDKs and Clojure versions**
   ```toml
   # pants.toml
   [jvm.resolves_to_jdk]
   project-b-deps = "11"
   project-c-deps = "17"
   ```
   
   ```python
   # 3rdparty/jvm/BUILD
   jvm_artifact(
       name="clojure-1-11",
       artifact="org.clojure:clojure",
       version="1.11.1",
       resolve="project-b-deps",
   )
   
   jvm_artifact(
       name="clojure-1-12",
       artifact="org.clojure:clojure",
       version="1.12.0",
       resolve="project-c-deps",
   )
   ```

4. ✅ **Test A against both B's and C's contexts**
   ```python
   # project_a/BUILD
   clojure_tests(
       name="tests-b-context",
       sources=["test/**/*.clj"],
       dependencies=[":lib"],
       resolve="project-b-deps",  # B's environment
   )
   
   clojure_tests(
       name="tests-c-context",
       sources=["test/**/*.clj"],
       dependencies=[":lib"],
       resolve="project-c-deps",  # C's environment
   )
   ```
   
   ```bash
   # Run tests in both contexts
   pants test project_a:tests-b-context
   pants test project_a:tests-c-context
   ```

5. ✅ **Pants caches correctly**
   ```bash
   # First run compiles and tests
   pants test project_a::
   # Second run uses cache (instant)
   pants test project_a::
   ```

6. ✅ **Dependency inference works**
   ```clojure
   ;; project_a/src/main.clj
   (ns project-a.main
     (:require [project-a.util :as util]))  ; Auto-inferred dependency
   ```
   
   ```bash
   pants dependencies project_a/src/main.clj
   # Shows: project_a:util (inferred from :require)
   ```

7. ✅ **REPL works with correct classpath**
   ```bash
   pants repl project_a::
   # Starts REPL with all project_a dependencies
   # Can require and use all project_a namespaces
   ```

8. ✅ **All standard Pants commands work**
   ```bash
   pants list ::                    # List all targets
   pants list project_a::          # List project_a targets
   pants dependencies project_a::  # Show dependencies
   pants dependents project_a:lib  # What depends on project_a?
   pants filedeps project_a::      # Show file dependencies
   pants tailor ::                 # Generate BUILD files
   ```

## Getting Started: Your First Steps

### Step 1: Set Up In-Repo Plugin Structure

```bash
# In your monorepo root
mkdir -p pants-plugins/clojure_backend/{subsystems,compile,test,repl,util}
cd pants-plugins/clojure_backend

# Create __init__.py files
touch __init__.py
touch subsystems/__init__.py
touch compile/__init__.py
touch test/__init__.py
touch repl/__init__.py
touch util/__init__.py

# Create main files
touch register.py
touch target_types.py
```

### Step 2: Configure pants.toml

```toml
[GLOBAL]
pants_version = "2.26.1"

# Point to your in-repo plugin
pythonpath = ["pants-plugins"]

backend_packages = [
    # Required: JVM backend for jvm_artifact
    "pants.backend.experimental.java",
    
    # Enable plugin development
    "pants.backend.plugin_development",
    "pants.backend.python",
    
    # Your Clojure backend
    "clojure_backend",
]

[source]
root_patterns = [
    "pants-plugins",
    "project_a",
    "project_b",
    "project_c",
]

[jvm]
default_resolve = "jvm-default"

[jvm.resolves]
jvm-default = "3rdparty/jvm/default.lock"
project-b-deps = "3rdparty/jvm/project-b.lock"
project-c-deps = "3rdparty/jvm/project-c.lock"

[jvm.resolves_to_jdk]
jvm-default = "11"
project-b-deps = "11"
project-c-deps = "17"
```

### Step 3: Create Minimal Plugin (Phase 1)

**pants-plugins/clojure_backend/register.py:**
```python
"""Clojure backend for Pants."""

def target_types():
    """Register target types."""
    # Start empty, will add in Phase 2
    return []

def rules():
    """Register rules."""
    # Start empty, will add in later phases
    return []
```

### Step 4: Test Plugin Loads

```bash
# From monorepo root
pants help

# Should see no errors
# Plugin is loaded but does nothing yet
```

### Step 5: Add Target Types (Phase 2)

Copy the target type definitions from Phase 2 into `target_types.py`, then update `register.py`:

```python
from clojure_backend.target_types import (
    ClojureSourceTarget,
    ClojureSourcesTarget,
    ClojureTestsTarget,
)

def target_types():
    return [
        ClojureSourceTarget,
        ClojureSourcesTarget,
        ClojureTestsTarget,
    ]
```

Test:
```bash
pants help clojure_sources
# Should show help for your new target type!
```

### Step 6: Create First BUILD File

**project_a/BUILD:**
```python
clojure_sources(
    name="lib",
    sources=["src/**/*.clj"],
)
```

Test:
```bash
pants list project_a::
# Should show: project_a:lib
```

### Step 7: Continue Through Phases

Work through phases 3-9, testing each feature as you add it:

1. **Phase 3**: Add Clojure subsystem, verify JVM integration works
2. **Phase 4**: Set up jvm_artifact targets, generate lockfiles
3. **Phase 5**: Implement compilation, test with `pants compile project_a::`
4. **Phase 6**: Implement test runner, test with `pants test project_a::`
5. **Phase 7**: Add dependency inference, verify with `pants dependencies`
6. **Phase 8**: Add REPL support, test with `pants repl project_a::`
7. **Phase 9**: Write docs, add tests, refine

## Common Patterns to Study from Scala Backend

As you implement each phase, study these specific files in the Pants source code:

### Target Types
- `pants/backend/scala/target_types.py` - How Scala defines targets
- Look for: `ScalaSourceTarget`, field definitions, how they use `JvmResolveField`

### Compilation
- `pants/backend/scala/compile/scalac.py` - Scala compilation
- Key learnings: How to build classpaths, invoke compiler, handle outputs

### Test Execution
- `pants/backend/scala/test/scalatest.py` - ScalaTest runner
- Key learnings: Test discovery, running tests with proper classpath

### Dependency Inference
- `pants/backend/scala/dependency_inference/` - Scala's import inference
- Key learnings: Parsing source files, mapping imports to targets

### JVM Integration Points
```python
# These are your friends - study how they're used:
from pants.jvm.resolve.coursier_fetch import CoursierResolvedLockfile
from pants.jvm.resolve.key import CoursierResolveKey
from pants.jvm.subsystems import JvmSubsystem
from pants.jvm.compile import ClasspathEntry
from pants.jvm.target_types import JvmResolveField, JvmJdkField
```

### Rule Patterns
Study how Scala backend writes rules:
```python
@rule
async def some_rule(
    request: SomeRequest,
    jvm: JvmSubsystem,  # Get subsystem
) -> SomeResult:
    # 1. Get inputs using await Get(...)
    something = await Get(Output, Input, input_value)
    
    # 2. Do work
    result = process_something(something)
    
    # 3. Return result
    return SomeResult(result)
```

### Process Execution
Study how to run external processes:
```python
process = Process(
    argv=[str(java_binary), "-cp", classpath, "clojure.main", ...],
    input_digest=sources_digest,
    description="Compile Clojure sources",
)
result = await Get(ProcessResult, Process, process)
```

## Troubleshooting & Tips

### Plugin Not Loading
**Problem:** `pants help` shows error about clojure_backend

**Solutions:**
- Check `pythonpath = ["pants-plugins"]` in pants.toml
- Verify `register.py` exists with `target_types()` and `rules()` functions
- Ensure all `__init__.py` files exist
- Check Python syntax errors in plugin code

### Target Type Not Found
**Problem:** `Unknown target type: clojure_sources`

**Solutions:**
- Verify target is returned from `target_types()` in register.py
- Check target's `alias` field matches what you're using in BUILD
- Restart Pants: `pants --version` to reload plugins

### Cannot Import JVM Types
**Problem:** `ImportError: cannot import name 'JvmResolveField'`

**Solutions:**
- Ensure JVM backend is enabled: `"pants.backend.experimental.java"` in backend_packages
- Check Pants version compatibility (need 2.20+)
- Correct import path: `from pants.jvm.target_types import JvmResolveField`

### Compilation Fails
**Problem:** `java: command not found` or classpath issues

**Solutions:**
- Verify JDK is specified correctly in resolve config
- Check lockfile exists: `pants generate-lockfiles --resolve=your-resolve`
- Ensure Clojure is in dependencies (jvm_artifact with org.clojure:clojure)
- Check classpath is built correctly (add debug logging)

### Tests Not Running
**Problem:** `pants test` finds no tests

**Solutions:**
- Verify test file patterns match: `*_test.clj`, `test_*.clj`
- Check test namespace naming conventions
- Ensure test files have proper `(ns ...)` declarations
- Verify ClojureTestFieldSet.required_fields matches your target

### Dependency Inference Not Working
**Problem:** Dependencies not auto-detected from `:require`

**Solutions:**
- Check namespace parsing regex in `parse_requires()`
- Verify namespace-to-target mapping is built correctly
- Ensure InferDependenciesRequest is registered as UnionRule
- Add debug logging to see what namespaces are found

### Caching Issues
**Problem:** Changes not picked up, or cache always invalidates

**Solutions:**
- Pants caching is automatic if you use the engine correctly
- Don't read files directly - always use `await Get(Digest, ...)`
- Ensure all inputs are captured in request types
- Use `pants --no-local-cache` to debug cache issues

### Performance Problems
**Problem:** Slow compilation or test execution

**Solutions:**
- Check if you're rebuilding classpaths unnecessarily
- Verify lockfiles are being used (not resolving on every build)
- Consider enabling remote caching
- Profile with `pants --stats-log`

### General Debugging Tips

1. **Add logging:**
```python
import logging
logger = logging.getLogger(__name__)

@rule
async def my_rule(request: MyRequest) -> MyResult:
    logger.debug(f"Processing {request}")
    # ...
```

2. **Use pants in verbose mode:**
```bash
pants --level=debug compile project_a::
```

3. **Check rule graph:**
```bash
pants peek project_a:lib
```

4. **Inspect classpaths:**
```bash
pants dependencies --transitive project_a:lib
```

5. **Test isolation:**
Test each feature in isolation before integrating

## Future Enhancements (Post-MVP)

Once your core plugin is working, consider these enhancements:

### 1. ClojureScript Support
Add target types for ClojureScript:
```python
clojurescript_sources(
    name="frontend",
    sources=["src/**/*.cljs"],
)
```
Challenges: Different compiler, different output (JavaScript), npm dependencies

### 2. deps.edn Auto-Import
Generate `jvm_artifact` targets from existing deps.edn files:
```bash
pants generate-jvm-artifacts-from-deps project_a/deps.edn
```

### 3. nREPL Support
Enhanced REPL with nREPL protocol:
- Better editor integration
- Remote REPL connections
- Middleware support

### 4. Linting Integration
Add support for Clojure linters:
- clj-kondo
- eastwood
- kibit

### 5. Formatting
Add formatting support:
- cljfmt
- zprint

### 6. Advanced Test Features
- Parallel test execution
- Test selection by namespace pattern
- Test coverage reporting
- Integration with test.check (property-based testing)

### 7. Build Performance
- Incremental compilation improvements
- Better caching strategies
- Parallel compilation of independent namespaces

### 8. IDE Integration
- Generate IntelliJ/Cursive project files
- VS Code/Calva integration
- LSP server integration

### 9. Deployment
- Uberjar generation
- Docker image creation
- AWS Lambda packaging

### 10. Advanced Dependency Features
- Automatic conflict resolution suggestions
- Dependency graph visualization
- Security vulnerability scanning