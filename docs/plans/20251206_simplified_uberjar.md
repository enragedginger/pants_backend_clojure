# Implementation Plan: Simplified Uberjar Creation

**Date**: 2025-12-06
**Status**: Ready for Implementation

## Summary

Simplify the `clojure_deploy_jar` target by removing the `aot` field entirely. Users will only specify a `main` namespace, and AOT compilation will happen transitively from that namespace. If users want to avoid AOT, they should use `clojure.main` as the main entry point.

Additionally, align our compilation with tools.build best practices by adding `:direct-linking true` and excluding common conflicting files like `LICENSE`.

## Problem Statement

### Current Complexity

The current `aot` field supports multiple modes:
- `[':none']` - Source-only JAR
- `()` (empty/default) - Compile only main namespace transitively
- `[':all']` - Compile all project namespaces
- `['ns1', 'ns2']` - Compile specific namespaces

This flexibility creates confusion:
1. Users must understand the differences between modes
2. Source-only JARs require special execution (`java -cp ... clojure.main -m ns`)
3. The `:all` mode compiles all project namespaces but still transitively compiles third-party deps
4. Explicit namespace lists require understanding Clojure's transitive compilation

### Proposed Simplification

**New API:**
```python
clojure_deploy_jar(
    name="my-app",
    main="my.app.core",  # Required: namespace with -main and (:gen-class)
    dependencies=[...],
    provided=[...],      # Optional: compile-time only deps
)
```

**Behavior:**
- Always AOT compile from `main` namespace (transitive to all required namespaces)
- Use tools.build-style options: `{:direct-linking true}`
- Exclude common conflicting files (LICENSE, etc.)
- If user wants no AOT: set `main="clojure.main"` and pass app namespace as args at runtime

### Why This Works

1. **Transitive compilation is sufficient**: When you compile a namespace, Clojure compiles all required namespaces. Compiling `main` covers everything needed for the app to run.

2. **`:all` mode is rarely needed**: The only difference between "compile main" and "compile all" is namespaces that aren't required by main. If they're not required, they don't need to be in the JAR.

3. **Source-only JARs are an edge case**: Users who truly need source-only can use `main="clojure.main"` and handle the classpath execution themselves.

4. **Direct linking is best practice**: Clojure's core library uses direct linking since 1.8. It provides faster startup and execution.

### Important Note

When `main="clojure.main"` is specified, this cannot be used with AOT compilation of that namespace. This is intentional - `clojure.main` signals "I want a source-only JAR". If someone truly wants to AOT compile while using `clojure.main` as the entry point, they would need to create a thin wrapper namespace.

## Implementation Plan

### Phase 1: Simplify Target Type

**Goal**: Remove the `aot` field and update documentation.

**Files to modify**:
- `pants-plugins/clojure_backend/target_types.py`

**Tasks**:

#### 1.1 Remove `ClojureAOTNamespacesField`

Delete the `ClojureAOTNamespacesField` class entirely (lines 239-255).

#### 1.2 Update `ClojureMainNamespaceField` documentation

```python
class ClojureMainNamespaceField(StringField):
    alias = "main"
    required = True
    help = (
        "Main namespace for the executable JAR. This namespace will be AOT compiled "
        "along with all namespaces it transitively requires.\n\n"
        "The namespace must include (:gen-class) in its ns declaration and define "
        "a -main function.\n\n"
        "Example:\n"
        "  (ns my.app.core\n"
        "    (:gen-class))\n"
        "  (defn -main [& args]\n"
        "    (println \"Hello, World!\"))\n\n"
        "To avoid AOT compilation entirely (source-only JAR), use 'clojure.main' "
        "as the main namespace and invoke your app namespace at runtime:\n"
        "  java -jar app.jar -m my.actual.namespace"
    )
```

#### 1.3 Remove `ClojureAOTNamespacesField` from `ClojureDeployJarTarget`

Update `core_fields` to remove `ClojureAOTNamespacesField` (line 294).

#### 1.4 Update `ClojureDeployJarTarget` help text

