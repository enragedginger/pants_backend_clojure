# Clojure REPL Workflow Redesign

## Problem Statement

The current Pants Clojure REPL workflow has a significant usability issue: when you start a REPL with a target, only that target's transitive dependencies are loaded. If you add a dependency from that target to another source target while the REPL is running, the REPL doesn't know about the new namespace and you must manually load it every time.

This creates a poor developer experience compared to traditional Clojure workflows where:
- All project sources are available on the classpath
- New files can be discovered automatically
- Code reloading tools (tools.namespace, clj-reload) work seamlessly

## Current Implementation

### How It Works

The REPL implementation (`pants-plugins/clojure_backend/clj_repl.py`) uses:

1. **Target-based loading**: Takes specific target addresses and resolves their transitive dependencies
2. **Workspace mode**: `run_in_workspace=True` allows live file reloading from workspace via "." in classpath
3. **Classpath construction**: `[".", *classpath.args(), *tool_classpath.classpath_entries()]`
   - "." = workspace directory (for live source access)
   - classpath.args() = JARs for transitive deps of specified targets
   - tool_classpath = nREPL or Rebel Readline libraries

### What Works Well

- Live file reloading for files already on classpath (no restart needed)
- Hermetic dependency management via lock files
- Multiple REPL variants (standard, nREPL, Rebel Readline)
- Support for multiple JDK versions via resolves

### What's Broken

- **Adding new dependencies**: New source files not in the original target graph aren't available
- **IDE workflow mismatch**: Traditional Clojure IDEs expect all project sources on classpath
- **Manual namespace loading**: Developers must manually `(require)` new namespaces instead of using editor commands

## Research Findings

### Traditional Clojure Workflows

#### deps.edn Approach (Standard)

The `deps.edn` file is the standard way to configure Clojure projects:

```clojure
{:paths ["src" "resources"]
 :deps {org.clojure/clojure {:mvn/version "1.12.0"}
        com.google.guava/guava {:mvn/version "33.0.0-jre"}}
 :aliases {:dev {:extra-paths ["dev" "test"]
                 :extra-deps {nrepl/nrepl {:mvn/version "1.4.0"}}}}}
```

**Key characteristics**:
- `:paths` lists all source directories (added to classpath beginning)
- `:deps` specifies third-party dependencies (Maven, git, local)
- `:aliases` provide environment-specific configurations
- All sources in `:paths` are immediately available in REPL
- Tools like `add-libs` (Clojure 1.12+) allow dynamic dependency loading

#### IDE Integration Patterns

**How IDEs consume deps.edn**:
- **Cursive**: Directly reads deps.edn, builds classpath, starts nREPL with full project context
- **Calva**: Uses jack-in to start REPL with cider-nrepl middleware and project classpath
- **Standard workflow**: IDE starts `clj -M:dev:test -m nrepl.server`, which includes all `:paths`

**REPL Reloading Workflow**:
- `(require 'namespace :reload)` - Reload a single namespace
- `tools.namespace/refresh` - Smart reload based on dependency graph
- `clj-reload` - Modern alternative with better tracking
- All rely on source files being on classpath already

### Comparison with Other Build Tools

#### Bazel Clojure Integration

**Challenges found**:
- rules_clojure generates BUILD files FROM deps.edn (opposite direction)
- IDE integration is "hacky" - doesn't expose runtime deps properly
- Community recognizes need for deps.edn generation but no mature solution exists

**Insight**: Pants is better positioned than Bazel because of:
- Build Server Protocol (BSP) support
- Export goal for IDE metadata
- Better integration philosophy

#### Pants Python REPL

**What Pants does for Python**:
- `pants repl` opens REPL with target + dependencies
- `pants export` generates metadata for IDEs (`.pants.d/export/`)
- BSP support for IntelliJ integration
- Supports both default Python shell and IPython

**Pattern to follow**: Export/generate approach for IDE tooling

## Solution Options

### Option 1: Generate deps.edn per Resolve (RECOMMENDED)

Create a new Pants goal: `pants generate-deps-edn [--resolve=java21]`

**What it generates**:

```clojure
{:paths ["projects/example/project-a/src/example/project_a/core.clj"
         "projects/example/project-a/src/example/project_a/utils.clj"
         "projects/example/project-b/src"]
 :deps {org.clojure/clojure {:mvn/version "1.12.0" :exclusions [*]}
        com.google.guava/guava {:mvn/version "33.0.0-jre" :exclusions [*]}
        junit/junit {:mvn/version "4.13.2" :exclusions [*]}}
 :aliases {:nrepl {:extra-deps {nrepl/nrepl {:mvn/version "1.4.0"}}}
           :test {:extra-paths ["projects/example/project-a/test/example/project_a/core_test.clj"
                                "projects/example/project-b/test"]}}}
```

