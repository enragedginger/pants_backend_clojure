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

**Key simplification**: Pants/Coursier already resolves all dependencies. We don't need tools.deps for dependency resolution. We simply:
1. Lay out compile-time JARs in one directory
2. Lay out runtime JARs (excluding provided) in another directory
3. Pass these directories to tools.build as pre-resolved classpaths

**Important: Three Distinct Classpaths**

There are THREE separate classpaths involved in this process:

1. **tools.build execution classpath** (for running the build script itself):
   - `io.github.clojure:tools.build` and its transitive dependencies
   - Coursier automatically resolves: Clojure, tools.deps, tools.namespace, slf4j-nop
   - This is the classpath for `java -cp <tools-classpath> clojure.main build.clj`

2. **Application compile classpath** (for AOT-compiling the user's code):
   - User's source files + ALL dependency JARs (including provided)
   - Needed because the Clojure compiler must load all required namespaces
   - Passed to tools.build via `:classpath-roots` in the basis

3. **Application runtime classpath** (for packaging the uberjar):
   - User's source files + runtime dependency JARs (excluding provided)
   - This is what actually goes into the final JAR

tools.build maintains classpath isolation by forking a **new JVM process** when running `compile-clj`. The basis's `:classpath-roots` become the classpath for this compilation subprocess, completely separate from the tools.build execution classpath.

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

## Phase 1: Create tools.build Integration Infrastructure [COMPLETED]

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

**Note**: We don't need tools.deps for dependency resolution (Pants/Coursier handles that). However, tools.deps IS included as a transitive dependency of tools.build and will be fetched automatically by Coursier.

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
                # Coursier resolves transitive deps automatically:
                # - org.clojure:clojure (required to run clojure.main)
                # - org.clojure:tools.deps (tools.build dependency)
                # - org.clojure:tools.namespace
                # - org.slf4j:slf4j-nop
            ]),
        ),
    )