```python
help = (
    "A Clojure application packaged as an executable JAR (uberjar).\n\n"
    "The main namespace will be AOT compiled along with all its transitive "
    "dependencies, using direct linking for optimal performance. All dependencies "
    "are packaged into a single JAR file that can be executed with `java -jar`.\n\n"
    "The main namespace must include (:gen-class) in its ns declaration and "
    "define a -main function.\n\n"
    "To create a source-only JAR (no AOT compilation), use 'clojure.main' as the "
    "main namespace."
)
```

**Validation**:
- Target type tests pass
- `pants help clojure_deploy_jar` shows updated help text

---

### Phase 2: Update Packaging Logic

**Goal**: Simplify the package rule to always compile from main namespace with direct linking.

**Files to modify**:
- `pants-plugins/clojure_backend/goals/package.py`
- `pants-plugins/clojure_backend/aot_compile.py`

**Tasks**:

#### 2.1 Remove `aot` field handling from `package.py`

Update `ClojureDeployJarFieldSet` to remove the `aot` field:

```python
@dataclass(frozen=True)
class ClojureDeployJarFieldSet(PackageFieldSet):
    required_fields = (
        ClojureMainNamespaceField,
        JvmResolveField,
    )

    main: ClojureMainNamespaceField
    provided: ClojureProvidedDependenciesField
    jdk: JvmJdkField
    resolve: JvmResolveField
    output_path: OutputPathField
```

Also remove the import of `ClojureAOTNamespacesField` at the top of the file.

#### 2.2 Simplify namespace determination logic

Remove all the mode-switching logic. Specifically:
- Remove line 88: `aot_field = field_set.aot`
- Remove lines 90-95: The validation logic checking for `:none` combined with other values
- Remove lines 151-165: The complex mode-switching logic

Replace with:

```python
main_namespace = field_set.main.value

# Check for source-only mode (using clojure.main)
skip_aot = main_namespace == "clojure.main"

if skip_aot:
    namespaces_to_compile = ()
else:
    # Always compile just the main namespace - transitive compilation handles deps
    namespaces_to_compile = (main_namespace,)
```

#### 2.3 Update gen-class validation for clojure.main

Update the gen-class validation section (around lines 193-227):

```python
# Only validate gen-class if not using clojure.main
if skip_aot:
    # Source-only mode with clojure.main
    main_class_name = "clojure.main"
else:
    main_class_name = main_namespace
    # Check for (:gen-class) in the namespace declaration
    # ... existing validation code ...

    # Get the main class name from the gen-class declaration
    # ... existing gen-class :name extraction code ...
```

#### 2.4 Update manifest for clojure.main mode

When `main="clojure.main"`, the manifest should set `Main-Class: clojure.main` so the JAR is executable:

```python
if skip_aot:
    # Source-only JAR using clojure.main as entry point
    manifest_content = """\
Manifest-Version: 1.0
Main-Class: clojure.main
Created-By: Pants Build System
X-Source-Only: true
"""
else:
    # Standard manifest with application main class
    manifest_content = f"""\
Manifest-Version: 1.0
Main-Class: {main_class_name}
Created-By: Pants Build System
"""
```

Users will invoke their actual namespace at runtime: `java -jar app.jar -m my.actual.namespace`

#### 2.5 Add direct linking to AOT compilation

Update `aot_compile.py` to use direct linking. Modify the compile script (around line 107):

```python
compile_script = f"""
(do
  ;; Create classes directory if it doesn't exist
  (.mkdirs (java.io.File. "{classes_dir}"))

  ;; Compile namespaces with direct linking enabled for optimal performance
  (binding [*compile-path* "{classes_dir}"
            *compiler-options* {{:direct-linking true}}]
    {compile_statements}))
"""
```

Note: The double braces `{{` are Python f-string escaping and will produce `{:direct-linking true}` in the actual Clojure code.

#### 2.6 Add LICENSE exclusion to JAR packaging

When extracting dependency JARs (around line 443), skip LICENSE files that can conflict between dependencies:

```python
for item in dep_jar.namelist():
    # Skip META-INF (includes signatures, manifests)
    if item.startswith('META-INF/'):
        continue
    # Skip LICENSE files at root level (can conflict between deps)
    # These are typically file vs directory conflicts
    item_basename = os.path.basename(item).upper()
    if item_basename.startswith('LICENSE'):
        continue
    if item in added_entries:
        continue
    # ... rest of extraction
```

**Important**: The AOT-first, JAR-override strategy remains unchanged. This is critical for:
- Protocol extension safety (pre-compiled library classes override AOT versions)
- Source-only library support (AOT classes kept when no JAR class exists)

The existing `is_first_party_class()` and `is_provided_class()` logic continues to work as designed.

**Validation**:
- Build a simple deploy JAR and verify it works
- Verify source-only mode with `main="clojure.main"` works
- Verify LICENSE files are excluded

---

### Phase 3: Update Tests

**Goal**: Update tests to reflect the simplified API and add comprehensive JAR content validation.

**Files to modify**:
- `pants-plugins/tests/test_target_types.py`
- `pants-plugins/tests/test_package_clojure_deploy_jar.py`

**Tasks**:

#### 3.1 Remove tests for removed `aot` field modes

Delete or update the following tests:
- Tests using `aot=[":none"]` → Convert to use `main="clojure.main"`
- Tests using `aot=[":all"]` → Remove (default behavior now covers the main use case)
- Tests using `aot=["ns1", "ns2"]` → Remove (transitive from main covers this)
- Tests for validation that `:none` cannot combine with other values → Remove

Specific tests to migrate (from test_package_clojure_deploy_jar.py):
- `test_package_deploy_jar_with_aot_none` → Rename to `test_package_deploy_jar_clojure_main_source_only` and use `main="clojure.main"`
- `test_package_deploy_jar_aot_none_no_gen_class_required` → Update to use `main="clojure.main"`
- `test_package_deploy_jar_aot_none_includes_cljc` → Update to use `main="clojure.main"`
- `test_package_deploy_jar_aot_none_cannot_combine` → Remove (no longer applicable)
- `test_package_deploy_jar_aot_none_with_transitive_deps` → Update to use `main="clojure.main"`
- `test_clojure_aot_namespaces_field_default` → Remove
- `test_clojure_deploy_jar_target_has_required_fields` → Update to remove `aot` assertion
- `test_package_deploy_jar_with_aot_all` → Remove
- `test_package_deploy_jar_with_selective_aot` → Remove

#### 3.2 Example test migration: aot=[":none"] to main="clojure.main"

**Before:**
```python
def test_package_deploy_jar_with_aot_none(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({
        "src/app/BUILD": dedent("""\
            clojure_source(name="core", source="core.clj")
            clojure_deploy_jar(
                name="app",
                main="app.core",
                aot=[":none"],
                dependencies=[":core"],
            )
            """),
        "src/app/core.clj": "(ns app.core)\n(defn -main [] (println \"Hi\"))",
    })
    # ...
```

**After:**
```python
def test_package_deploy_jar_clojure_main_source_only(rule_runner: RuleRunner) -> None:
    """Test that main='clojure.main' creates a source-only JAR."""
    rule_runner.write_files({
        "src/app/BUILD": dedent("""\
            clojure_source(name="core", source="core.clj")
            clojure_deploy_jar(
                name="app",
                main="clojure.main",  # Source-only mode
                dependencies=[":core"],
            )
            """),
        # No (:gen-class) needed since we're not AOT compiling app code
        "src/app/core.clj": "(ns app.core)\n(defn -main [] (println \"Hi\"))",
    })

    field_set = ClojureDeployJarFieldSet.create(
        rule_runner.get_target(Address("src/app", target_name="app"))
    )
    result = rule_runner.request(BuiltPackage, [field_set])

    # Verify JAR was created
    assert len(result.artifacts) == 1

    # Extract and examine JAR contents
    jar_contents = rule_runner.request(DigestContents, [result.digest])
    jar_data = next(fc for fc in jar_contents if fc.path.endswith('.jar'))

    with zipfile.ZipFile(io.BytesIO(jar_data.content)) as jar:
        entries = jar.namelist()

        # Should have source files
        assert any('core.clj' in e for e in entries)

        # Should NOT have app-specific compiled classes
        app_classes = [e for e in entries if e.startswith('app/') and e.endswith('.class')]
        assert not app_classes

        # Check manifest has clojure.main as Main-Class
        manifest = jar.read('META-INF/MANIFEST.MF').decode()
        assert 'Main-Class: clojure.main' in manifest
        assert 'X-Source-Only: true' in manifest
```

