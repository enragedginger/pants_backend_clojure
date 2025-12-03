# Plan: Debug Pants Test Hang - Root Cause Investigation

## Status: Ready for Implementation

## Problem Summary

Tests in `pants-plugins/` hang indefinitely when:
1. A `clojure_source` target depends on `jvm_artifact(clojure)`
2. Using check or AOT compile goals
3. Running in RuleRunner tests

**Minimal reproduction:** `pants test pants-plugins/tests/test_hang_repro.py`

### Key Observations

1. **Previous fixes did not resolve the issue:**
   - Removed ToolClasspathRequest (to avoid conflicting Clojure resolutions)
   - Fixed nested `await Get()` patterns
   - Both were valid improvements but did not fix the hang

2. **Debug print statements never appeared:**
   - Added to `package.py`, `aot_compile.py`, `provided_dependencies.py`
   - None were reached during the hanging test
   - This could mean: hang before rule execution, stdout buffering, or exception swallowing

3. **The hang is specific to clojure_source -> jvm_artifact(clojure) dependency:**
   - Tests with `clojure_source` depending on `jsr305` pass
   - Tests with `clojure_source` NOT depending on any jvm_artifact pass
   - Only the combination of clojure_source + jvm_artifact(clojure) hangs

---

## Root Cause Hypotheses

### Hypothesis A: Lockfile Integrity Issue
The embedded lockfile in tests may have incorrect fingerprints, duplicate entries, or circular artifact dependencies that cause resolution to hang.

### Hypothesis B: Scheduler Deadlock in Rule Graph Resolution
The Pants scheduler may be deadlocking when building the rule graph, not during rule execution.

### Hypothesis C: Missing Auto-Injection Pattern
Scala auto-injects `scala-library`; the Clojure backend might need similar auto-injection of Clojure JAR instead of having users depend on it directly.

### Hypothesis D: Rule Graph Conflict
Multiple rules may be racing to satisfy the same artifact resolution, causing contention in the Rust scheduler.

---

## Phase 0: Validate Environment and Lockfile

**Goal:** Ensure the test setup is correct before debugging scheduler issues.

### Task 0.1: Validate Lockfile Against Coursier
```bash
# Generate a fresh lockfile for Clojure 1.11.0
coursier resolve org.clojure:clojure:1.11.0 --output /tmp/clojure.lock

# Compare fingerprints with the embedded lockfile in test_hang_repro.py
# Look for:
# - Fingerprint mismatches
# - Missing transitive dependencies
# - Circular references in dependency graph
```

### Task 0.2: Check for Duplicate Artifacts
```bash
# Extract artifact coordinates from embedded lockfile
grep -E "group|artifact|version" pants-plugins/tests/test_hang_repro.py

# Verify each appears exactly once
# Look for org.clojure:clojure appearing in multiple entries
```

### Task 0.3: Validate Target Structure
```bash
# From a temp directory with the test setup
pants peek :: 2>&1 | head -100

# Check for unexpected dependencies or cycles
pants dependencies --transitive //:example
```

### Task 0.4: Compare with Working Lockfile
Look at how other tests (e.g., test_test_runner.py) structure their lockfiles:
```bash
grep -A 50 "LOCKFILE" pants-plugins/tests/test_test_runner.py
```

---

## Phase 1: Minimal Reproduction and Bisection

**Goal:** Identify the exact point where the hang occurs by progressive test simplification.

### Task 1.1: Create Progressive Test Suite
Create these test variants in `test_hang_repro.py`:

```python
# Test A: Just create targets (no rule execution)
def test_targets_only(rule_runner):
    rule_runner.write_files({...})
    tgt = rule_runner.get_target(Address(spec_path="", target_name="example"))
    assert tgt is not None
    # Does this hang? If yes -> target parsing issue

# Test B: Resolve dependencies (not classpath)
def test_dependencies_only(rule_runner):
    from pants.engine.target import Dependencies, DependenciesRequest
    rule_runner.write_files({...})
    tgt = rule_runner.get_target(Address(...))
    deps = rule_runner.request(Dependencies, [DependenciesRequest(tgt[Dependencies])])
    # Does this hang? If yes -> dependency resolution issue

# Test C: Resolve classpath without check goal
def test_classpath_only(rule_runner):
    from pants.jvm.classpath import Classpath
    rule_runner.write_files({...})
    result = rule_runner.request(Classpath, [Addresses([Address(...)])])
    # Does this hang? If yes -> classpath/coursier issue

# Test D: Check goal with clojure_source but NO jvm_artifact dependency
def test_check_no_artifact_dep(rule_runner):
    # clojure_source WITHOUT dependencies field
    rule_runner.write_files({
        "BUILD": 'clojure_source(name="example", source="example.clj")',
        "example.clj": "(ns example)",
    })
    # Does this hang? If yes -> check goal issue unrelated to artifacts
```

### Task 1.2: Compare Rule Graphs
```bash
# Run with working test (jsr305 dependency)
pants --no-pantsd \
  --engine-visualize-to=/tmp/viz-working \
  test pants-plugins/tests/test_package_clojure_deploy_jar.py -- -v -k "test_provided_jvm_artifact_excluded_from_jar"

# Run with hanging test (clojure dependency)
timeout 30 pants --no-pantsd \
  --engine-visualize-to=/tmp/viz-hanging \
  test pants-plugins/tests/test_hang_repro.py -- -v

# Compare the rule graphs
diff /tmp/viz-working/rule_graph.dot /tmp/viz-hanging/rule_graph.dot
```

### Task 1.3: Use pytest-timeout
Add to `test_hang_repro.py`:
```python
import pytest

@pytest.mark.timeout(30)
def test_check_with_clojure_dependency(rule_runner: RuleRunner) -> None:
    ...
```

Run with verbose timeout output:
```bash
pants test pants-plugins/tests/test_hang_repro.py -- -v --timeout-method=signal
```

---

## Phase 2: Scheduler-Level Debugging

**Goal:** Get visibility into what the Pants scheduler is doing during the hang.

### Task 2.1: Run with Maximum Verbosity
```bash
# Kill any cached pantsd state
pkill -f pantsd

# Run with trace logging including Rust logs
RUST_LOG=trace RUST_BACKTRACE=1 \
pants --no-pantsd \
  --log-level=trace \
  --no-dynamic-ui \
  --print-stacktrace \
  test pants-plugins/tests/test_hang_repro.py -- -v 2>&1 | tee debug.log

# Look for:
# - Last logged rule before hang
# - Cycle detection warnings
# - "Running" or "Waiting" messages
```

### Task 2.2: Visualize Rule Graph with Sandbox Preservation
```bash
pants --no-pantsd \
  --engine-visualize-to=/tmp/pants-viz \
  --keep-sandboxes=always \
  test pants-plugins/tests/test_hang_repro.py -- -v

# Even if test hangs, graph may be written
ls -la /tmp/pants-viz/
dot -Tpdf /tmp/pants-viz/rule_graph.dot -o /tmp/rules.pdf

# Check sandbox state
find /tmp -name "__run.sh" 2>/dev/null | head -5
```

### Task 2.3: Use Streaming Workunits
```bash
pants --no-pantsd \
  --streaming-workunits-report-interval=0.5 \
  --streaming-workunits-level=trace \
  test pants-plugins/tests/test_hang_repro.py -- -v
```

---

## Phase 3: Deep Inspection During Hang

**Goal:** Capture runtime state while the test is hung.

### Task 3.1: Capture Thread Dumps
In one terminal:
```bash
# Run the hanging test
pants --no-pantsd test pants-plugins/tests/test_hang_repro.py -- -v
```

In another terminal:
```bash
# Find the Python process
ps aux | grep python | grep pants

# Get Python thread dump with py-spy
py-spy dump --pid <PID>

# Get native + Python stacks with gdb (Linux)
gdb -p <PID> -ex "thread apply all bt" -ex "quit"

# Check for stuck subprocesses
ps aux | grep -E "coursier|java" | grep -v grep
```

