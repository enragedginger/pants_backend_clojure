# Clojure Check Goal Implementation Plan

## Executive Summary

This plan outlines the implementation of a `check` goal for Clojure that validates source code compiles correctly without producing artifacts. The goal is to provide fast compilation feedback for CI/CD pipelines, filling the gap between static linting (`pants lint`) and full test execution (`pants test`).

**Key Decision:** Use **namespace loading** rather than AOT compilation for checking, as it's more idiomatic for Clojure and provides the right balance of speed and thoroughness.

---

## Background

### Current State

The Clojure plugin currently has:
- `pants compile` - Constructs classpaths for runtime compilation
- `pants test` - Runs tests (which implicitly validates compilation)
- `pants lint` - Static analysis with clj-kondo
- `pants package` - Creates deploy JARs with AOT compilation

**Missing:** A dedicated `check` goal for compilation validation.

### Comparison with Scala/Java

Both Scala and Java plugins have `check` goals:
- **Scala**: `pants/backend/scala/goals/check.py` - Runs `scalac` to compile sources
- **Java**: `pants/backend/java/goals/check.py` - Runs `javac` to compile sources

These perform ahead-of-time compilation to validate code without creating packaged artifacts.

### The Clojure Challenge

Clojure's compilation model differs fundamentally from Scala/Java:

| Aspect | Scala/Java | Clojure |
|--------|------------|---------|
| **Default mode** | Ahead-of-time (AOT) | Runtime compilation |
| **Source files** | Compiled to .class | Included in classpath |
| **Validation** | Compile = check | Load = check |
| **Speed** | Slower (full compilation) | Faster (just load) |

**Implication:** For Clojure, "checking" means validating that namespaces can be loaded, not necessarily producing .class files.

---

## Design Options

### Option A: Namespace Loading (RECOMMENDED)

**Approach:** Load all namespaces in a Clojure process to verify they compile.

**How it works:**
1. Build classpath with all dependencies
2. Extract namespace declarations from source files
3. Create a loader script: `(require 'namespace1 'namespace2 ...)`
4. Execute in Clojure process
5. Report success/failure based on exit code and output

**Pros:**
-  Idiomatic for Clojure (loading is the standard validation)
-  Fast (no .class file generation)
-  Thorough (catches syntax errors, undefined symbols, macro expansion errors)
-  Validates dependencies and imports
-  Can be easily cached by Pants

**Cons:**
- L Doesn't validate AOT-specific features (:gen-class, etc.)
- L May trigger side effects in namespace initialization

**Verdict:** Best option - balances speed and thoroughness, matches Clojure idioms.

---

### Option B: AOT Compilation to Temp Directory

**Approach:** Perform full AOT compilation like `deploy_jar` but discard output.

**How it works:**
1. Build classpath
2. Run `(compile 'namespace)` for all namespaces
3. Write .class files to temporary directory
4. Delete output
5. Report compilation results

**Pros:**
-  Most thorough (validates everything including AOT features)
-  Matches behavior of Scala/Java check goals

**Cons:**
- L Slower than loading (I/O overhead)
- L Less idiomatic (AOT is not typical for dev workflow)
- L Overkill for most use cases

**Verdict:** Too heavy for a check goal, but could be offered as an option.

---

### Option C: Enhanced Static Analysis

**Approach:** Use clj-kondo with additional validation.

**How it works:**
1. Run clj-kondo with classpath awareness
2. Parse output for errors
3. Return results

**Pros:**
-  Very fast
-  No JVM startup overhead
-  Already implemented (just reuse lint)

**Cons:**
- L Less thorough than actual compilation
- L Doesn't catch runtime errors
- L Overlaps with `pants lint`

**Verdict:** Insufficient - doesn't validate actual compilation.

---

## Recommended Implementation: Option A (Namespace Loading)

### Architecture

```
pants check ::<target>
    �
ClojureCheckRequest
    �
For each ClojureCheckFieldSet:
    1. Build classpath (dependencies + sources)
    2. Extract namespaces from source files
    3. Generate loader script
    4. Execute Clojure process
    5. Collect results
    �
CheckResults (pass/fail per target)
```

### Core Components

#### 1. Field Set Definition