#### 3.3 Update existing tests to remove `aot` field

Grep for all uses of `aot=` in test files and remove them (the default behavior is now to compile main transitively).

```bash
grep -r "aot=" pants-plugins/tests/
```

#### 3.4 Add test for LICENSE exclusion

```python
def test_package_deploy_jar_excludes_license_files(rule_runner: RuleRunner) -> None:
    """Test that LICENSE files from dependencies are excluded."""
    import io
    import zipfile

    setup_rule_runner(rule_runner)
    rule_runner.write_files({
        "locks/jvm/java17.lock.jsonc": CLOJURE_LOCKFILE,
        "3rdparty/jvm/BUILD": CLOJURE_3RDPARTY_BUILD,
        "src/app/BUILD": dedent("""\
            clojure_source(
                name="core",
                source="core.clj",
                dependencies=["3rdparty/jvm:org.clojure_clojure"],
            )

            clojure_deploy_jar(
                name="app",
                main="app.core",
                dependencies=[":core"],
            )
            """),
        "src/app/core.clj": dedent("""\
            (ns app.core
              (:gen-class))

            (defn -main [& args]
              (println "Hello"))
            """),
    })

    target = rule_runner.get_target(Address("src/app", target_name="app"))
    field_set = ClojureDeployJarFieldSet.create(target)

    result = rule_runner.request(BuiltPackage, [field_set])
    assert len(result.artifacts) == 1

    jar_path = result.artifacts[0].relpath
    jar_digest_contents = rule_runner.request(DigestContents, [result.digest])
    jar_content = next(fc.content for fc in jar_digest_contents if fc.path == jar_path)

    jar_buffer = io.BytesIO(jar_content)
    with zipfile.ZipFile(jar_buffer, 'r') as jar:
        entries = jar.namelist()

    # LICENSE files should NOT be present at root level
    license_entries = [e for e in entries if os.path.basename(e).upper().startswith('LICENSE')]
    assert len(license_entries) == 0, \
        f"LICENSE files should be excluded, but found: {license_entries}"
```

#### 3.5 Add comprehensive JAR content validation tests

These tests ensure the final JAR has the correct structure:

```python
def test_jar_manifest_structure(rule_runner: RuleRunner) -> None:
    """Verify JAR manifest has correct attributes for AOT mode."""
    # Verify: Main-Class, Created-By, no X-Source-Only

def test_jar_manifest_structure_source_only(rule_runner: RuleRunner) -> None:
    """Verify JAR manifest has correct attributes for source-only mode."""
    # Verify: Main-Class: clojure.main, X-Source-Only: true

def test_jar_contains_first_party_classes(rule_runner: RuleRunner) -> None:
    """Verify first-party AOT classes are present with correct structure."""
    # Verify: app/core.class, app/core__init.class, inner classes

def test_jar_contains_third_party_classes_from_jars(rule_runner: RuleRunner) -> None:
    """Verify third-party classes come from dependency JARs, not AOT."""
    # Verify: clojure/core*.class present (from JAR)

def test_jar_contains_clj_sources_from_deps(rule_runner: RuleRunner) -> None:
    """Verify .clj source files from dependencies are included."""
    # Verify: clojure/core.clj, etc. (Clojure includes source in JAR)

def test_jar_no_meta_inf_from_deps(rule_runner: RuleRunner) -> None:
    """Verify META-INF from dependency JARs is excluded (except manifest)."""
    # Verify: only META-INF/MANIFEST.MF, no other META-INF/* files

def test_jar_no_duplicate_entries(rule_runner: RuleRunner) -> None:
    """Verify JAR has no duplicate entries."""
    # Already exists - keep this test

def test_jar_source_only_has_first_party_sources(rule_runner: RuleRunner) -> None:
    """Verify source-only JAR includes first-party .clj/.cljc files."""
    # Verify: app/core.clj present, no app/core.class

def test_jar_source_only_excludes_first_party_classes(rule_runner: RuleRunner) -> None:
    """Verify source-only JAR does NOT include first-party classes."""
    # Verify: no app/*.class files

def test_jar_provided_deps_fully_excluded(rule_runner: RuleRunner) -> None:
    """Verify provided dependencies and their transitives are excluded."""
    # Already exists - ensure it verifies both classes and sources excluded

def test_jar_transitive_first_party_classes_included(rule_runner: RuleRunner) -> None:
    """Verify transitive first-party deps have AOT classes included."""
    # Already exists as test_transitive_first_party_classes_included - keep

def test_jar_deeply_nested_deps_included(rule_runner: RuleRunner) -> None:
    """Verify deeply nested transitive deps (A->B->C->D) work."""
    # Already exists as test_deeply_nested_transitive_deps_included - keep

def test_jar_hyphenated_namespaces_handled(rule_runner: RuleRunner) -> None:
    """Verify hyphenated namespaces (my-lib -> my_lib/) work correctly."""
    # Already exists as test_hyphenated_namespace_classes_included - keep
```

#### 3.6 Add end-to-end JAR execution test (optional)

If feasible, add a test that actually runs the JAR:

```python
def test_jar_actually_executes(rule_runner: RuleRunner) -> None:
    """End-to-end test: verify JAR can be executed with java -jar."""
    # 1. Package the JAR
    # 2. Write JAR to temp file
    # 3. Run: java -jar <jar> and capture output
    # 4. Verify output matches expected

def test_source_only_jar_executes_with_m_flag(rule_runner: RuleRunner) -> None:
    """End-to-end test: verify source-only JAR runs with -m flag."""
    # 1. Package the JAR with main="clojure.main"
    # 2. Write JAR to temp file
    # 3. Run: java -jar <jar> -m app.core
    # 4. Verify output matches expected
```

Note: These execution tests may be complex to set up in the test environment. They can be marked as integration tests or skipped in CI if needed.

#### 3.7 Verify direct linking (manual verification)

Direct linking is a compile-time optimization that affects bytecode generation. Testing it programmatically would require bytecode inspection. Instead:
- Manual verification: Build a JAR and confirm it works correctly
- The direct linking setting is straightforward (`*compiler-options*` binding)
- If direct linking causes issues, they would manifest as runtime errors (rare)

**Validation**:
- All tests pass
- No references to `aot` field remain in tests
- Run `pants test pants-plugins::` to verify

---

### Phase 4: Update Documentation

**Goal**: Update all documentation to reflect the simplified API while preserving important technical details about the AOT-first strategy.

**Files to modify**:
- `docs/aot_compilation.md`

**Tasks**:

#### 4.1 Update AOT documentation

The documentation should:
1. Explain the simplified API (just specify `main`)
2. Explain how AOT compilation works (transitive from main)
3. Document the `clojure.main` source-only mode
4. **Preserve** the technical explanation of the AOT-first, JAR-override strategy
5. Document direct linking and its implications

**Updated structure:**

```markdown
# AOT Compilation in clojure_deploy_jar

## Overview

When you create a `clojure_deploy_jar`, the main namespace is AOT compiled along
with all namespaces it transitively requires. This produces an executable JAR
that starts quickly without runtime compilation.

## Basic Usage

```python
clojure_deploy_jar(
    name="my-app",
    main="my.app.core",
    dependencies=[":lib"],
)
```

The main namespace must include `(:gen-class)` and define a `-main` function:

```clojure
(ns my.app.core
  (:gen-class))

(defn -main [& args]
  (println "Hello, World!"))
