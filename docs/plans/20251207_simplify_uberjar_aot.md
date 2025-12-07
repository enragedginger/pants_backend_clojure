# Plan: Simplify Uberjar/AOT Compilation Using tools.build

## Problem Statement

The current uberjar/AOT compilation implementation is overly complex (~1,170 lines across 4 files) with ~40% of the code dedicated to working around AOT/protocol issues:

- Custom class filtering logic (first-party vs third-party vs macro-generated)
- Source-only library detection and special handling
- Namespace-to-class-path conversions with hyphen/underscore munging
- Two-phase JAR scanning to build class indexes
- Regex-based gen-class name detection

These workarounds are fragile and continue to cause bugs (like the recent Specter protocol issue).

## Proposed Solution

Delegate AOT compilation and uberjar creation to **tools.build**, which is the official Clojure tooling and handles these complexities correctly.

**Key simplification**: Pants/Coursier already resolves all dependencies. We don't need tools.deps at all. We simply:
1. Lay out compile-time JARs in one directory
2. Lay out runtime JARs (excluding provided) in another directory
3. Pass these directories to tools.build as pre-resolved classpaths

```clojure
;; No tools.deps needed - Pants provides the classpaths
(let [compile-basis {:classpath-roots ["src" "compile-libs"]}
      uber-basis {:classpath-roots ["src" "uber-libs"]}]
  (b/compile-clj {:basis compile-basis :class-dir "classes" :ns-compile ['my.app]})
  (b/uber {:basis uber-basis :class-dir "classes" :uber-file "app.jar" :main 'my.app}))
```

## Design Principles

1. **Simple mental model**: User specifies `main` namespace, we handle the rest
2. **Default to source-only**: `main="clojure.main"` (no AOT) is the default
3. **Delegate complexity**: Let tools.build handle AOT/uberjar intricacies
4. **Leverage Pants**: Use Pants-computed classpaths directly, no tools.deps resolution
5. **Single invocation**: One tools.build call that does compile + uber together

## Target API

```python
clojure_deploy_jar(
    name="my-app",
    main="my.app.core",  # Required. Use "clojure.main" for no AOT (default)
    dependencies=[...],
    provided=["//3rdparty:servlet-api"],  # Optional: excluded from JAR
)
```

Behavior:
- If `main="clojure.main"`: Skip AOT, create source-only JAR
- Otherwise: AOT compile main namespace via tools.build, create executable JAR

---

## Phase 1: Create tools.build Integration Infrastructure

### Goal
Establish the ability to invoke tools.build from Pants, fetching it as a dependency.

### Tasks

#### 1.1 Add tools.build as a fetchable tool dependency

Create a subsystem for tools.build:

```python
# pants-plugins/clojure_backend/subsystems/tools_build.py
from pants.option.option_types import StrOption
from pants.option.subsystem import Subsystem

class ToolsBuildSubsystem(Subsystem):
    options_scope = "clojure-tools-build"
    help = "Configuration for Clojure tools.build"

    version = StrOption(
        default="0.10.11",
        help="Version of tools.build to use for AOT compilation and uberjar creation"
    )
```

**Note**: We do NOT need tools.deps. Pants handles all dependency resolution.

#### 1.2 Create a rule to fetch tools.build classpath

```python
from pants.jvm.resolve.common import ArtifactRequirement, ArtifactRequirements, Coordinate
from pants.jvm.resolve.coursier_fetch import ToolClasspath, ToolClasspathRequest

@rule
async def get_tools_build_classpath(
    tools_build: ToolsBuildSubsystem,
) -> ToolClasspath:
    return await Get(
        ToolClasspath,
        ToolClasspathRequest(
            artifact_requirements=ArtifactRequirements([
                ArtifactRequirement(Coordinate(
                    "io.github.clojure", "tools.build", tools_build.version
                )),
                # tools.build pulls in what it needs transitively
                # No need to explicitly add tools.deps
            ]),
        ),
    )
```

### Deliverables
- `tools_build.py` subsystem with version configuration
- Rule to fetch tools.build classpath

### Validation
- Unit test that tools.build can be fetched

---

## Phase 2: Implement Single tools.build Invocation

### Goal
Create a single rule that invokes tools.build to perform both AOT compilation AND uberjar creation in one step, using Pants-provided classpaths.

### Tasks

#### 2.1 Create build script generator

Generate a self-contained Clojure build script that uses pre-resolved classpaths:

```python
def generate_build_script(
    main_ns: str,
    class_dir: str = "classes",
    uber_file: str = "app.jar",
) -> str:
    """Generate build.clj that uses Pants-provided classpaths.

    This script:
    1. Uses compile-libs/ directory for AOT compilation (all deps including provided)
    2. Uses uber-libs/ directory for packaging (excludes provided deps)
    3. Only compiles the main namespace - Clojure handles transitive compilation
    4. No tools.deps resolution needed - Pants already resolved everything
    """
    return f'''
(ns build
  (:require [clojure.tools.build.api :as b]
            [clojure.java.io :as io]))

(def class-dir "{class_dir}")
(def uber-file "{uber_file}")
(def main-ns '{main_ns})

(defn list-jars [dir]
  (->> (io/file dir)
       (.listFiles)
       (filter #(.endsWith (.getName %) ".jar"))
       (map str)
       vec))

(defn uberjar [_]
  ;; Classpaths pre-resolved by Pants - no tools.deps needed
  (let [compile-cp (concat ["src"] (list-jars "compile-libs"))
        uber-cp (concat ["src"] (list-jars "uber-libs"))
        compile-basis {{:classpath-roots compile-cp}}
        uber-basis {{:classpath-roots uber-cp}}]

    ;; Clean previous output
    (b/delete {{:path class-dir}})

    ;; AOT compile main namespace (Clojure transitively compiles all required namespaces)
    (println "Compiling" (str main-ns "..."))
    (b/compile-clj {{:basis compile-basis
                     :class-dir class-dir
                     :ns-compile [main-ns]
                     :compile-opts {{:direct-linking true}}}})

    ;; Build uberjar with runtime classpath (excludes provided deps)
    (println "Building uberjar" (str uber-file "..."))
    (b/uber {{:basis uber-basis
              :class-dir class-dir
              :uber-file uber-file
              :main main-ns
              ;; Exclude LICENSE to avoid conflicts (some deps have file, others have folder)
              :exclude ["LICENSE"]}})

    ;; Clean up
    (b/delete {{:path class-dir}})
    (println "Uberjar built:" uber-file)))

;; Entry point
(uberjar nil)
(System/exit 0)
'''
```

**Key points**:
- `compile-libs/` contains ALL JARs (including provided) - for AOT compilation
- `uber-libs/` contains runtime JARs only (excluding provided) - for packaging
- No tools.deps, no deps.edn, no Maven coordinate parsing
- Pants lays out the directories, tools.build just uses them
- `:ns-compile [main-ns]` - only compiles main; Clojure handles transitive compilation
- `:exclude ["LICENSE"]` - avoids conflicts when some deps have LICENSE as file vs folder

#### 2.2 Create the uberjar rule

```python
@dataclass(frozen=True)
class ToolsBuildUberjarRequest:
    """Request to build an uberjar using tools.build."""
    main_namespace: str
    compile_classpath: Classpath  # All deps including provided (for AOT)
    runtime_classpath: Classpath  # Deps excluding provided (for JAR)
    source_digest: Digest  # Source files with stripped roots
    jdk: JvmJdkField | None = None

@dataclass(frozen=True)
class ToolsBuildUberjarResult:
    """Result of building an uberjar with tools.build."""
    digest: Digest  # Contains the uberjar
    jar_path: str   # Relative path to the JAR in the digest


@rule(desc="Build uberjar with tools.build")
async def build_uberjar_with_tools_build(
    request: ToolsBuildUberjarRequest,
    tools_build_subsystem: ToolsBuildSubsystem,
) -> ToolsBuildUberjarResult:
    # 1. Get tools.build classpath
    tools_classpath = await Get(ToolClasspath, ToolsBuildSubsystem, tools_build_subsystem)

    # 2. Get JDK
    jdk = await Get(JdkEnvironment, JdkRequest, ...)

    # 3. Generate build script
    build_script = generate_build_script(
        main_ns=request.main_namespace,
        class_dir="classes",
        uber_file="app.jar",
    )

    # 4. Create working directory structure
    # Structure:
    #   build.clj           <- Generated build script
    #   src/                <- Source files (namespace structure preserved)
    #   compile-libs/       <- All JARs including provided (for AOT)
    #   uber-libs/          <- Runtime JARs excluding provided (for packaging)

    build_script_digest = await Get(Digest, CreateDigest([
        FileContent("build.clj", build_script.encode()),
    ]))

    # Put sources under src/
    src_digest = await Get(Digest, AddPrefix(request.source_digest, "src"))

    # Put compile-time JARs under compile-libs/
    compile_libs_digest = await Get(
        Digest,
        AddPrefix(await Get(Digest, MergeDigests(request.compile_classpath.digests())), "compile-libs")
    )

    # Put runtime JARs under uber-libs/
    uber_libs_digest = await Get(
        Digest,
        AddPrefix(await Get(Digest, MergeDigests(request.runtime_classpath.digests())), "uber-libs")
    )

    # Merge everything
    input_digest = await Get(Digest, MergeDigests([
        build_script_digest,
        src_digest,
        compile_libs_digest,
        uber_libs_digest,
    ]))

    # 5. Run tools.build
    process = JvmProcess(
        jdk=jdk,
        classpath_entries=tools_classpath.classpath_entries(),
        argv=["clojure.main", "build.clj"],
        input_digest=input_digest,
        output_files=("app.jar",),
        description=f"Build uberjar for {request.main_namespace}",
        timeout_seconds=600,
        ...
    )

    result = await Get(FallibleProcessResult, Process, await Get(Process, JvmProcess, process))

    if result.exit_code != 0:
        raise Exception(f"tools.build failed: {result.stderr.decode()}")

    return ToolsBuildUberjarResult(
        digest=result.output_digest,
        jar_path="app.jar",
    )
```