```python
@dataclass(frozen=True)
class ClojureCheckFieldSet(FieldSet):
    required_fields = (ClojureSourceField,)

    sources: ClojureSourceField
    dependencies: DependenciesField
    resolve: ClojureResolveField
```

#### 2. Check Request

```python
class ClojureCheckRequest(CheckRequest):
    field_set_type = ClojureCheckFieldSet
    tool_name = "Clojure check"
```

#### 3. Main Rule

```python
@rule(desc="Check Clojure compilation", level=LogLevel.DEBUG)
async def check_clojure(
    request: ClojureCheckRequest,
    jvm: JvmSubsystem,
    clojure_check: ClojureCheckSubsystem,
) -> CheckResults:
    """Validate Clojure sources by loading all namespaces."""

    if clojure_check.skip:
        return CheckResults([], checker_name="Clojure check")

    results = []

    for field_set in request.field_sets:
        # Get the classpath for this target
        classpath_request = CompileClojureSourceRequest(
            component=field_set.address,
            resolve=field_set.resolve,
        )
        classpath = await Get(RenderedClasspath, CompileClojureSourceRequest, classpath_request)

        # Get source files and extract namespaces
        sources = await Get(SourceFiles, SourceFilesRequest([field_set.sources]))
        namespaces = await _extract_namespaces(sources)

        if not namespaces:
            # No namespaces to check, skip
            continue

        # Create loader script
        loader_script = _create_loader_script(namespaces, clojure_check)

        # Prepare digest with the loader script
        loader_digest = await Get(
            Digest,
            CreateDigest([FileContent("check_loader.clj", loader_script.encode())]),
        )

        # Merge with classpath digest
        input_digest = await Get(Digest, MergeDigests([loader_digest, classpath.digest]))

        # Run the check
        process = Process(
            argv=[
                *jvm.java_command(classpath.entries),
                "clojure.main",
                "check_loader.clj",
            ],
            input_digest=input_digest,
            description=f"Check Clojure compilation: {field_set.address}",
            level=LogLevel.DEBUG,
            env={"CLASSPATH": ":".join(classpath.entries)},
        )

        result = await Get(ProcessResult, Process, process)

        results.append(
            CheckResult(
                exit_code=result.exit_code,
                stdout=result.stdout.decode(),
                stderr=result.stderr.decode(),
                partition_description=str(field_set.address),
            )
        )

    return CheckResults(results, checker_name="Clojure check")
```

### Helper Functions

#### Namespace Extraction

```python
async def _extract_namespaces(sources: SourceFiles) -> list[str]:
    """Extract namespace declarations from Clojure source files."""
    import re

    namespaces = []
    ns_pattern = re.compile(r'\(ns\s+([a-zA-Z0-9._-]+)')

    for file_path in sources.files:
        with open(file_path, 'r') as f:
            content = f.read()
            match = ns_pattern.search(content)
            if match:
                namespaces.append(match.group(1))

    return namespaces
```

**Note:** Can reuse logic from `dependency_inference.py` which already parses namespace declarations.

#### Loader Script Generation

```python
def _create_loader_script(namespaces: list[str], config: ClojureCheckSubsystem) -> str:
    """Generate a Clojure script that loads all namespaces and reports errors."""

    ns_symbols = " ".join(f"'{ns}" for ns in namespaces)

    return f'''
(require 'clojure.main)

(def failed (atom false))
(def error-messages (atom []))

(defn check-namespace [ns-sym]
  (try
    (require ns-sym)
    (println (str " Loaded: " ns-sym))
    (catch Exception e
      (reset! failed true)
      (let [msg (str " Failed to load " ns-sym ": " (.getMessage e))]
        (swap! error-messages conj msg)
        (println msg)
        (when-let [cause (.getCause e)]
          (println "  Caused by:" (.getMessage cause)))))))

(println "Checking Clojure compilation...")
(println "Namespaces to check: {len(namespaces)}")
(println)

(doseq [ns-sym [{ns_symbols}]]
  (check-namespace ns-sym))

(println)
(if @failed
  (do
    (println "Check FAILED")
    (println "Errors:")
    (doseq [msg @error-messages]
      (println "  " msg))
    (System/exit 1))
  (do
    (println "Check PASSED - All namespaces loaded successfully")
    (System/exit 0)))
'''
```