```

### Deliverables
- `tools_build.py` subsystem with version configuration
- Rule to fetch tools.build classpath

### Validation
- Unit test that tools.build can be fetched (exists at `test_tools_build.py`)
- **Enhancement needed**: Update test to verify transitive deps are included (Clojure JAR should be in classpath)

---

## Phase 1.5: Test Cleanup (Required Before Phase 2) [COMPLETED]

### Problem Statement

The current test file `test_package_clojure_deploy_jar.py` is 2599 lines long and contains many tests that specifically verify the complex filtering logic that we're eliminating:

- First-party vs third-party class detection
- AOT-first, JAR-override packaging strategy
- Source-only JAR detection and namespace path tracking
- `is_first_party_class()` / `is_provided_class()` / `get_namespace_path_from_class()` functions

These implementation-specific tests caused confusion during the Phase 2 implementation attempt because they test internal mechanics that will no longer exist after simplification.

### Goal

Remove or simplify tests that are tied to the complex filtering logic before implementing Phase 2, so the new implementation isn't constrained by outdated test expectations.

### Test Analysis

#### Tests to SIMPLIFY or REMOVE

**IMPORTANT**: Per plan review feedback, prefer SIMPLIFYING tests to verify JAR contents (behavior) rather than removing them entirely. Only remove tests that are purely about internal implementation mechanics.

| Test Name | Lines | Action | Reason |
|-----------|-------|--------|--------|
| `test_only_first_party_aot_classes_included` | 1547-1628 | SIMPLIFY | Keep assertion that app classes are in JAR; remove `is_first_party_class()` specific checks |
| `test_third_party_content_extracted_from_jars` | 1710-1777 | SIMPLIFY | Keep assertion that third-party classes are in JAR; remove extraction implementation details |
| `test_aot_class_not_in_jars_is_kept` | 2261-2367 | KEEP | Verifies macro-generated classes are in final JAR (behavior we need tools.build to match) |
| `test_nested_inner_class_not_in_jars_is_kept` | 2370-2471 | KEEP | Verifies inner classes are in final JAR (behavior we need tools.build to match) |

The macro-generated class tests are particularly important because they verify that tools.build produces the same output as our current implementation. Keep them as behavior validation.

#### Tests to SIMPLIFY (keep end-to-end behavior, remove implementation checks):

| Test Name | Lines | Simplification |
|-----------|-------|---------------|
| `test_aot_classes_included_then_jar_overrides` | 1136-1211 | Keep assertions (project + clojure classes in JAR), simplify docstring |
| `test_transitive_first_party_classes_included` | 1214-1316 | Keep but just verify classes exist, remove comments about filtering |
| `test_deeply_nested_transitive_deps_included` | 1319-1443 | Keep basic verification |
| `test_third_party_classes_not_from_aot` | 1631-1707 | Keep assertions (third-party classes exist), remove "from JARs not AOT" details |
| `test_hyphenated_namespace_classes_included` | 1780-1871 | Keep but remove filtering-specific comments |
| `test_transitive_macro_generated_classes_included` | 2474-2600 | Keep basic verification |

#### Tests to KEEP as-is (test end behavior, not implementation):

- `test_package_simple_deploy_jar` - Basic packaging
- `test_package_deploy_jar_validates_gen_class` - Input validation
- `test_clojure_main_namespace_field_required` - Field validation
- `test_clojure_deploy_jar_target_has_required_fields` - Target type validation
- `test_package_deploy_jar_with_custom_gen_class_name` - gen-class :name handling
- `test_package_deploy_jar_multiple_gen_class_names` - Multiple gen-class declarations
- `test_package_deploy_jar_gen_class_without_name` - Standard gen-class
- `test_package_deploy_jar_gen_class_name_after_other_options` - Complex gen-class parsing
- `test_package_deploy_jar_with_defrecord_deftype` - defrecord/deftype handling
- `test_package_deploy_jar_missing_main_namespace` - Error handling
- `test_package_deploy_jar_with_transitive_dependencies` - Transitive deps
- `test_provided_field_can_be_parsed` - Field parsing
- `test_provided_dependencies_excluded_from_jar` - Provided deps exclusion (keep!)
- `test_provided_jvm_artifact_excluded_from_jar` - Third-party provided
- `test_transitive_maven_deps_included_in_jar` - Maven transitives included
- `test_provided_maven_transitives_excluded_from_jar` - Provided Maven transitives
- `test_hyphenated_main_namespace` - Hyphenated namespaces
- `test_no_duplicate_entries_in_jar` - No duplicates (keep - behavior verification)
- All `clojure.main` source-only tests (4 tests)

### Tasks

#### 1.5.0 Verify Phase 1 completeness

Before starting test cleanup, confirm Phase 1 deliverables exist:
- [x] `tools_build.py` subsystem exists at `pants-plugins/clojure_backend/subsystems/tools_build.py`
- [x] Rule to fetch tools.build classpath exists (`ToolsBuildClasspathRequest`)
- [x] Unit tests exist at `pants-plugins/tests/test_tools_build.py`
- [x] Subsystem registered in `register.py`

#### 1.5.1 Simplify implementation-specific tests

Simplify these tests to verify JAR contents without testing internal mechanics:

```python
# SIMPLIFY: Keep assertions about what's in the JAR, remove implementation details
test_only_first_party_aot_classes_included  # Keep: verify app classes in JAR
test_third_party_content_extracted_from_jars  # Keep: verify third-party classes in JAR

# KEEP AS-IS: These verify behavior that tools.build must match
test_aot_class_not_in_jars_is_kept  # Critical: macro-generated classes must be in JAR
test_nested_inner_class_not_in_jars_is_kept  # Critical: inner classes must be in JAR
```

Update section header comments to be behavior-focused:
- Lines 1117-1134: Change to "Tests for JAR contents" (remove "AOT-first, JAR-override")
- Lines 1540-1545: Change to "Tests for application classes in JAR"
- Lines 2253-2259: Change to "Tests for macro-generated classes in JAR"

#### 1.5.2 Simplify retained tests

Update 6 tests that verify behavior but have implementation-specific docstrings:

**test_aot_classes_included_then_jar_overrides:**
```python
# BEFORE:
"""Integration test: verify AOT classes are added first, then JAR contents override.
This tests the core behavior of the packaging logic:
1. All AOT-compiled classes are added first (project + third-party transitives)
2. Dependency JAR contents are extracted and override existing entries
...
"""

# AFTER:
"""Verify that both project classes and dependency classes are in the final JAR."""
```

**test_transitive_first_party_classes_included:**
```python
# BEFORE:
"""Verify that transitive first-party dependencies have their AOT classes included.
This test is critical for catching regressions like the source-only library bug...
"""

# AFTER:
"""Verify that transitive first-party dependencies have their classes in the JAR.
When app depends on lib, the compiled classes from lib must be included.
"""
```

**test_third_party_classes_not_from_aot:**
```python
# BEFORE:
"""Verify that third-party classes (e.g., clojure/core*.class) are NOT from AOT.
The third-party classes should come from the dependency JARs, not from AOT...
"""

