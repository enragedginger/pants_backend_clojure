# Comprehensive Plan: AOT Compilation & Uberjar Support for Clojure Backend

## Executive Summary

The Clojure backend currently uses **runtime compilation** (sources on classpath, compiled on-the-fly). For uberjars, we need **AOT (Ahead-of-Time) compilation** to generate `.class` files that can be packaged and executed without source files.

**Key Decision**: Following Leiningen's best practice, AOT compilation should be **optional and only performed when creating uberjars**, not during development.

---

## Research Findings

### How Other Tools Handle AOT Compilation

#### 1. Leiningen (Most Popular Clojure Build Tool)

**Configuration**: `:aot` key specifies namespaces to compile
- `:aot [my.main.namespace]` - Compile specific namespace (transitive)
- `:aot :all` - Compile all project namespaces

**Best Practice**: AOT only in uberjar profile
```clojure
:profiles {:uberjar {:aot :all}}
```

**Main Class**: `:main` specifies entry point, must have `(:gen-class)` and `-main` function

**Rationale**: Avoid AOT during development (slower REPL loads, stale classes)

#### 2. tools.build (Official Clojure Tool)

**API**: `compile-clj` function
```clojure
(b/compile-clj {:basis basis
                :ns-compile '[my.app.main]  ; Namespaces to compile
                :class-dir "target/classes"})
```

**Uber JAR**: `uber` function packages classes + dependencies
```clojure
(b/uber {:class-dir class-dir
         :uber-file "app.jar"
         :basis basis
         :main 'my.app.main})
```

**Process**: compile-clj � copy resources � uber

#### 3. Clojure Core Compilation Mechanism

**API**: `(compile 'namespace.name)` function

**Setup Requirements**:
- `*compile-path*` must be set (output directory for `.class` files)
- Output directory must be in classpath
- Main namespace needs `(:gen-class)` for executable entry point

**Output**: Generates `namespace/path__init.class` + function classes

**Transitive**: Automatically compiles all required namespaces

### Pants deploy_jar Target

- **Purpose**: Creates uberjar with application + dependencies
- **Backend**: Requires `pants.backend.experimental.scala` (JVM backend)
- **Required Field**: `main` - JVM class name with `main()` method
- **Dependencies**: Accepts any JVM targets via `dependencies` field
- **Configuration**:
  - `jdk`: JDK version for building
  - `resolve`: Which JVM resolve to use
  - `output_path`: Custom output location
  - `duplicate_policy`: Handle duplicate files in merged JARs
  - `shading_rules`: Package relocation for avoiding conflicts
  - `exclude_files`: Patterns to exclude

---

## Implementation Plan

### Phase 1: Add AOT Compilation Rule

**Goal**: Create a Pants rule that AOT compiles Clojure namespaces to `.class` files

**New File**: `pants-plugins/clojure_backend/aot_compile.py`

#### Key Components

**1. CompileClojureAOT Request/Result**

```python
@dataclass(frozen=True)
class CompileClojureAOTRequest:
    namespaces: tuple[str, ...]  # Namespaces to compile
    source_targets: Targets      # Source targets containing the namespaces
    jdk_version: str | None = None
    resolve: str | None = None

@dataclass(frozen=True)
class CompiledClojureClasses:
    digest: Digest              # Contains .class files
    classpath: ClasspathEntry   # For passing to deploy_jar
```

**2. AOT Compilation Process**

```python
@rule
async def aot_compile_clojure(
    request: CompileClojureAOTRequest,
    jvm: JvmSubsystem,
    bash: BashBinary,
) -> CompiledClojureClasses:
    # 1. Get classpath for dependencies (libraries needed during compilation)
    # 2. Create compile script that:
    #    - Sets *compile-path* to output directory
    #    - Calls (compile 'namespace) for each requested namespace
    # 3. Execute: clojure -Scp <classpath> -e "(compile 'ns1) (compile 'ns2) ..."
    # 4. Capture output .class files from compile-path
    # 5. Return digest with compiled classes
```