### Subsystem Configuration

```python
class ClojureCheckSubsystem(Subsystem):
    options_scope = "clojure-check"
    help = "Options for checking Clojure compilation via namespace loading."

    skip = SkipOption("check")

    use_aot = BoolOption(
        default=False,
        help=softwrap(
            """
            Use AOT compilation instead of namespace loading for checking.
            This is more thorough but slower. Useful for validating :gen-class
            and other AOT-specific features.
            """
        ),
    )

    fail_on_warnings = BoolOption(
        default=False,
        help="Treat Clojure compilation warnings as errors.",
    )

    args = ArgsListOption(
        example="-Dclojure.compiler.direct-linking=true",
        help="Additional JVM arguments to pass to the check process.",
    )
```

### Registration

```python
def rules():
    return [
        *collect_rules(),
        UnionRule(CheckRequest, ClojureCheckRequest),
    ]

def target_types():
    return [
        # Existing target types already support CheckRequest via ClojureSourceField
    ]
```

---

## Implementation Details

### Classpath Construction

Reuse existing classpath logic from `compile_clj.py`:

```python
classpath_request = CompileClojureSourceRequest(
    component=field_set.address,
    resolve=field_set.resolve,
)
classpath = await Get(RenderedClasspath, CompileClojureSourceRequest, classpath_request)
```

This ensures:
- All first-party dependencies are included
- Third-party JARs from lock files are included
- Java/Scala dependencies are on the classpath
- Correct resolve isolation

### Namespace Extraction

Reuse from `dependency_inference.py`:

```python
# Already has logic to parse (ns ...) declarations
def _parse_namespace_declaration(content: str) -> Optional[str]:
    # Extract namespace name
    ...
```

### Error Handling

The loader script should:
1. **Catch exceptions** during `require`
2. **Print clear error messages** with namespace name and error
3. **Exit with code 1** on any failure
4. **Exit with code 0** only if all namespaces load successfully

### Caching

Pants will automatically cache check results based on:
- Source file content hashes
- Dependency content hashes
- JVM version
- Configuration options

No special caching logic needed in the plugin.

### Parallelization

Pants will automatically parallelize check requests for different targets. Each target's check runs in its own process.

---

## What Gets Validated

###  Validated by Namespace Loading

1. **Syntax errors**
   ```clojure
   (defn foo [x y)  ; Missing closing bracket
   ```

2. **Undefined symbols**
   ```clojure
   (defn bar [] (unknown-function 42))
   ```

3. **Missing dependencies**
   ```clojure
   (ns myapp.core
     (:require [missing.namespace :as m]))
   ```

4. **Macro expansion errors**
   ```clojure
   (defmacro bad-macro [] (throw (Exception. "fail")))
   (bad-macro)  ; Will fail on load
   ```

5. **Type hint errors**
   ```clojure
   (defn ^String foo [] 42)  ; Returns Integer, not String
   ```

6. **Java class imports**
   ```clojure
   (import [java.util.NonexistentClass])  ; Will fail
   ```

7. **Circular dependencies** (if problematic)

### � Not Validated (Edge Cases)

1. **AOT-specific features**
   - `:gen-class` directives (only validated during AOT)
   - Reflection warnings (runtime only)

   **Solution:** Offer `--clojure-check-use-aot` flag for thorough checking

2. **Runtime type errors**
   ```clojure
   (defn add [x y] (+ x y))
   (add "string" 123)  ; Type error at runtime, not load time
   ```

   **Solution:** Tests should catch these

3. **Side effects**
   - Namespaces with top-level side effects will execute them
   - Generally acceptable (similar to Scala object initialization)

---

## Testing Strategy

### Unit Tests

Create `test_check.py` with the following test cases:

#### 1. Valid Code - Should Pass
```python
def test_check_valid_clojure_code(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({
        "src/BUILD": "clojure_sources()",
        "src/example.clj": """
            (ns example)
            (defn greet [name] (str "Hello, " name))
        """,
    })

    result = rule_runner.run_goal_rule(Check, args=["src:src"])
    assert result.exit_code == 0
```