# AFTER:
"""Verify that third-party dependency classes are included in the JAR."""
```

Apply similar simplifications to:
- `test_deeply_nested_transitive_deps_included`
- `test_hyphenated_namespace_classes_included`
- `test_transitive_macro_generated_classes_included`

#### 1.5.3 Add missing critical test

Add a test to verify provided deps are available during compilation:

```python
def test_provided_deps_available_for_compilation_excluded_from_jar(rule_runner: RuleRunner) -> None:
    """Verify provided dependencies are available during AOT but excluded from JAR.

    This is critical for libraries like servlet-api that must be available at
    compile time but should not be bundled (container provides them at runtime).
    """
    # Create app that uses type hints or imports from a provided dependency
    # Assert: AOT compilation succeeds
    # Assert: JAR does not contain provided dependency classes
```

This test validates the two-classpath approach (compile-libs vs uber-libs) in Phase 2.

#### 1.5.4 Verify tests pass after cleanup

After making changes, run retained tests:
```bash
pants test pants-plugins/tests/test_package_clojure_deploy_jar.py
```

All retained tests must pass with the current implementation before proceeding to Phase 2.

### Deliverables
- Simplified test file with behavior-focused tests
- All tests now verify **what** the JAR contains, not **how** it's created
- Updated test docstrings without references to filtering logic
- New test for provided deps during compilation
- Macro-generated class tests preserved (critical for tools.build behavior validation)

### Validation
- All tests pass with current implementation
- No test docstrings reference "first-party filtering", "AOT-first", "JAR-override", "source-only library detection", or "two-phase scanning"
- Critical behavior tests (macro-generated classes, inner classes) still exist and pass

---

## Phase 2: Implement Single tools.build Invocation

### Goal
Create a single rule that invokes tools.build to perform both AOT compilation AND uberjar creation in one step, using Pants-provided classpaths.

### Classpath Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     JVM Process (started by Pants)                  │
│  Classpath: tools.build + Clojure + tools.deps + tools.namespace   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  build.clj script                                            │   │
│  │                                                              │   │
│  │  (b/compile-clj {:basis compile-basis ...})                  │   │
│  │        │                                                     │   │
│  │        ▼  forks new JVM                                      │   │
│  │  ┌──────────────────────────────────────────────────────┐   │   │
│  │  │  Compilation subprocess                               │   │   │
│  │  │  Classpath: src/ + compile-libs/*.jar                 │   │   │
│  │  │  (includes provided deps for type resolution)         │   │   │
│  │  └──────────────────────────────────────────────────────┘   │   │
│  │                                                              │   │
│  │  (b/uber {:basis uber-basis ...})                            │   │
│  │        │                                                     │   │
│  │        ▼  reads JARs from uber-basis classpath               │   │
│  │  ┌──────────────────────────────────────────────────────┐   │   │
│  │  │  Packages: classes/ + uber-libs/*.jar → app.jar       │   │   │
│  │  │  (excludes provided deps from final JAR)              │   │   │
│  │  └──────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

Key insight: The tools.build execution classpath and the application classpaths are **completely separate**. tools.build forks a new JVM for compilation with only the application's classpath.

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
    # Note: direct_linking parameter should be added to target field and passed in
    return f'''
(ns build
  (:require [clojure.tools.build.api :as b]
            [clojure.java.io :as io]))

(def class-dir "{class_dir}")
(def uber-file "{uber_file}")
(def main-ns '{main_ns})

(defn list-jars
  "List all JAR files in a directory, returning relative paths."
  [dir]
  (->> (io/file dir)
       (.listFiles)
       (filter #(and (.isFile %) (.endsWith (.getName %) ".jar")))
       (map #(str dir "/" (.getName %)))  ; Return relative path: "dir/name.jar"
       vec))

(defn build-classpath-map
  "Build a classpath map structure for tools.build basis.
  Each entry maps a path to metadata (we use minimal metadata)."
  [paths]
  (into {{}} (map (fn [p] [p {{:path-key (keyword (str "path-" (hash p)))}}]) paths)))

(defn uberjar [_]
  (try
    ;; Classpaths pre-resolved by Pants - no tools.deps needed
    (let [compile-cp (vec (concat ["src"] [class-dir] (list-jars "compile-libs")))
          uber-cp (vec (concat ["src"] [class-dir] (list-jars "uber-libs")))
          ;; Construct basis maps with required structure
          compile-basis {{:classpath (build-classpath-map compile-cp)
                          :classpath-roots compile-cp}}
          uber-basis {{:classpath (build-classpath-map uber-cp)
                       :classpath-roots uber-cp}}]

      ;; Clean previous output
      (b/delete {{:path class-dir}})
      (.mkdirs (io/file class-dir))

      ;; AOT compile main namespace (Clojure transitively compiles all required namespaces)
      (println "Compiling" (str main-ns "..."))
      (b/compile-clj {{:basis compile-basis
                       :src-dirs ["src"]
                       :class-dir class-dir
                       :ns-compile [main-ns]}})

      ;; Build uberjar with runtime classpath (excludes provided deps)
      (println "Building uberjar" (str uber-file "..."))
      (b/uber {{:basis uber-basis
                :class-dir class-dir
                :uber-file uber-file
                :main main-ns
                ;; Exclude LICENSE to avoid conflicts (some deps have file, others have folder)
                :exclude ["LICENSE"]}})

      ;; Clean up classes directory
      (b/delete {{:path class-dir}})
      (println "Uberjar built:" uber-file)
      (System/exit 0))

    (catch Exception e
      (println "ERROR:" (.getMessage e))
      (.printStackTrace e)
      (System/exit 1))))

;; Entry point
(uberjar nil)
'''
```