**3. Compile Script Template**

```clojure
(do
  (binding [*compile-path* "classes"]
    (compile 'namespace.one)
    (compile 'namespace.two)))
```

#### Key Decisions

- **Transitive compilation**: When compiling a main namespace, all its dependencies are automatically compiled (Clojure behavior)
- **Error handling**: Compilation failures should produce clear error messages
- **Caching**: Compiled classes should be cached by Pants (via digest)

---

### Phase 2: Create clojure_deploy_jar Target

**Goal**: Provide a Clojure-specific target that handles AOT + packaging

**Modification**: `pants-plugins/clojure_backend/target_types.py`

#### New Target Definition

```python
class ClojureDeployJarTarget(Target):
    alias = "clojure_deploy_jar"
    core_fields = (
        *COMMON_JVM_FIELDS,
        ClojureMainNamespaceField,    # NEW: Main namespace (symbol, e.g. "my.app.core")
        ClojureAOTNamespacesField,    # NEW: Which namespaces to AOT compile
        JvmDeployJarDuplicatePolicy,  # From deploy_jar
        JvmDeployJarShadingRules,     # From deploy_jar
        JvmDeployJarExcludeFiles,     # From deploy_jar
        OutputPathField,               # Custom output path
    )
    help = "A Clojure application packaged as an executable JAR (uberjar)."
```

#### New Fields

**1. ClojureMainNamespaceField**

```python
class ClojureMainNamespaceField(StringField):
    alias = "main"
    required = True
    help = "Main namespace with -main function and (:gen-class). Example: 'my.app.core'"
```

**2. ClojureAOTNamespacesField**

```python
class ClojureAOTNamespacesField(StringSequenceField):
    alias = "aot"
    help = """
    Namespaces to AOT compile. Options:
    - Empty (default): Compile only main namespace (transitive)
    - [":all"]: Compile all project namespaces
    - ["ns1", "ns2"]: Compile specific namespaces
    """
    default = ()  # Empty = main only
```

#### Example BUILD Usage

```python
# Simple case: AOT compile main namespace only (transitive)
clojure_deploy_jar(
    name="myapp",
    main="my.app.core",  # Must have (:gen-class) and -main
    dependencies=[":src"],
)

# AOT compile all project code
clojure_deploy_jar(
    name="myapp-all-aot",
    main="my.app.core",
    aot=[":all"],
    dependencies=[":src"],
)

# AOT compile specific namespaces
clojure_deploy_jar(
    name="myapp-selective",
    main="my.app.core",
    aot=["my.app.core", "my.app.config"],
    dependencies=[":src"],
)
```

---

### Phase 3: Integrate AOT Compilation with deploy_jar

**Goal**: Wire up Clojure AOT compilation to feed into Pants' existing deploy_jar

**New File**: `pants-plugins/clojure_backend/package_clojure_deploy_jar.py`

#### Implementation Strategy

**1. Generate deploy_jar Target**

```python
@rule
async def generate_deploy_jar_from_clojure_deploy_jar(
    request: GenerateTargetsRequest,
    union_membership: UnionMembership,
) -> GeneratedTargets:
    # Parse clojure_deploy_jar target
    # Generate a corresponding deploy_jar target with:
    #   - main = converted namespace (my.app.core � my.app.core)
    #   - dependencies = [AOT compiled classes] + original dependencies
```

**2. AOT Compilation Hook**