#### 2. Syntax Error - Should Fail
```python
def test_check_syntax_error(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({
        "src/BUILD": "clojure_sources()",
        "src/bad.clj": """
            (ns bad)
            (defn broken [x y  ; Missing closing paren
        """,
    })

    result = rule_runner.run_goal_rule(Check, args=["src:src"])
    assert result.exit_code != 0
    assert "Failed to load bad" in result.stderr
```

#### 3. Undefined Symbol - Should Fail
```python
def test_check_undefined_symbol(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({
        "src/BUILD": "clojure_sources()",
        "src/undef.clj": """
            (ns undef)
            (defn foo [] (unknown-function 42))
        """,
    })

    result = rule_runner.run_goal_rule(Check, args=["src:src"])
    assert result.exit_code != 0
```

#### 4. Missing Dependency - Should Fail
```python
def test_check_missing_dependency(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({
        "src/BUILD": "clojure_sources()",
        "src/missing_dep.clj": """
            (ns missing-dep
              (:require [nonexistent.namespace :as n]))
        """,
    })

    result = rule_runner.run_goal_rule(Check, args=["src:src"])
    assert result.exit_code != 0
```

#### 5. Valid Dependencies - Should Pass
```python
def test_check_with_dependencies(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({
        "lib/BUILD": "clojure_sources()",
        "lib/helper.clj": """
            (ns helper)
            (defn help [] "helping")
        """,
        "app/BUILD": "clojure_sources(dependencies=['//lib:lib'])",
        "app/main.clj": """
            (ns main
              (:require [helper :as h]))
            (defn run [] (h/help))
        """,
    })

    result = rule_runner.run_goal_rule(Check, args=["app:app"])
    assert result.exit_code == 0
```

#### 6. Java Interop - Should Pass
```python
def test_check_java_interop(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({
        "src/BUILD": "clojure_sources()",
        "src/java_interop.clj": """
            (ns java-interop
              (:import [java.util ArrayList HashMap]))

            (defn make-list [] (ArrayList.))
            (defn make-map [] (HashMap.))
        """,
    })

    result = rule_runner.run_goal_rule(Check, args=["src:src"])
    assert result.exit_code == 0
```

#### 7. Multiple Namespaces - Should Check All
```python
def test_check_multiple_namespaces(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({
        "src/BUILD": "clojure_sources()",
        "src/ns1.clj": "(ns ns1) (defn f1 [] 1)",
        "src/ns2.clj": "(ns ns2) (defn f2 [] 2)",
        "src/ns3.clj": "(ns ns3) (defn f3 [] (unknown))",  # Error in ns3
    })

    result = rule_runner.run_goal_rule(Check, args=["src:src"])
    assert result.exit_code != 0
    assert "ns3" in result.stderr
```

#### 8. Skip Option - Should Skip
```python
def test_check_skip(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({
        "src/BUILD": "clojure_sources()",
        "src/bad.clj": "(ns bad) (defn broken",
    })

    result = rule_runner.run_goal_rule(
        Check,
        args=["--clojure-check-skip", "src:src"]
    )
    assert result.exit_code == 0  # Skipped, so passes
```

#### 9. Transitive Dependencies
```python
def test_check_transitive_dependencies(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({
        "a/BUILD": "clojure_sources()",
        "a/lib_a.clj": "(ns lib-a) (defn a [] 'a')",

        "b/BUILD": "clojure_sources(dependencies=['//a:a'])",
        "b/lib_b.clj": """
            (ns lib-b (:require [lib-a :as a]))
            (defn b [] (a/a))
        """,

        "c/BUILD": "clojure_sources(dependencies=['//b:b'])",
        "c/main.clj": """
            (ns main (:require [lib-b :as b]))
            (defn run [] (b/b))
        """,
    })

    result = rule_runner.run_goal_rule(Check, args=["c:c"])
    assert result.exit_code == 0
```

### Integration Tests

Test in a real project:

```bash
# Create test project
mkdir check-test-project
cd check-test-project

# Create valid code
cat > src/good.clj <<EOF
(ns good)
(defn hello [name] (str "Hello, " name))
EOF

# Create invalid code
cat > src/bad.clj <<EOF
(ns bad)
(defn broken [] (unknown-func 42))
EOF

# Run check
pants check src:good  # Should pass
pants check src:bad   # Should fail
pants check ::        # Should fail (bad.clj fails)
```