**How it works**:

1. **Gather all Clojure sources** in the specified resolve
   - Use `Targets` to find all `clojure_source` and `clojure_test` targets
   - Filter by resolve (e.g., `java21`)
   - **Differentiate between target types**:
     - `clojure_source` (singular): Generated from `clojure_sources`, points to **individual file** → include file path
     - `clojure_sources` (plural): User-defined target → include **directory** (parent of sources)
   - This allows fine-grained control when mixing resolves in same directory

2. **Extract third-party dependencies** from lock file
   - Parse `locks/jvm/{resolve}.lock.jsonc`
   - Convert each entry to deps.edn format with `:exclusions [*]`:
     - `{group}/{artifact} {:mvn/version "version" :exclusions [*]}`
   - **Exclusions prevent transitive deps** (Pants lock file already flattened all transitives)

3. **Write deps.edn** to project root
   - Default: `deps.edn` (project root)
   - Optionally: custom path via `--output-path`

**Usage workflow**:

```bash
# Generate deps.edn for a resolve (writes to project root)
pants generate-deps-edn --resolve=java21

# Start REPL using generated deps.edn (outside Pants)
clj -M:nrepl -m nrepl.server

# Or use with IDE - Cursive/Calva auto-discover deps.edn in project root
```

**Advantages**:
-  Works with existing Clojure tooling (no special Pants knowledge needed)
-  Full IDE support out of the box (Cursive, Calva, etc.)
-  All project sources available immediately
-  Standard Clojure reload workflows work (tools.namespace, etc.)
-  Can still use `pants repl` for Pants-managed workflow
-  Aligns with Pants export philosophy

**Disadvantages**:
- � Two REPL workflows (Pants-managed vs. deps.edn)
- � deps.edn becomes stale if dependencies change (need to regenerate)
- � Workspace structure visible in paths (but this is fine for local dev)

**Implementation complexity**: Medium

**Key files to create/modify**:
- Create `pants-plugins/clojure_backend/deps_edn_export.py` (new goal)
- Register goal in `pants-plugins/clojure_backend/register.py`
- Add tests in `pants-plugins/clojure_backend/tests/test_deps_edn_export.py`

---

### Option 2: Auto-load All Resolve Sources in REPL

Modify `pants repl` to automatically include all sources in a resolve.

**How it works**:

When user runs `pants repl --resolve=java21 path/to/file.clj`, instead of:
1. Loading only transitive deps of that target

Do:
1. Find ALL `clojure_source` targets in `java21` resolve
2. Add all their source roots to classpath
3. Start REPL with complete resolve context

**Advantages**:
-  Single REPL workflow (everything through Pants)
-  All sources immediately available
-  Automatic - no manual generation step

**Disadvantages**:
- L Breaks Pants hermetic philosophy (loading unrelated code)
- L Slower REPL startup (more sources to process)
- L Doesn't solve IDE integration problem
- L Still need to regenerate classpath on dependency changes

**Implementation complexity**: Low

**Verdict**: Solves half the problem (REPL) but not IDE integration. Not recommended as sole solution.

---

### Option 3: Dynamic Dependency Discovery

Make REPL watch for BUILD file changes and dynamically reload classpath.

**How it works**:
1. REPL starts with initial target dependencies
2. Watch for BUILD file changes in background
3. When dependency added, automatically fetch and add to classpath
4. Use Clojure 1.12's `add-libs` to dynamically load new deps

**Advantages**:
-  No manual reloading needed
-  Feels magical and seamless

**Disadvantages**:
- L Very complex implementation (file watching, incremental updates)
- L Doesn't solve IDE integration
- L Requires Clojure 1.12+ for `add-libs`
- L May have weird edge cases (circular deps, resolve conflicts)

**Implementation complexity**: Very High

**Verdict**: Too complex for the benefit. Better to use Option 1.

---

### Option 4: Build Server Protocol (BSP) Integration

Implement BSP server for Clojure to integrate with IDEs directly.

**How it works**:
- IDE (IntelliJ, VS Code) connects to Pants via BSP
- Pants provides classpath, source roots, dependencies
- IDE uses this to configure nREPL and language features

**Advantages**:
-  Professional-grade IDE integration
-  Works across multiple IDEs
-  Pants controls the dependency story

**Disadvantages**:
- L Very complex (BSP is a large protocol)
- L May not be worth it for Clojure alone
- L Requires IDE plugin support
- L Longer development timeline

**Implementation complexity**: Very High

