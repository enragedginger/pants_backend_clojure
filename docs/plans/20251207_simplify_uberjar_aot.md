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

Delegate AOT compilation and uberjar creation to **tools.build**, which is the official Clojure tooling and handles these complexities correctly. The key insight from a proven build.clj pattern:

```clojure
;; Two separate basis:
;; 1. compile-basis: Full deps for AOT compilation
;; 2. uber-basis: Excludes provided deps for packaging
(let [compile-basis (b/create-basis)
      uber-basis (b/create-basis {:aliases [:uberjar]})  ; alias excludes provided
      ...]
  (b/compile-clj {:basis compile-basis :class-dir class-dir :ns-compile [main]})
  (b/uber {:basis uber-basis :class-dir class-dir :uber-file uber-file :main main}))
```

## Design Principles

1. **Simple mental model**: User specifies `main` namespace, we handle the rest
2. **Default to source-only**: `main="clojure.main"` (no AOT) is the default
3. **Delegate complexity**: Let tools.build handle AOT/uberjar intricacies
4. **Two-basis pattern**: Compile with all deps, package without provided deps
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
Establish the ability to invoke tools.build from Pants, including fetching it as a dependency and generating the necessary deps.edn and build script.

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
        default="0.10.5",
        help="Version of tools.build to use for AOT compilation and uberjar creation"
    )

    tools_deps_version = StrOption(
        default="0.20.1331",
        help="Version of tools.deps to use"
    )
```

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
                ArtifactRequirement(Coordinate(
                    "org.clojure", "tools.deps", tools_build.tools_deps_version
                )),
            ]),
        ),
    )
```

#### 1.3 Create helper to generate deps.edn with two-basis support

Generate a deps.edn that represents the project's dependencies with a `:uberjar` alias for packaging:

```python
def generate_deps_edn_for_uberjar(
    dependency_jars: list[JarInfo],  # From classpath analysis
    source_paths: list[str],  # Directories containing source files
    provided_coords: set[tuple[str, str]],  # (group, artifact) pairs to exclude
) -> str:
    """Generate deps.edn with :uberjar alias excluding provided deps.

    The deps.edn has:
    - :paths pointing to source directories
    - :deps with ALL dependencies (for compile-basis)
    - :aliases {:uberjar {:replace-deps {...}}} with provided deps excluded (for uber-basis)
    """
    # Build full deps map
    all_deps = {}
    for jar in dependency_jars:
        coord_key = f"{jar.group}/{jar.artifact}"
        all_deps[coord_key] = {"mvn/version": jar.version}

    # Build deps map WITHOUT provided (and their transitives)
    # Note: provided_coords should already include transitives from lockfile expansion
    uber_deps = {}
    for coord_key, version_map in all_deps.items():
        group, artifact = coord_key.split("/")
        if (group, artifact) not in provided_coords:
            uber_deps[coord_key] = version_map

    deps_edn = {
        ":paths": source_paths,
        ":deps": all_deps,
        ":aliases": {
            ":uberjar": {
                ":replace-deps": uber_deps
            }
        }
    }

    return format_as_edn(deps_edn)
```

**Key insight**: The `:uberjar` alias uses `:replace-deps` to completely replace the dependency map with one that excludes provided deps. When tools.build calls `(b/create-basis {:aliases [:uberjar]})`, it gets the reduced dependency set.

#### 1.4 Create helper to extract JAR coordinates from Pants classpath

```python
@dataclass(frozen=True)
class JarInfo:
    group: str
    artifact: str
    version: str
    path: str

def extract_jar_coordinates(classpath: Classpath) -> list[JarInfo]:
    """Extract Maven coordinates from JAR paths in classpath.

    Pants/Coursier JAR paths follow: {group}_{artifact}_{version}.jar
    e.g., "org.clojure_clojure_1.11.1.jar"
    """
    jars = []
    for entry in classpath.args():
        if entry.endswith('.jar'):
            # Parse coordinate from filename
            filename = os.path.basename(entry)
            # ... parsing logic ...
            jars.append(JarInfo(group, artifact, version, entry))
    return jars
```

### Deliverables
- `tools_build.py` subsystem with version configuration
- Rule to fetch tools.build/tools.deps classpath
- Helper function to generate deps.edn with two-basis support
- Helper function to extract JAR coordinates from Pants classpath

### Validation
- Unit test that tools.build can be fetched
- Unit test that deps.edn generation produces valid EDN
- Unit test that `:uberjar` alias correctly excludes provided deps

---

## Phase 2: Implement Single tools.build Invocation

### Goal
Create a single rule that invokes tools.build to perform both AOT compilation AND uberjar creation in one step.

### Tasks

#### 2.1 Create build script generator

Generate a self-contained Clojure build script:

```python
def generate_build_script(
    main_ns: str,
    class_dir: str = "target/classes",
    uber_file: str = "target/app.jar",
) -> str:
    """Generate build.clj that uses the two-basis pattern.

    This mirrors a proven build.clj pattern that:
    1. Uses compile-basis (all deps) for AOT compilation
    2. Uses uber-basis (:uberjar alias) for packaging (excludes provided deps)
    3. Only compiles the main namespace - Clojure handles transitive compilation
    4. No source copying needed - AOT classes are sufficient
    """
    return f'''
(ns build
  (:require [clojure.tools.build.api :as b]))

(def class-dir "{class_dir}")
(def uber-file "{uber_file}")
(def main-ns '{main_ns})

(defn uberjar [_]
  ;; Full basis for compilation (includes ALL deps including provided)
  (let [compile-basis (b/create-basis)
        ;; Packaging basis uses :uberjar alias (excludes provided deps)
        uber-basis (b/create-basis {{:aliases [:uberjar]}})]

    ;; Clean previous output
    (b/delete {{:path class-dir}})

    ;; AOT compile main namespace (Clojure transitively compiles all required namespaces)
    (println "Compiling" (str main-ns "..."))
    (b/compile-clj {{:basis compile-basis
                     :class-dir class-dir
                     :ns-compile [main-ns]
                     :compile-opts {{:direct-linking true}}}})

    ;; Build uberjar with packaging basis (excludes provided deps)
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
- `compile-basis` uses default basis (all deps) - provided deps ARE available for compilation
- `uber-basis` uses `:uberjar` alias which has provided deps excluded
- `:ns-compile [main-ns]` - only compiles main; Clojure handles transitive compilation at the language level
- No `b/copy-dir` needed - source files aren't required in uberjar when AOT compiling
- `:exclude ["LICENSE"]` - avoids conflicts when some deps have LICENSE as file vs folder
- `b/compile-clj` does AOT with direct-linking enabled
- `b/uber` creates the final JAR using the reduced basis
- Cleans up class-dir after building

#### 2.2 Create the uberjar rule

```python
@dataclass(frozen=True)
class ToolsBuildUberjarRequest:
    """Request to build an uberjar using tools.build."""
    main_namespace: str
    source_addresses: Addresses
    provided_coordinates: frozenset[tuple[str, str]]  # (group, artifact) pairs
    jdk: JvmJdkField | None = None
    resolve: JvmResolveField | None = None

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
    # 1. Get tools.build classpath (separate from project deps)
    tools_classpath = await Get(ToolClasspath, ToolsBuildSubsystem, tools_build_subsystem)

    # 2. Get JDK
    jdk_request = JdkRequest.from_field(request.jdk) if request.jdk else JdkRequest.SOURCE_DEFAULT
    jdk = await Get(JdkEnvironment, JdkRequest, jdk_request)

    # 3. Get project classpath (all dependencies)
    project_classpath = await Get(Classpath, Addresses, request.source_addresses)

    # 4. Get source files with stripped source roots
    source_targets = await Get(Targets, Addresses, request.source_addresses)
    source_fields = [t[ClojureSourceField] for t in source_targets if t.has_field(ClojureSourceField)]
    stripped_sources = await Get(
        StrippedSourceFiles,
        SourceFilesRequest(source_fields),
    )

    # 5. Extract JAR coordinates and generate deps.edn
    jar_infos = extract_jar_coordinates(project_classpath)
    deps_edn = generate_deps_edn_for_uberjar(
        dependency_jars=jar_infos,
        source_paths=["src"],  # Sources will be at "src/" in the sandbox
        provided_coords=request.provided_coordinates,
    )

    # 6. Generate build script
    build_script = generate_build_script(
        main_ns=request.main_namespace,
        class_dir="target/classes",
        uber_file="target/app.jar",
    )

    # 7. Create working directory structure
    # Structure:
    #   deps.edn          <- Generated deps.edn with :uberjar alias
    #   build.clj         <- Generated build script
    #   src/              <- Source files (namespace structure preserved)
    #   .m2/              <- Symlinks or copies of dependency JARs (for tools.deps)

    # Create deps.edn and build.clj
    config_digest = await Get(Digest, CreateDigest([
        FileContent("deps.edn", deps_edn.encode()),
        FileContent("build.clj", build_script.encode()),
    ]))

    # Restructure sources under src/ directory
    src_digest = await Get(Digest, AddPrefix(stripped_sources.snapshot.digest, "src"))

    # Merge everything
    input_digest = await Get(Digest, MergeDigests([
        config_digest,
        src_digest,
        *project_classpath.digests(),  # JAR files
    ]))

    # 8. Run tools.build
    # Note: Only tools.build is on the JVM classpath
    # Project deps are referenced via deps.edn which tools.build reads
    process = JvmProcess(
        jdk=jdk,
        classpath_entries=tools_classpath.classpath_entries(),
        argv=["clojure.main", "build.clj"],
        input_digest=input_digest,
        output_files=("target/app.jar",),
        extra_env={},
        extra_jvm_options=(),
        extra_nailgun_keys=(),
        output_directories=(),
        description=f"Build uberjar for {request.main_namespace}",
        timeout_seconds=600,  # 10 minutes for large projects
        level=LogLevel.DEBUG,
        use_nailgun=False,
    )

    process_obj = await Get(Process, JvmProcess, process)
    result = await Get(FallibleProcessResult, Process, process_obj)

    if result.exit_code != 0:
        raise Exception(
            f"tools.build failed for {request.main_namespace}:\n"
            f"stdout: {result.stdout.decode()}\n"
            f"stderr: {result.stderr.decode()}"
        )

    return ToolsBuildUberjarResult(
        digest=result.output_digest,
        jar_path="target/app.jar",
    )