**Key points**:
- `compile-libs/` contains ALL JARs (including provided) - for AOT compilation
- `uber-libs/` contains runtime JARs only (excluding provided) - for packaging
- No tools.deps resolution, no deps.edn, no Maven coordinate parsing
- Pants lays out the directories, tools.build just uses them
- `:ns-compile [main-ns]` - only compiles main; Clojure handles transitive compilation
- `:exclude ["LICENSE"]` - avoids conflicts when some deps have LICENSE as file vs folder

**Important: Basis Map Structure**

The basis map passed to `compile-clj` and `uber` needs more than just `:classpath-roots`. Looking at tools.build source:
- `compile-clj` uses `basis-paths` which reads both `:classpath` and `:classpath-roots`
- `uber` uses the basis to pull dependency JARs

**Alternative approach using `:cp` and `:src-dirs`**:

Instead of constructing a full basis, we can pass classpath explicitly via `:cp`:
```clojure
(b/compile-clj {:cp (vec compile-cp)  ; explicit classpath, no basis needed
                :src-dirs ["src"]      ; source directories explicitly
                :class-dir class-dir
                :ns-compile [main-ns]})
```

This bypasses basis entirely for compilation. For `uber`, we may need a different approach (see Phase 2 Validation).

**Classpath isolation**: When `b/compile-clj` runs, it forks a NEW JVM process with the classpath specified via `:basis` or `:cp`. The tools.build execution classpath (which includes Clojure, tools.deps, etc.) is completely separate from the application classpath.

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