### Task 3.2: Check for Process Leaks
```bash
# While test is hanging
pstree -p $(pgrep -f "pants test")

# Look for zombie or stuck processes
ps aux | grep -E "coursier|java" | grep -v grep
```

### Task 3.3: Inspect RuleRunner's Scheduler Metrics
Add to test file before the hanging request:
```python
print(f"Scheduler state before request:")
print(f"  Metrics: {rule_runner.scheduler.metrics()}")

# Make the request
results = rule_runner.request(...)
```

---

## Phase 4: Coursier and Classpath Investigation

**Goal:** Determine if the issue is in artifact resolution.

### Task 4.1: Verify Lockfile Fingerprints
```python
# Add to test file:
import hashlib
import urllib.request

# Fetch actual JAR and compute fingerprint
url = "https://repo1.maven.org/maven2/org/clojure/clojure/1.11.0/clojure-1.11.0.jar"
with urllib.request.urlopen(url) as response:
    content = response.read()
    fingerprint = hashlib.sha256(content).hexdigest()
    print(f"Actual fingerprint: {fingerprint}")
    print(f"Expected: 3e21fa75a07ec9ddbbf1b2b50356cf180710d0398deaa4f44e91cd6304555947")
```

### Task 4.2: Test Classpath Resolution in Isolation
```python
# Create test that ONLY resolves classpath, no check goal
def test_classpath_resolution_only(rule_runner):
    from pants.jvm.classpath import Classpath
    from pants.engine.addresses import Addresses, Address

    rule_runner.write_files({...})
    rule_runner.set_options([...])

    # Just resolve classpath
    classpath = rule_runner.request(
        Classpath,
        [Addresses([Address(spec_path="", target_name="example")])]
    )
    print(f"Classpath entries: {classpath.args()}")
```

### Task 4.3: Compare with JVM Test Fixtures
Look at how Pants JVM tests handle lockfiles:
```bash
# Find JVMLockfileFixture usage
grep -r "JVMLockfileFixture" /Users/hopper/workspace/python/pants/src/python/pants --include="*.py"

# Find how Scala tests set up lockfiles
grep -r "lockfile" /Users/hopper/workspace/python/pants/src/python/pants/backend/scala/test --include="*test*.py"
```

---

## Phase 5: Alternative Approaches

### Task 5.1: Run Check Goal as Integration Test
```bash
# Create a temp project outside RuleRunner
mkdir -p /tmp/clojure-test/3rdparty/jvm

cat > /tmp/clojure-test/pants.toml << 'EOF'
[GLOBAL]
pants_version = "2.23.0"
backend_packages = ["pants_backend_clojure"]

[jvm]
default_resolve = "jvm-default"
resolves = { "jvm-default" = "3rdparty/jvm/default.lock" }
EOF

# Copy lockfile and source files...
# Run directly (not through RuleRunner)
cd /tmp/clojure-test
pants check ::
```

### Task 5.2: Consider Auto-Injection Pattern
Look at how Scala injects scala-library:
```bash
grep -r "scala-library" /Users/hopper/workspace/python/pants/src/python/pants/backend/scala --include="*.py" | head -20
```

If Clojure should be auto-injected similar to Scala, this might be the real fix.

### Task 5.3: File Pants Issue
If root cause is identified in Pants core, create a minimal reproduction and file:
- https://github.com/pantsbuild/pants/issues
- Include: minimal test case, rule graph visualizations, thread dumps

---

## Success Criteria

1. **Identify the exact location of the hang** (scheduler, Coursier, rule execution, etc.)
2. **Understand the root cause** (deadlock, infinite loop, waiting on impossible condition)
3. **Develop a fix or workaround** that allows tests to pass
4. **Document the findings** for future reference

---

## Quick Start Commands