**Verdict**: Best long-term solution, but too much work for now. Could be future enhancement.

---

### Option 5: Hybrid Approach (deps.edn + Enhanced REPL)

Combine Option 1 (deps.edn generation) with Option 2 (auto-load resolve sources).

**How it works**:
1. `pants generate-deps-edn` for IDE integration
2. `pants repl` enhanced to load all sources in resolve by default (with flag to disable)
3. Both workflows available depending on use case

**Usage**:

```bash
# For IDE users
pants generate-deps-edn --resolve=java21
# Then open project in Cursive/Calva with generated deps.edn

# For command-line REPL users
pants repl --resolve=java21 [target]  # Loads all java21 sources
pants repl --hermetic [target]        # Old behavior (only target deps)
```

**Advantages**:
-  Best of both worlds
-  IDE users get standard Clojure experience
-  CLI users get improved Pants REPL
-  Flexibility for different workflows

**Disadvantages**:
- � Two code paths to maintain
- � Need to document both workflows clearly

**Implementation complexity**: Medium-High

**Verdict**: Best pragmatic solution if we want to support both use cases.

---

## Recommendations

### Immediate Action: Option 1 (Generate deps.edn)

**Why**:
1. Solves the stated problem (adding deps while REPL running)
2. Enables standard Clojure IDE workflows
3. Moderate complexity, high value
4. Doesn't require changes to REPL implementation
5. Aligns with how Pants handles other ecosystems (export goals)

**Implementation plan**:

**Phase 1: Core Goal Implementation**
- Create `GenerateDepsEdn` goal/subsystem
- Implement source root discovery per resolve
- Parse lock file and convert to deps.edn format
- Write generated deps.edn to `.pants.d/clojure/{resolve}/deps.edn`

**Phase 2: Enhanced Features**
- Add `:aliases` for common configurations (:test, :nrepl)
- Support custom output path via option
- Add `--watch` mode to regenerate on changes
- Integrate with `pants export` if possible

**Phase 3: Documentation**
- Document IDE setup (Cursive, Calva)
- Create quickstart guide
- Add troubleshooting section

### Future Enhancement: Option 5 (Hybrid)

After Option 1 is stable, consider also implementing Option 2 (auto-load resolve sources) to improve the `pants repl` command-line experience. This would require:

1. Add `--load-all-resolve-sources` flag to `pants repl` (default true)
2. Modify REPL rules to gather all resolve sources when flag is true
3. Document the two modes (full vs. hermetic)

### Not Recommended

- **Option 3** (Dynamic Discovery): Too complex, fragile
- **Option 4** (BSP): Future consideration, too much work now

## Technical Deep Dive: Implementation Details

### deps.edn Generation Algorithm

```python
@dataclass(frozen=True)
class GenerateDepsEdnRequest:
    resolve: str
    output_path: str | None = None

@rule
async def generate_deps_edn(request: GenerateDepsEdnRequest, jvm: JvmSubsystem) -> DepsEdnFile:
    # 1. Find all Clojure targets in resolve
    all_targets = await Get(AllTargets)
    clojure_targets = [
        t for t in all_targets
        if (t.has_field(ClojureSourceField) or t.has_field(ClojureTestSourceField)) and
        t[JvmResolveField].normalized_value(jvm) == request.resolve
    ]

    # 2. Differentiate between file-level and directory-level targets
    paths = set()
    for target in clojure_targets:
        # Check if this is a generated target (clojure_source) or user-defined (clojure_sources)
        if target.target_type == ClojureSourceTarget:
            # Generated target from clojure_sources - points to individual file
            # Include the specific file path
            source_files = target[ClojureSourceField].value
            if source_files:
                # Typically one file per generated target
                file_path = target.address.spec_path + "/" + source_files[0]
                paths.add(file_path)
        else:
            # User-defined clojure_sources target - include source root directory
            source_root = determine_source_root(target)
            paths.add(source_root)

    # 3. Parse lock file for third-party deps with :exclusions [*]
    lock_file_path = jvm.resolves[request.resolve]
    lock_content = await Get(DigestContents, PathGlobs([lock_file_path]))
    dependencies = parse_lock_file_to_deps_with_exclusions(lock_content)

    # 4. Generate deps.edn content
    deps_edn = {
        "paths": sorted(paths),
        "deps": dependencies,
        "aliases": generate_aliases(request.resolve)
    }

    # 5. Write to project root by default
    output = request.output_path or "deps.edn"
    return DepsEdnFile(path=output, content=format_edn(deps_edn))

def parse_lock_file_to_deps_with_exclusions(lock_content: str) -> dict:
    """Parse Pants lock file and convert to deps.edn format with :exclusions [*].

    This prevents pulling transitive dependencies since Pants lock file
    already has all transitives flattened.

    Example output:
    {
      'org.clojure/clojure': {:mvn/version "1.12.0", :exclusions [*]},
      'com.google.guava/guava': {:mvn/version "33.0.0-jre", :exclusions [*]}
    }
    """
    # Parse JSONC lock file
    # For each entry, create: {group/artifact {:mvn/version version :exclusions [*]}}
    pass
```