```

## Direct Linking

Compilation uses `:direct-linking true` for optimal performance. This:
- Eliminates var dereferencing overhead at call sites
- Produces faster startup times
- Creates smaller class files

**Trade-off**: With direct linking, runtime var redefinition won't affect already-compiled
call sites. If you need dynamic redefinition (e.g., for REPL development patterns in
production), mark vars with `^:redef`:

```clojure
(defn ^:redef my-configurable-fn []
  ...)
```

## Source-Only Mode

To avoid AOT compilation entirely, use `clojure.main` as your main namespace:

```python
clojure_deploy_jar(
    name="my-app-source-only",
    main="clojure.main",
    dependencies=[":my-lib"],
)
```

Then run your app by specifying the namespace at runtime:
```bash
java -jar my-app-source-only.jar -m my.actual.namespace
```

**When to use source-only mode:**
- Libraries with known AOT compatibility issues
- Development/testing where startup time is acceptable
- Maximum compatibility with dynamically-loaded code

**Trade-offs:**
- Slower startup (10-30+ seconds for compilation at runtime)
- Must remember the `-m namespace` invocation

## How It Works

[Keep the existing detailed explanation of:]
- The transitive compilation challenge
- Protocol extension issues
- The AOT-first, JAR-override solution
- Source-only library handling
- Troubleshooting section
```

#### 4.2 Update all error messages mentioning `aot`

Search for error messages in the codebase that reference the `aot` field:

```bash
grep -r "aot" pants-plugins/clojure_backend/ --include="*.py"
```

Update any user-facing error messages to reflect the new API.

**Validation**:
- Documentation is accurate and complete
- Technical details about AOT-first strategy are preserved
- No references to removed `aot` field in docs or error messages

---

### Phase 5: Verify Example Project

**Goal**: Ensure the example project works with the simplified API.

**Files to verify**:
- `projects/example/hello-app/src/BUILD`

**Tasks**:

#### 5.1 Check example BUILD file

The example project at `projects/example/hello-app/src/BUILD` should work without changes since it doesn't use the `aot` field.

#### 5.2 Manual verification

```bash
# Build standard deploy JAR
pants package //projects/example/hello-app:hello-jar

# Verify it works
java -jar dist/projects.example.hello-app/hello-jar.jar

# Optionally create a source-only variant for testing
# (would need to add a new target with main="clojure.main")
```

---

## Migration Guide

### For Users

**Before (old API):**
```python
clojure_deploy_jar(
    name="app",
    main="my.app.core",
    aot=[":none"],  # or aot=[":all"], or aot=["ns1", "ns2"]
)
```

**After (new API):**
```python
# Standard AOT (compiles main transitively) - UNCHANGED
clojure_deploy_jar(
    name="app",
    main="my.app.core",
)

# Source-only (no AOT) - use clojure.main
clojure_deploy_jar(
    name="app-source-only",
    main="clojure.main",
)
# Run with: java -jar app-source-only.jar -m my.app.core
```

### Migration for specific modes:

| Old Mode | New Equivalent | Notes |
|----------|----------------|-------|
| `aot=()` (default) | Just specify `main` | Unchanged behavior |
| `aot=[":none"]` | `main="clojure.main"` | Run with `-m namespace` |
| `aot=[":all"]` | Just specify `main` | Transitive compilation covers needed namespaces |
| `aot=["ns1", "ns2"]` | Just specify `main` | If ns1/ns2 are required by main, they'll be compiled |

---

## Testing Strategy

### Unit Tests
- Target type without `aot` field
- Main namespace validation
- `clojure.main` detection for source-only mode

### JAR Content Validation Tests (Critical)

These tests ensure the final JAR has correct contents:

| Test | What it Validates |
|------|-------------------|
| `test_jar_manifest_structure` | Main-Class set correctly, Created-By present |
| `test_jar_manifest_structure_source_only` | Main-Class: clojure.main, X-Source-Only: true |
| `test_jar_contains_first_party_classes` | AOT classes for project namespaces present |
| `test_jar_contains_third_party_classes_from_jars` | Third-party classes from JAR deps (not AOT) |
| `test_jar_no_duplicate_entries` | No duplicate entries in JAR |
| `test_jar_no_meta_inf_from_deps` | META-INF from deps excluded |
| `test_package_deploy_jar_excludes_license_files` | LICENSE files excluded |
| `test_jar_source_only_has_first_party_sources` | Source-only mode includes .clj/.cljc |
| `test_jar_source_only_excludes_first_party_classes` | Source-only mode excludes .class |
| `test_jar_provided_deps_fully_excluded` | Provided deps + transitives excluded |
| `test_jar_transitive_first_party_classes_included` | Transitive first-party AOT classes present |
| `test_jar_deeply_nested_deps_included` | Deep transitive chain (A→B→C→D) works |
| `test_jar_hyphenated_namespaces_handled` | my-lib.core → my_lib/core.class |

### Integration Tests
- Standard AOT compilation from main
- Source-only JAR with `clojure.main`
- LICENSE files excluded
- Provided dependencies still work

### End-to-End Tests (Optional)
- `test_jar_actually_executes`: Run `java -jar` and verify output
- `test_source_only_jar_executes_with_m_flag`: Run `java -jar app.jar -m ns`

### Manual Testing
```bash
# Build standard deploy JAR
pants package //projects/example/hello-app:hello-jar

# Verify it works
java -jar dist/projects.example.hello-app/hello-jar.jar

# Inspect JAR contents
jar tf dist/projects.example.hello-app/hello-jar.jar | head -50

# Check for LICENSE files (should be empty)
jar tf dist/projects.example.hello-app/hello-jar.jar | grep -i license

# Check manifest
unzip -p dist/projects.example.hello-app/hello-jar.jar META-INF/MANIFEST.MF

# Run full test suite
pants test pants-plugins::
```

---

## Success Criteria

1. [ ] `aot` field is removed from `clojure_deploy_jar`
2. [ ] Main namespace is always AOT compiled transitively
3. [ ] Direct linking (`{:direct-linking true}`) is enabled during compilation
4. [ ] LICENSE files are excluded from uberjars
5. [ ] `main="clojure.main"` creates source-only JAR with correct manifest
6. [ ] AOT-first, JAR-override strategy remains unchanged
7. [ ] All existing tests pass (updated as needed)
8. [ ] Documentation reflects new simplified API while preserving technical details
9. [ ] Example project works
10. [ ] `pants test pants-plugins::` passes

---

## Potential Issues and Mitigations

### Issue 1: Breaking Change for `aot` Field Users

**Problem**: Users with explicit `aot` fields will get errors.

**Mitigation**:
- Clear migration guide in documentation
- Error message will indicate `aot` is not a valid field (standard Pants behavior)
- For this internal plugin, the breaking change is acceptable

### Issue 2: `:all` Mode Functionality Loss

**Problem**: Users who relied on `:all` to compile namespaces not required by main.

**Mitigation**:
- Document that unrequired namespaces shouldn't be in the JAR anyway
- If truly needed, user can add explicit requires in main
- The difference is minimal in practice

### Issue 3: Direct Linking Breaks Dynamic Redefinition

**Problem**: Code using `with-redefs` or runtime var changes won't work as expected for already-compiled call sites.

**Mitigation**:
- Document in help text and docs
- Suggest using `^:redef` metadata for vars that need runtime redefinition
- Most production code doesn't need runtime redefinition
- This aligns with Clojure core's own compilation strategy since 1.8

### Issue 4: `clojure.main` Cannot Be AOT Compiled

**Problem**: Using `main="clojure.main"` always means source-only mode.

**Mitigation**:
- Document this explicitly
- Users who truly need to AOT while using clojure.main as entry can create a thin wrapper namespace

---

## References

- tools.build documentation: https://clojure.org/guides/tools_build
- tools.build API: https://clojure.github.io/tools.build/clojure.tools.build.api.html
- Direct linking: https://clojure.org/reference/compilation
- Current implementation: `pants-plugins/clojure_backend/goals/package.py`