```bash
# 1. Clean slate
pkill -f pantsd
rm -rf ~/.cache/pants/lmdb_store

# 2. Validate lockfile first (Phase 0)
coursier resolve org.clojure:clojure:1.11.0 --json | jq .

# 3. Run progressive tests (Phase 1)
pants test pants-plugins/tests/test_hang_repro.py -- -v -k "test_targets_only"
pants test pants-plugins/tests/test_hang_repro.py -- -v -k "test_classpath_only"

# 4. Run with maximum debugging (Phase 2)
RUST_LOG=trace RUST_BACKTRACE=1 \
pants --no-pantsd \
  --log-level=trace \
  --no-dynamic-ui \
  --print-stacktrace \
  --engine-visualize-to=/tmp/pants-viz \
  --keep-sandboxes=always \
  test pants-plugins/tests/test_hang_repro.py -- -v 2>&1 | tee debug.log

# 5. In another terminal, if it hangs (Phase 3):
ps aux | grep python | head -5
py-spy dump --pid <PID>

# 6. Examine rule graph
ls -la /tmp/pants-viz/
dot -Tpdf /tmp/pants-viz/rule_graph.dot -o /tmp/rules.pdf
```

---

## Files to Modify for Debugging

| File | Purpose |
|------|---------|
| `pants-plugins/tests/test_hang_repro.py` | Add progressive test variants, pytest.mark.timeout |
| Test file outdated comments (lines 13-29) | Update to reflect that nested pattern is already fixed |

---

## Research Summary

### Why Print Statements Don't Appear During Hangs

1. **Buffered stdout/stderr**: RuleRunner redirects output to temp files; buffer is never flushed if test hangs
2. **Rules never execute**: If hang is in scheduler/graph setup, rule code never runs
3. **Exception swallowing**: An exception might be caught silently somewhere
4. **Async context**: Rules run in Rust async tasks; Python prints may be captured differently

### Key Pants Debug Options

| Option | Purpose |
|--------|---------|
| `--log-level=trace` | Maximum Python verbosity |
| `RUST_LOG=trace` | Maximum Rust verbosity |
| `RUST_BACKTRACE=1` | Better Rust error context |
| `--no-pantsd` | Avoid daemon state issues |
| `--no-dynamic-ui` | Avoid known UI deadlock |
| `--engine-visualize-to=DIR` | Dump rule graphs |
| `--keep-sandboxes=always` | Preserve execution sandboxes |
| `--streaming-workunits-*` | Real-time progress |
| `--print-stacktrace` | Full exception details |

### Thread Dump Tools

| Tool | Command |
|------|---------|
| py-spy | `py-spy dump --pid <PID>` |
| gdb | `gdb -p <PID> -ex "thread apply all bt"` |
| pstree | `pstree -p $(pgrep -f pants)` |

---

## Notes from Previous Attempts

From `docs/plans/20251202_fix_hanging_test_provided_maven_transitives.md`:
- Debug prints in rules were never reached
- Missing rules in RuleRunner did NOT fix the issue
- Single BUILD file vs multiple did NOT affect hang
- Cross-directory references did NOT affect hang
- Only the specific dependency pattern (clojure_source -> jvm_artifact(clojure)) triggers the hang

From `docs/plans/20251203_fix_nested_await_get_pattern.md`:
- Nested `await Get()` pattern was fixed in both check.py and aot_compile.py
- Hang persisted after the fix
- Confirming the nested pattern was NOT the root cause

From `docs/plans/20251202_fix_clojure_source_clojure_artifact_hang.md`:
- ToolClasspathRequest was removed to avoid conflicting resolutions
- Tests STILL hang after removal
- The root cause is something else entirely

---

## Reviewer Feedback Incorporated

1. **Reordered phases**: Now starts with lockfile validation (Phase 0) before scheduler debugging
2. **Added bisection strategy**: Progressive test suite in Phase 1
3. **Removed manual timeout handling**: Use pytest-timeout instead
4. **Added RUST_LOG and RUST_BACKTRACE**: For Rust-level visibility
5. **Added lockfile comparison tasks**: Validate against Coursier output
6. **Added rule graph diff**: Compare working vs hanging tests
7. **Noted auto-injection pattern**: May need to follow Scala's approach
8. **Updated comments reference**: Note that test_hang_repro.py has outdated comments