```python
@rule
async def prepare_clojure_classes_for_deploy_jar(
    clojure_deploy_jar: ClojureDeployJarTarget,
) -> CompiledClojureClasses:
    # 1. Determine which namespaces to compile
    aot_field = clojure_deploy_jar[ClojureAOTNamespacesField]
    main_ns = clojure_deploy_jar[ClojureMainNamespaceField].value

    if ":all" in aot_field.value:
        # Compile all Clojure sources in transitive dependencies
        namespaces = await determine_all_namespaces(clojure_deploy_jar)
    elif not aot_field.value:
        # Default: compile just main (transitive)
        namespaces = (main_ns,)
    else:
        # Explicit list
        namespaces = tuple(aot_field.value)

    # 2. AOT compile
    compiled = await Get(
        CompiledClojureClasses,
        CompileClojureAOTRequest(
            namespaces=namespaces,
            source_targets=...,
            resolve=clojure_deploy_jar[JvmResolveField].value,
        )
    )

    return compiled
```

**3. Main Class Name Conversion**

- Clojure namespace: `my.app.core` with `(:gen-class)`
- Generated class name: `my.app.core` (same as namespace for default gen-class)
- Pass to deploy_jar's `main` field as-is

#### Key Integration Points

- Reuse existing `deploy_jar` packaging logic (no need to reimplement JAR creation)
- AOT compilation step produces classpath entry with `.class` files
- Pass compiled classes + dependencies to `deploy_jar`

---

### Phase 4: Validation & Developer Experience

#### 1. Validate gen-class Presence

Check that main namespace has `(:gen-class)` in its `ns` declaration:

```python
def validate_main_namespace_has_gen_class(source_file: str) -> None:
    """Ensure main namespace has (:gen-class) for executable JAR."""
    content = read_source_file(source_file)
    ns_form = parse_clojure_namespace(content)  # Reuse existing parser

    if ":gen-class" not in ns_form and "gen-class" not in ns_form:
        raise ValueError(
            f"Main namespace must include (:gen-class) in ns declaration. "
            f"Found: {ns_form}"
        )
```

#### 2. Error Messages

Provide helpful errors:
- Missing `:gen-class`: "Main namespace 'my.app.core' must include (:gen-class)"
- Missing `-main` function: Detect during compilation
- Compilation failures: Show full Clojure stacktrace

#### 3. Documentation

Add guide: `docs/clojure-deploy-jar.md` covering:
- When to use AOT compilation (uberjars only, not libraries)
- How to structure main namespace with `:gen-class`
- AOT compilation options (`:all` vs selective)
- Performance implications
- Common issues (stale classes, REPL development)

---

## Technical Considerations

### 1. Classpath Construction

**For AOT Compilation**:
```
<source directories> : <dependency JARs> : <compile-output-dir>
```

**For deploy_jar Packaging**:
```
<compiled-classes-digest> + <dependency-digests>
```

### 2. Main Class Naming

Clojure `gen-class` conventions:
- Default: Namespace becomes class name (`my.app.core` � `my.app.core`)
- Custom: `(:gen-class :name my.custom.ClassName)` � `my.custom.ClassName`
- Need to parse `:gen-class :name` if present

### 3. AOT Compilation Performance

- **Caching**: Pants will cache compiled classes by digest (automatic)
- **Transitive compilation**: Compiling main namespace may compile dozens of namespaces
- **First run**: May be slow for large projects
- **Subsequent runs**: Cached unless source changes

### 4. Multiple Resolves

AOT compilation must respect JVM resolves:
- Each resolve may have different dependency versions
- Compiled classes are specific to a resolve
- Cache key includes resolve in digest computation

---

## Comparison with Other Tools

| Feature | Leiningen | tools.build | **Pants (Proposed)** |
|---------|-----------|-------------|---------------------|
| AOT Config | `:aot` in project.clj | `compile-clj :ns-compile` | `aot` field on target |
| Main Namespace | `:main` | `:main` in uber | `main` field |
| Selective AOT | `:aot [ns1 ns2]` | `[:ns-compile [ns1 ns2]]` | `aot=["ns1", "ns2"]` |
| AOT All | `:aot :all` | `compile-clj` all sources | `aot=[":all"]` |
| Default | No AOT | Explicit only | Main namespace only |
| Uberjar | `lein uberjar` | `uber` function | `clojure_deploy_jar` target |
| Caching | Incremental | Manual | Automatic (Pants) |