```

#### 2.3 Handle dependency JAR locality for tools.deps

**Issue**: tools.deps expects to resolve dependencies from Maven repositories or a local `.m2` cache. But Pants provides JAR files via digests in the sandbox.

**Solution options**:

**Option A: Local Maven repo structure**
Create a `.m2/repository` structure in the sandbox with the JAR files:
```
.m2/repository/org/clojure/clojure/1.11.1/clojure-1.11.1.jar
```
And configure deps.edn with `:local-repo ".m2/repository"`.

**Option B: Use :local/root coordinates**
Instead of Maven coordinates, use local paths:
```clojure
{:deps {org.clojure/clojure {:local/root "jars/org.clojure_clojure_1.11.1.jar"}}}
```

**Option C: Pre-resolved classpath**
Pass the classpath directly to tools.build, bypassing tools.deps resolution:
```clojure
;; In build.clj, manually construct basis with pre-resolved classpath
(def basis {:classpath-roots [... paths to JARs ...]
            :libs {... lib map ...}})
```

**Recommendation**: Option A (local Maven repo) is cleanest and matches how tools.build is designed to work. We'll create the directory structure and set `:local-repo`.

### Deliverables
- Build script generator function
- `ToolsBuildUberjarRequest` and `ToolsBuildUberjarResult` dataclasses
- Rule to build uberjar using tools.build
- Dependency JAR locality solution

### Validation
- Integration test: build uberjar for simple app
- Integration test: build uberjar with dependencies
- Integration test: verify direct-linking is enabled
- Integration test: verify provided deps excluded

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

#### 3.2 Implement source-only JAR (keep existing logic, simplified)

```python
async def create_source_only_jar(
    field_set: ClojureDeployJarFieldSet,
    jvm: JvmSubsystem,
) -> BuiltPackage:
    """Create a JAR with source files only, no AOT compilation."""
    # Get classpath and sources
    # Create JAR with manifest Main-Class: clojure.main
    # Bundle all source files and dependency JARs
    # This path is already relatively simple in current code
    ...
```

#### 3.3 Implement AOT JAR via tools.build

```python
async def create_aot_jar_with_tools_build(
    field_set: ClojureDeployJarFieldSet,
    jvm: JvmSubsystem,
) -> BuiltPackage:
    """Create an AOT-compiled uberjar using tools.build."""

    # Validate main namespace has (:gen-class)
    await validate_gen_class(field_set)

    # Resolve provided dependencies with transitives
    provided_deps = await Get(
        ProvidedDependencies,
        ResolveProvidedDependenciesRequest(field_set.provided, resolve_name),
    )

    # Build uberjar with tools.build
    result = await Get(
        ToolsBuildUberjarResult,
        ToolsBuildUberjarRequest(
            main_namespace=field_set.main.value,
            source_addresses=source_addresses,
            provided_coordinates=provided_deps.coordinates,
            jdk=field_set.jdk,
            resolve=field_set.resolve,
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

#### 3.4 Remove complex filtering logic

Delete from package.py:
- `is_first_party_class()` function (~30 lines)
- `is_provided_class()` function (~15 lines)
- `get_namespace_path_from_class()` function (~20 lines)
- Two-phase JAR scanning logic (~50 lines)
- Source-only library detection (~20 lines)
- Class filtering in Step 1 (~60 lines)
- Source file exclusion logic in Step 2 (~30 lines)

**Total removal: ~225 lines of complex filtering logic**

#### 3.5 Remove or repurpose aot_compile.py

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

def test_two_basis_pattern():
    """Verify compile-basis has provided deps, uber-basis doesn't."""
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

### Risk 2: Classpath/dependency locality for tools.deps
**Mitigation**: Phase 2.3 addresses this with the local Maven repo structure approach.

### Risk 3: tools.build invocation overhead
**Mitigation**: The overhead is minimal compared to AOT compilation time itself.

### Risk 4: Error messages may be less clear
**Mitigation**: Wrap tools.build errors with helpful context about what step failed.

### Risk 5: Source file handling in sandbox
**Mitigation**: Phase 2.2 shows explicit source file restructuring with AddPrefix to create expected directory structure.

---

## Success Metrics

1. **Code reduction**: From ~1,170 lines to ~500 lines (~55% reduction)
2. **Bug fixes**: Specter and similar source-only library issues resolved
3. **Maintainability**: Single source of truth (tools.build) for AOT/uberjar logic
4. **Test coverage**: All existing tests pass, plus new edge case tests

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