# How the two classpaths are computed (shown in Phase 3):
#
# 1. Get all target addresses (first-party deps):
#    transitive_targets = await Get(TransitiveTargets, TransitiveTargetsRequest(...))
#    all_source_addresses = Addresses(t.address for t in transitive_targets.closure)
#
# 2. Get provided deps (to exclude from runtime):
#    provided_deps = await Get(ProvidedDependencies, ProvidedDependenciesRequest(...))
#
# 3. Compute runtime addresses (excluding provided):
#    runtime_source_addresses = Addresses(
#        addr for addr in all_source_addresses
#        if addr not in provided_deps.addresses
#    )
#
# 4. Get classpaths:
#    compile_classpath = await Get(Classpath, Addresses, all_source_addresses)
#    runtime_classpath = await Get(Classpath, Addresses, runtime_source_addresses)

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
    # NOTE: tools_classpath contains tools.build + ALL its transitive deps:
    #   - org.clojure:clojure (provides clojure.main entry point)
    #   - org.clojure:tools.deps
    #   - org.clojure:tools.namespace
    #   - org.slf4j:slf4j-nop
    # These are resolved automatically by Coursier.
    # This is SEPARATE from the application classpath (compile-libs/uber-libs).
    process = JvmProcess(
        jdk=jdk,
        classpath_entries=tools_classpath.classpath_entries(),  # tools.build classpath
        argv=["clojure.main", "build.clj"],  # clojure.main is in the Clojure JAR
        input_digest=input_digest,  # Contains: build.clj, src/, compile-libs/, uber-libs/
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

**Pre-implementation validation (CRITICAL - do before writing production code):**
- Manually test the minimal basis map approach in a standalone Clojure project
- Verify that `compile-clj` with `:basis {:classpath ... :classpath-roots ...}` works correctly
- Verify that `uber` correctly uses the basis to pull JARs
- Document any additional basis fields required

**Integration tests (after implementation):**
- Integration test: build uberjar for simple app
- Integration test: build uberjar with dependencies
- Integration test: verify provided deps excluded from JAR but available during compile
- Integration test: verify classpath isolation (app compiled with its own Clojure version, not tools.build's)
- Unit test: verify tools.build classpath includes Clojure and transitive deps (already exists in test_tools_build.py)

### Implementation Notes (Deviations from Plan)

During implementation, the following deviations from the original plan were necessary:

#### 1. Added `:java-cmd` parameter for `compile-clj`

**Problem**: tools.build's `compile-clj` forks a new JVM subprocess and looks for the Java executable via `$JAVA_CMD`, `$PATH`, or `$JAVA_HOME`. In the Pants sandbox, these environment variables aren't configured.

**Solution**: Added a `java_cmd` parameter to `generate_build_script()` and pass `__java_home/bin/java` (Pants' JDK symlink location) to `:java-cmd` in the build script:

```clojure
(b/compile-clj {:basis compile-basis
                :src-dirs ["src"]
                :class-dir class-dir
                :ns-compile [main-ns]
                :java-cmd java-cmd})  ;; Added this
```

#### 2. Different basis structure for `uber` vs `compile-clj`

**Problem**: The plan suggested using `:classpath` and `:classpath-roots` for both functions, but `uber` actually expects a `:libs` map where each library has `:paths`.

**Solution**: Use different basis structures:

```clojure
;; For compile-clj (simpler than plan suggested):
{:classpath-roots compile-cp}

;; For uber (requires :libs map):
{:libs {dep0 {:paths ["uber-libs/clojure.jar"]}
        dep1 {:paths ["uber-libs/spec.jar"]}
        ...}}
```

The `build-libs-map` helper function constructs this structure from the JAR paths.

#### 3. Simplified `compile-clj` basis

**Problem**: The plan suggested a full basis map with both `:classpath` and `:classpath-roots`.

**Solution**: Testing revealed that `compile-clj` only needs `:classpath-roots`, so the simpler structure was used.

#### 4. Added debug output to build script

Added `println` statements to show JAR counts during build, helpful for debugging:

```clojure
(println "compile-libs:" (count compile-jars) "JARs")
(println "uber-libs:" (count uber-jars) "JARs")
```

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

## Phase 4: Keep Provided Dependencies Logic (Don't Oversimplify) [COMPLETED]

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

## Phase 5: Testing and Documentation [COMPLETED]

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

### Risk 5: Classpath version conflicts between tools.build and application
**Situation**: tools.build brings in Clojure 1.12.0 (and its other deps) on its execution classpath. If the application uses a different Clojure version, could there be conflicts?

**Mitigation**: This is NOT a concern because:
1. tools.build runs in its own JVM process with its own classpath
2. When `b/compile-clj` runs, it forks a NEW JVM with only the application's classpath (from `:classpath-roots`)
3. The application's Clojure version (in `compile-libs/`) is used for AOT compilation
4. tools.build's Clojure is never on the same classpath as the application's Clojure

**Validation**: Add a test that compiles an app using Clojure 1.10.x to verify classpath isolation works correctly.

---

## Success Metrics

1. **Code simplification**: Significant reduction in complex filtering logic in package.py
   - Remove `is_first_party_class()`, `is_provided_class()`, `get_namespace_path_from_class()`
   - Remove two-phase JAR scanning logic
   - Remove source-only library detection
   - **Realistic target**: ~40-50% reduction in package.py complexity
2. **Dependency reduction**: No tools.deps resolution needed (though it's included transitively)
3. **Bug fixes**: Specter and similar source-only library issues resolved
4. **Maintainability**: Single source of truth (tools.build) for AOT/uberjar logic
5. **Test coverage**: All behavior tests pass, plus new edge case tests
6. **Classpath isolation**: Verified that tools.build's Clojure version doesn't conflict with app's Clojure version

---

## Phase Dependencies

```
Phase 1 (Infrastructure) [COMPLETED]
    ↓
Phase 1.5 (Test Cleanup) [COMPLETED]
    ↓
Phase 2 (tools.build Invocation) [COMPLETED]
    ↓
Phase 3 (Simplify Package Rule) [COMPLETED]
    ↓
Phase 4 (Provided Deps - parallel with Phase 3) [COMPLETED]
    ↓
Phase 5 (Testing & Docs) [COMPLETED]
```

**Why Test Cleanup before Phase 2?**
During the initial Phase 2 implementation attempt, the complex test cases for filtering logic caused confusion and led to implementation deviations from the plan. By cleaning up these tests first, we ensure that:
1. The new implementation is guided by behavior tests, not implementation tests
2. There's no confusion about what the final JAR should contain
3. Tests that will inevitably fail (because they test removed code) are already gone