**Pants Advantages**:
- Automatic caching and invalidation
- Multi-resolve support out-of-the-box
- Integration with existing JVM ecosystem
- Dependency graph visualization
- Remote execution support

---

## Implementation Roadmap

### Phase 1: AOT Compilation Core (Estimated: 2-3 days)
- [ ] Create `aot_compile.py` with compilation rule
- [ ] Implement `CompileClojureAOTRequest` and result types
- [ ] Test AOT compilation of simple namespace
- [ ] Test transitive compilation
- [ ] Test error handling

### Phase 2: Target Definition (Estimated: 1 day)
- [ ] Add `ClojureDeployJarTarget` to `target_types.py`
- [ ] Add `ClojureMainNamespaceField` and `ClojureAOTNamespacesField`
- [ ] Write unit tests for field parsing
- [ ] Document target in docstrings

### Phase 3: Integration (Estimated: 2-3 days)
- [ ] Create `package_clojure_deploy_jar.py`
- [ ] Wire AOT compilation into deploy_jar dependency graph
- [ ] Handle main class name conversion
- [ ] Test with simple application
- [ ] Test with complex multi-namespace application
- [ ] Test `:all` option

### Phase 4: Validation & Polish (Estimated: 1-2 days)
- [ ] Add gen-class validation
- [ ] Improve error messages
- [ ] Write integration tests
- [ ] Write documentation
- [ ] Test with example project

**Total Estimated Time**: 6-9 days

---

## Open Questions

1. **Custom gen-class names**: Should we parse `:gen-class :name` or require developers to specify the class name explicitly?
   - **Recommendation**: Parse it automatically for better UX

2. **Resource handling**: Should resources be automatically included in uberjars?
   - **Recommendation**: Yes, follow JVM backend conventions

3. **Ahead-of-time validation**: Should we validate `:gen-class` presence during target parsing or compilation?
   - **Recommendation**: During AOT compilation (lazy validation)

4. **Library AOT**: Should we support AOT compilation for libraries (e.g., `clojure_jar` target)?
   - **Recommendation**: No, libraries should not be AOT compiled (Clojure best practice)

---

## Success Criteria

A successful implementation will:
1.  Allow creating executable Clojure uberjars with `clojure_deploy_jar` target
2.  Support specifying main namespace with automatic `:gen-class` handling
3.  Provide options for selective AOT compilation (main-only, specific namespaces, all)
4.  Integrate seamlessly with existing `deploy_jar` packaging
5.  Respect JVM resolves and JDK versions
6.  Cache compiled classes efficiently
7.  Provide clear error messages for common mistakes
8.  Include comprehensive documentation and examples

---

## Additional Research Notes

### Clojure AOT Compilation Behavior

From the official Clojure documentation:
- All Clojure code is compiled to JVM bytecode before execution
- AOT compilation means it happens at a specific time rather than on-demand
- During AOT compilation, each file generates a loader class with `__init` appended
- Separate Java classes are generated for each function, namespace, gen-class, deftype, and defrecord
- If a namespace is AOT compiled, all namespaces it requires or uses are also AOT compiled

### When to Use AOT

AOT compilation is beneficial for:
- Delivering application binaries without source code
- Marginally speeding up application start time
- Generating classes loadable directly from Java
- Platforms that don't support custom class loaders (e.g., Android)

### Important Considerations

- AOT compilation does NOT change how code runs - it's no faster during execution
- For libraries, AOT may have negative impacts (slower REPL load times)
- Best practice: Only AOT compile for final application deployment (uberjars)

---

This plan follows industry best practices from Leiningen and tools.build while leveraging Pants' strengths in caching, dependency management, and multi-project builds.