### Source Root vs. File Path Determination

**Challenge**: We need to differentiate between:
1. **Generated targets** (`clojure_source`, singular) → Point to specific file
2. **User-defined targets** (`clojure_sources`, plural) → Point to directory

**Why this matters**: Imagine this scenario:
```
src/example/
  ├── foo.clj       # resolve=java21
  └── bar.clj       # resolve=java17
```

If we only used directory paths, `deps.edn` for java21 would include the entire `src/example/` directory, inadvertently loading `bar.clj` which is in a different resolve.

**Solution**: Use target type to determine granularity:

**For generated `clojure_source` targets**:
- File: `projects/example/project-a/src/example/project_a/core.clj`
- Target: `core.clj:../../java21` (generated from `clojure_sources`)
- Include in `:paths`: `"projects/example/project-a/src/example/project_a/core.clj"`

**For user-defined `clojure_sources` targets**:
- Target: `clojure_sources(name="java21", resolve="java21", sources=["**/*.clj"])`
- Determine source root from namespace:
  - Read file, parse namespace: `example.project-a.core`
  - Convert to path: `example/project_a`
  - Source root: `projects/example/project-a/src`
- Include in `:paths`: `"projects/example/project-a/src"`

**Trade-off**: Individual file paths are verbose but necessary for correctness when mixing resolves.

### Lock File Parsing

Pants lock files are JSONC format with structure:

```jsonc
{
  "entries": [
    {
      "coord": {
        "group": "org.clojure",
        "artifact": "clojure",
        "version": "1.12.0"
      }
    }
  ]
}
```

Convert to deps.edn with `:exclusions [*]`:
```clojure
{org.clojure/clojure {:mvn/version "1.12.0" :exclusions [*]}}
```

**Why `:exclusions [*]`?**
- Pants lock files are already fully resolved with all transitive dependencies flattened
- Each dependency in the lock file is an explicit entry
- If we don't exclude transitives, tools.deps will re-resolve and might pull different versions
- `[*]` means "exclude all transitive dependencies"
- This ensures deps.edn exactly matches what Pants resolved

## Alternative Considered: Extending `pants repl` Only

Initially considered just fixing `pants repl` without deps.edn generation. This would help command-line REPL users but not IDE users. Given that most professional Clojure development happens in an IDE (Cursive, Calva, Emacs with CIDER), solving IDE integration is critical for adoption.

deps.edn generation solves both problems:
- IDE users use standard Clojure tooling
- Command-line users can still use `pants repl` or `clj` with generated deps.edn

## Success Metrics

After implementation, measure success by:

1. **Functional**: Can add dependency to source file and immediately use it in REPL without restart
2. **IDE Integration**: Cursive/Calva can open project and get full autocomplete/navigation
3. **Usability**: Developers familiar with Leiningen/deps.edn can transition to Pants smoothly
4. **Performance**: deps.edn generation completes in <2s for typical project

## Open Questions

1. **Source root detection**: Should we infer from namespace or require explicit configuration?
   - **Answer**: Infer from namespace (match existing Pants philosophy of convention over configuration)

2. **deps.edn location**: Project root or .pants.d/?
   - **Answer**: Project root `deps.edn` (IDE auto-discovery), but allow custom path via flag
   - For multiple resolves, recommend `deps-java21.edn`, `deps-java17.edn` pattern

3. **Multiple resolves**: How to handle projects using both java17 and java21?
   - **Answer**: Generate separate files per resolve with naming convention (e.g., `--output-path=deps-java21.edn`)
   - IDEs can be configured to load specific deps file

4. **Regeneration strategy**: Auto-regenerate or manual command?
   - **Answer**: Manual by default, optional `--watch` mode for future enhancement

## References

- [Clojure deps.edn Reference](https://clojure.org/reference/deps_edn)
- [tools.namespace](https://github.com/clojure/tools.namespace)
- [Pants Python REPL](https://www.pantsbuild.org/docs/python-repl-goal)
- [Pants BSP Support](https://www.pantsbuild.org/docs/bsp)
- [Cursive deps.edn Integration](https://cursive-ide.com/)
- [Calva Jack-in](https://calva.io/connect/)