### Deliverables
- Build script generator function (no tools.deps)
- `ToolsBuildUberjarRequest` and `ToolsBuildUberjarResult` dataclasses
- Rule to build uberjar using tools.build with Pants-provided classpaths

### Validation
- Integration test: build uberjar for simple app
- Integration test: build uberjar with dependencies
- Integration test: verify provided deps excluded from JAR but available during compile

---

## Phase 3: Simplify Package Rule

### Goal
Replace the complex `package.py` with a simple implementation that either:
1. Creates a source-only JAR (when `main="clojure.main"`)
2. Delegates to tools.build for AOT + uberjar (otherwise)

### Tasks

#### 3.1 Keep source-only JAR creation simple

The `main="clojure.main"` path remains simple - no AOT needed:

```python
@rule(desc="Package Clojure deploy jar")
async def package_clojure_deploy_jar(
    field_set: ClojureDeployJarFieldSet,
    jvm: JvmSubsystem,
) -> BuiltPackage:
    main_namespace = field_set.main.value

    if main_namespace == "clojure.main":
        # Simple source-only JAR - no AOT
        return await create_source_only_jar(field_set, jvm)
    else:
        # Delegate to tools.build for AOT + uberjar
        return await create_aot_jar_with_tools_build(field_set, jvm)
```

#### 3.2 Implement AOT JAR via tools.build

```python
async def create_aot_jar_with_tools_build(
    field_set: ClojureDeployJarFieldSet,
    jvm: JvmSubsystem,
) -> BuiltPackage:
    """Create an AOT-compiled uberjar using tools.build."""

    # Validate main namespace has (:gen-class)
    await validate_gen_class(field_set)

    # Get compile-time classpath (ALL deps including provided)
    compile_classpath = await Get(Classpath, Addresses, all_source_addresses)

    # Get runtime classpath (excluding provided)
    runtime_classpath = await Get(Classpath, Addresses, runtime_source_addresses)

    # Get source files
    stripped_sources = await Get(StrippedSourceFiles, ...)

    # Build uberjar with tools.build
    result = await Get(
        ToolsBuildUberjarResult,
        ToolsBuildUberjarRequest(
            main_namespace=field_set.main.value,
            compile_classpath=compile_classpath,
            runtime_classpath=runtime_classpath,
            source_digest=stripped_sources.snapshot.digest,
            jdk=field_set.jdk,
        ),
    )

    # Rename output JAR to desired filename
    output_filename = field_set.output_path.value_or_default(file_ending="jar")
    final_digest = await rename_file_in_digest(result.digest, result.jar_path, output_filename)

    return BuiltPackage(
        digest=final_digest,
        artifacts=(BuiltPackageArtifact(relpath=output_filename),),
    )
```

#### 3.3 Remove complex filtering logic

Delete from package.py:
- `is_first_party_class()` function (~30 lines)
- `is_provided_class()` function (~15 lines)
- `get_namespace_path_from_class()` function (~20 lines)
- Two-phase JAR scanning logic (~50 lines)
- Source-only library detection (~20 lines)
- Class filtering in Step 1 (~60 lines)
- Source file exclusion logic in Step 2 (~30 lines)

**Total removal: ~225 lines of complex filtering logic**

#### 3.4 Remove or repurpose aot_compile.py

Since tools.build handles AOT compilation, `aot_compile.py` can be:
- Removed entirely, OR
- Repurposed for non-uberjar AOT compilation if needed elsewhere