---

## Performance Considerations

### Speed Comparison

Estimated execution times for a medium-sized project (50 namespaces):

| Approach | Time | Notes |
|----------|------|-------|
| **Namespace loading** | ~2-5s | JVM startup + loading |
| **AOT compilation** | ~5-10s | JVM startup + compilation + I/O |
| **Static analysis (clj-kondo)** | ~0.5-1s | Native binary, no JVM |

**Recommendation:** Use namespace loading by default, offer AOT as option for thoroughness.

### Caching Benefits

Pants caching will make subsequent checks near-instant:
- First check: 2-5s (actual execution)
- Subsequent checks (no changes): <100ms (cache hit)
- Partial changes: Only re-check affected targets

### Parallelization

Pants automatically parallelizes checks across targets:
- 10 targets � 2s each = 2s total (with sufficient parallelism)
- Not 20s sequential

---

## Edge Cases and Solutions

### 1. Namespaces with Side Effects

**Issue:** Some namespaces execute code on load (database connections, file I/O, etc.)

**Solution:**
- This is acceptable behavior (similar to Scala object initialization)
- Document that check may execute side effects
- Users should avoid side effects in namespace initialization anyway (best practice)

### 2. AOT-Only Features (:gen-class)

**Issue:** `:gen-class` directives only validated during AOT compilation

**Example:**
```clojure
(ns myapp.Main
  (:gen-class
    :name myapp.Main
    :methods [^:static [main [String[]] void]]))
```

**Solution:**
- Default behavior: Load namespace (won't catch :gen-class errors)
- Advanced option: `--clojure-check-use-aot` performs full AOT compilation
- Document the tradeoff

### 3. Circular Dependencies

**Issue:** Clojure allows some circular dependencies that fail on load

**Solution:**
- Namespace loading will naturally catch these
- Report clear error messages

### 4. Reflection Warnings

**Issue:** Clojure emits reflection warnings for untyped interop

**Example:**
```clojure
(.someMethod obj)  ; Reflection warning if obj type unknown
```

**Solution:**
- Reflection warnings go to stdout, not stderr
- Add `--clojure-check-fail-on-warnings` option to treat warnings as errors
- Alternatively: Use `*warn-on-reflection*` in loader script

### 5. Different Clojure Versions

**Issue:** Different resolves may use different Clojure versions

**Solution:**
- Check uses the same resolve mechanism as compile/test
- Each target checks with its declared Clojure version
- No special handling needed

### 6. Empty Source Sets

**Issue:** Target has no `.clj` files or no namespaces

**Solution:**
- Skip check silently (no error)
- Similar to how empty test targets are handled

---

## Alternative: AOT-Based Check (Optional Enhancement)

For users who want maximum validation, offer an AOT-based check mode:

### Implementation

```python
@rule(desc="Check Clojure compilation via AOT")
async def check_clojure_aot(
    request: ClojureCheckRequest,
    clojure_check: ClojureCheckSubsystem,
) -> CheckResults:
    """Validate Clojure sources by AOT compiling to temp directory."""

    if not clojure_check.use_aot:
        raise AssertionError("AOT check called but not enabled")

    results = []

    for field_set in request.field_sets:
        # Reuse logic from aot_compile.py
        aot_request = ClojureAOTCompileRequest(
            component=field_set.address,
            resolve=field_set.resolve,
            namespaces=":all",  # Compile everything
        )

        try:
            compiled = await Get(CompiledClojureClasses, ClojureAOTCompileRequest, aot_request)
            # AOT succeeded, report success
            results.append(CheckResult(exit_code=0, stdout="AOT compilation successful"))
        except ProcessExecutionFailure as e:
            # AOT failed, report error
            results.append(
                CheckResult(
                    exit_code=e.exit_code,
                    stdout=e.stdout,
                    stderr=e.stderr,
                )
            )

    return CheckResults(results, checker_name="Clojure check (AOT)")
```

### Configuration

```python
class ClojureCheckSubsystem(Subsystem):
    use_aot = BoolOption(
        default=False,
        help=softwrap(
            """
            Use AOT compilation for checking instead of namespace loading.

            Namespace loading (default) is faster and sufficient for most cases.
            AOT compilation is more thorough and validates :gen-class and other
            AOT-specific features, but is slower.
            """
        ),
    )
```

### Usage

```bash
# Fast check (namespace loading)
pants check ::

# Thorough check (AOT compilation)
pants --clojure-check-use-aot check ::
```

---

## Migration Path

### Phase 1: Basic Implementation (Day 1)

1. Implement `ClojureCheckFieldSet`
2. Implement `check_clojure` rule with namespace loading
3. Add basic subsystem with skip option
4. Write core tests (valid code, syntax errors, missing deps)
5. Register with check goal

**Deliverable:** Working `pants check` for Clojure with namespace loading.

### Phase 2: Enhancements (Day 2)

1. Add `--clojure-check-fail-on-warnings` option
2. Add `--clojure-check-args` for custom JVM args
3. Improve error messages in loader script
4. Add more test cases (transitive deps, Java interop)
5. Document behavior and options

**Deliverable:** Production-ready check goal with good error messages.

### Phase 3: Advanced Features (Optional)

1. Implement `--clojure-check-use-aot` for AOT-based checking
2. Add debug logging
3. Performance optimization
4. Integration tests with real projects

**Deliverable:** Feature parity with Scala/Java check goals.

---

## File Structure

New files to create:

```
pants-plugins/clojure_backend/
  goals/
    __init__.py
    check.py                 # Main implementation

  subsystems/
    clojure_check.py         # Configuration subsystem

tests/
  test_check.py             # Unit tests
```

Modifications to existing files:

```
pants-plugins/clojure_backend/
  __init__.py               # Register check rules
  target_types.py           # No changes needed (already have SourceField)
```

---

## Documentation

Update the following docs:

### User-Facing Documentation

**docs/check-goal.md** (new file):
```markdown
# Checking Clojure Compilation

The `pants check` goal validates that Clojure source code compiles correctly
without producing artifacts.

## Usage

```bash
# Check all Clojure targets
pants check ::

# Check specific target
pants check src/myapp:lib

# Check with options
pants --clojure-check-fail-on-warnings check ::
```

## How It Works

By default, `pants check` validates Clojure code by loading all namespaces
in a Clojure process. This catches:

- Syntax errors
- Undefined symbols
- Missing dependencies
- Macro expansion errors
- Java class imports

This is faster than AOT compilation and sufficient for most cases.

## Options

- `--clojure-check-skip`: Skip checking
- `--clojure-check-fail-on-warnings`: Treat warnings as errors
- `--clojure-check-use-aot`: Use AOT compilation (slower, more thorough)
- `--clojure-check-args`: Additional JVM arguments

## Comparison with Other Goals

| Goal | Purpose | Speed | Thoroughness |
|------|---------|-------|--------------|
| `check` | Validate compilation | Fast | Medium |
| `lint` | Static analysis | Fastest | Low |
| `test` | Run tests | Slow | High |

## Advanced: AOT-Based Checking

For maximum validation (including :gen-class), use AOT mode:

```bash
pants --clojure-check-use-aot check ::
```

This is slower but validates all AOT-specific features.
```

### README.md Update

Add to features list:
```markdown
- [x] **`check` goal**: Validate compilation (namespace loading or AOT)
```

### Reference Documentation

Add to subsystems documentation:

**[clojure-check]**
- `skip`: Skip Clojure checking
- `use_aot`: Use AOT compilation instead of namespace loading
- `fail_on_warnings`: Treat warnings as errors
- `args`: Additional JVM arguments

---

## Success Criteria

### Functional Requirements

-  `pants check` validates Clojure source files
-  Catches syntax errors, undefined symbols, missing dependencies
-  Integrates with Pants caching and parallelization
-  Respects resolve boundaries
-  Clear error messages
-  Can be skipped with `--clojure-check-skip`

### Non-Functional Requirements

-  Fast execution (<5s for medium projects on cache miss)
-  Instant execution on cache hit
-  Parallel execution across targets
-  Clear documentation
-  Comprehensive tests (>10 test cases)

### Parity with Scala/Java

-  Similar invocation: `pants check ::`
-  Similar performance characteristics
-  Similar error reporting
- � Different implementation (namespace loading vs AOT) - OK, idiomatic for Clojure

---

## Timeline

### Day 1 (4-6 hours)

- [ ] Create `goals/check.py` with basic implementation
- [ ] Create `subsystems/clojure_check.py` with configuration
- [ ] Implement namespace extraction (reuse from dependency_inference)
- [ ] Implement loader script generation
- [ ] Register with check goal
- [ ] Write 5 core tests
- [ ] Manual testing

### Day 2 (2-4 hours)

- [ ] Add configuration options (fail-on-warnings, args)
- [ ] Improve error messages
- [ ] Write 5 more tests
- [ ] Write documentation (check-goal.md)
- [ ] Update README.md
- [ ] Integration testing

### Optional (4-6 hours)

- [ ] Implement AOT-based check mode
- [ ] Add debug logging
- [ ] Performance tuning
- [ ] Advanced tests

**Total Estimated Effort:** 10-16 hours (with optional enhancements)

---

## Open Questions

### 1. Should we validate reflection warnings by default?

**Options:**
- A: Ignore reflection warnings (default Clojure behavior)
- B: Report but don't fail on reflection warnings
- C: Fail on reflection warnings

**Recommendation:** A (ignore) - Add `--clojure-check-fail-on-warnings` for users who want stricter checking.

### 2. Should we support alternative check modes?

**Options:**
- A: Only namespace loading
- B: Namespace loading (default) + AOT (optional)
- C: Multiple modes (loading, AOT, static analysis)

**Recommendation:** B - Simple default, advanced option for thorough checking.

### 3. Should we execute side effects during namespace loading?

**Context:** Some namespaces have side effects (def statements, database connections, etc.)

**Options:**
- A: Allow side effects (current plan)
- B: Try to sandbox/prevent side effects
- C: Warn about side effects

**Recommendation:** A - Sandboxing is complex and not idiomatic for Clojure. Document that check may execute side effects.

### 4. How should we handle :gen-class and other AOT features?

**Options:**
- A: Don't validate them in default mode
- B: Always use AOT compilation
- C: Provide optional AOT mode

**Recommendation:** C (already in plan) - Fast default, thorough option.

---

## Related Work

### Pants Check Goal Architecture

From `/src/python/pants/core/goals/check.py`:

```python
@dataclass(frozen=True)
class CheckRequest(EngineAwareParameter):
    """Union request for any tool that can check source files."""
    field_set_type: ClassVar[Type[FieldSet]]
    tool_name: ClassVar[str]

@dataclass(frozen=True)
class CheckResult:
    exit_code: int
    stdout: str
    stderr: str
    partition_description: str | None = None

@dataclass(frozen=True)
class CheckResults:
    results: tuple[CheckResult, ...]
    checker_name: str
```

### Existing Scala Implementation

From `/src/python/pants/backend/scala/goals/check.py`:

```python
class ScalacCheckRequest(CheckRequest):
    field_set_type = ScalacCheckFieldSet
    tool_name = "scalac"

@rule
async def scalac_check(request: ScalacCheckRequest, ...) -> CheckResults:
    # Compile with scalac
    # Return results
    ...
```

Our Clojure implementation will follow this same pattern.

---

## Conclusion

The check goal for Clojure will:

1. **Validate compilation** by loading namespaces (default) or AOT compiling (optional)
2. **Integrate seamlessly** with Pants (caching, parallelization, error reporting)
3. **Be idiomatic** for Clojure (namespace loading is standard validation)
4. **Be fast** (2-5s for medium projects, instant on cache hit)
5. **Be thorough** (catches syntax, undefined symbols, missing deps, macro errors)
6. **Match Scala/Java** in user experience (similar invocation and behavior)

**Implementation effort:** 6-10 hours for basic implementation, 10-16 hours with enhancements.

**Recommendation:** Implement Phase 1 (basic namespace loading) immediately, Phase 2 (enhancements) within a week, Phase 3 (AOT mode) as optional follow-up based on user feedback.

---

**Next Steps:**
1. Review this plan
2. Create implementation branch
3. Implement Phase 1 (basic check goal)
4. Test with real projects
5. Iterate based on feedback