### Deliverables
- Simplified `package.py` (~300 lines instead of ~630)
- Clear separation between source-only and AOT paths
- Removal of complex class filtering logic

### Validation
- All existing tests pass
- Integration test: source-only JAR works
- Integration test: AOT JAR works with simple app
- Integration test: AOT JAR works with Specter
- Integration test: provided dependencies excluded

---

## Phase 4: Keep Provided Dependencies Logic (Don't Oversimplify)

### Goal
Maintain the transitive expansion logic for provided dependencies, since this is critical for correctness.

### Tasks

#### 4.1 Keep lockfile-based transitive expansion

The current `provided_dependencies.py` correctly expands provided coordinates to include transitives using the Coursier lockfile. **Keep this logic.**

```python
def get_maven_transitive_coordinates(
    coordinates: set[tuple[str, str]],
    lockfile: CoursierResolvedLockfile,
) -> set[tuple[str, str]]:
    """Expand coordinates to include Maven transitives from lockfile."""
    # This uses pre-computed transitives from Coursier
    # Critical for correctness - don't remove
    ...
```

#### 4.2 Update interface if needed

The provided dependencies rule may need minor updates to work with the new flow, but the core transitive expansion logic should remain unchanged.

### Deliverables
- Minimal changes to `provided_dependencies.py`
- Transitive expansion logic preserved

### Validation
- Test: provided deps excluded from JAR
- Test: transitives of provided deps excluded
- Test: provided deps available during compilation

---

## Phase 5: Testing and Documentation

### Goal
Comprehensive testing and updated documentation.

### Tasks

#### 5.1 Update existing tests

- Update `test_package_clojure_deploy_jar.py` for new implementation
- Remove or update `test_aot_compile.py` if AOT compilation rule removed
- Keep `test_provided_dependencies.py` largely unchanged

#### 5.2 Add integration tests for edge cases

```python
def test_uberjar_with_specter():
    """Verify Specter's inline caching works correctly."""
    # Use Specter macros like recursive-path, declarepath
    # Verify no protocol identity errors at runtime

def test_uberjar_with_core_async():
    """Verify core.async go macro-generated classes are included."""

def test_uberjar_with_custom_gen_class_name():
    """Verify (:gen-class :name com.example.Custom) works."""

def test_provided_deps_in_compile_not_in_jar():
    """Verify provided deps available for AOT but excluded from JAR."""
```

#### 5.3 Update documentation

- Update `docs/aot_compilation.md`:
  - Explain tools.build delegation
  - Remove complex filtering explanations
  - Keep troubleshooting section

- Update `docs/uberjar_comparison.md`:
  - Update to reflect we now use tools.build
  - Simplify the comparison since we match tools.build behavior

### Deliverables
- Updated test suite
- New integration tests for edge cases
- Updated documentation

### Validation
- All tests pass
- Documentation accurately reflects new behavior

---

## Migration Path

### Backward Compatibility

The external API remains the same:
```python
clojure_deploy_jar(
    name="my-app",
    main="my.app.core",  # or "clojure.main"
    dependencies=[...],
    provided=[...],
)
```

No changes required in user BUILD files.

### Breaking Changes

None expected. The change is purely internal implementation.

---

## Risks and Mitigations

### Risk 1: tools.build doesn't handle all our edge cases
**Mitigation**: We'll discover and address edge cases during integration testing. If tools.build doesn't handle something, we'll add targeted workarounds or document limitations.

### Risk 2: Minimal basis map might not work
**Mitigation**: tools.build's `compile-clj` and `uber` primarily use `:classpath-roots` from the basis. If more fields are needed, we can add them to our constructed basis map.

### Risk 3: tools.build invocation overhead
**Mitigation**: The overhead is minimal compared to AOT compilation time itself.

### Risk 4: Error messages may be less clear
**Mitigation**: Wrap tools.build errors with helpful context about what step failed.

---

## Success Metrics

1. **Code reduction**: From ~1,170 lines to ~400 lines (~65% reduction)
2. **Dependency reduction**: No tools.deps needed, only tools.build
3. **Bug fixes**: Specter and similar source-only library issues resolved
4. **Maintainability**: Single source of truth (tools.build) for AOT/uberjar logic
5. **Test coverage**: All existing tests pass, plus new edge case tests

---

## Phase Dependencies

```
Phase 1 (Infrastructure)
    ↓
Phase 2 (tools.build Invocation)
    ↓
Phase 3 (Simplify Package Rule)
    ↓
Phase 4 (Provided Deps - parallel with Phase 3)
    ↓
Phase 5 (Testing & Docs)
```
