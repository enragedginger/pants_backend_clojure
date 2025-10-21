# Clojure Pants Plugin - Refactoring Analysis
**Date:** October 20, 2025
**Status:** Comprehensive Analysis

## Executive Summary

This document provides a comprehensive analysis of the Clojure Pants plugin codebase, identifying refactoring opportunities, code quality issues, architectural concerns, and potential feature improvements. The analysis is based on a thorough review of all 27 Python files comprising the plugin.

**Key Findings:**
- **Major code duplication** in dependency inference (~200 lines duplicated)
- **REPL implementation duplication** (~100 lines duplicated 3x)
- **Missing abstractions** for common operations
- **Opportunities for performance optimization** via parallel processing
- **Several potential feature enhancements** to improve developer experience

---

## 1. Code Duplication Analysis

### 1.1 Critical: Dependency Inference Duplication

**Location:** `dependency_inference.py:267-363` and `dependency_inference.py:366-460`

**Problem:**
The functions `infer_clojure_source_dependencies` and `infer_clojure_test_dependencies` contain nearly identical logic (~200 lines duplicated):
- Parsing required namespaces
- Parsing imported Java classes
- Converting namespaces to file paths
- Finding owners with glob patterns
- Filtering by resolve
- Handling Java imports via SymbolMapping

**Impact:**
- Difficult to maintain (bug fixes need to be applied twice)
- Risk of inconsistency between source and test inference
- Violates DRY principle

**Recommended Refactoring:**
```python
# Create a generic inference rule
@rule(desc="Infer Clojure dependencies", level=LogLevel.DEBUG)
async def infer_clojure_dependencies(
    request: Union[InferClojureSourceDependencies, InferClojureTestDependencies],
    jvm: JvmSubsystem,
    symbol_mapping: SymbolMapping,
) -> InferredDependencies:
    """Unified dependency inference for both source and test files."""
    # Extract field set (works for both types)
    field_set = request.field_set

    # Get the appropriate source field based on type
    source_field = (field_set.source if hasattr(field_set, 'source')
                   else field_set.test_source)

    # ... shared logic ...
```

**Estimated Impact:** Reduces ~200 lines to ~100 lines, single point of maintenance.

---

### 1.2 High Priority: REPL Implementation Duplication

**Location:** `clj_repl.py` - three similar functions:
- `create_clojure_repl_request` (lines 264-344)
- `create_nrepl_request` (lines 354-474)
- `create_rebel_repl_request` (lines 484-592)

**Problem:**
All three REPL implementations share 80-90% identical code:
1. Address resolution logic (with `load_resolve_sources` handling)
2. Classpath and transitive targets gathering
3. Source root gathering via `_gather_source_roots`
4. JDK environment setup
5. Workspace path preparation via `_prepare_repl_for_workspace`

Only the final `argv` construction differs:
- Standard REPL: `["clojure.main", "--repl"]`
- nREPL: `["clojure.main", "-e", nrepl_start_code]`
- Rebel: `["clojure.main", "-m", "rebel-readline.main"]`

**Recommended Refactoring:**
```python
@dataclass(frozen=True)
class ReplSetup:
    """Common setup for all REPL types."""
    addresses_to_load: Addresses
    classpath: Classpath
    source_roots: set[str]
    jdk: JdkEnvironment
    transitive_targets: TransitiveTargets

async def _prepare_repl_setup(
    addresses: Addresses,
    load_resolve_sources: bool,
    jvm: JvmSubsystem,
    clojure_repl_subsystem: ClojureReplSubsystem,
) -> ReplSetup:
    """Common setup logic for all REPL implementations."""
    # Handle address resolution (with load_resolve_sources logic)
    # Gather classpath, transitive targets, source roots
    # Extract JDK version
    # Return ReplSetup with all common data

@rule(desc="Create Clojure REPL", level=LogLevel.DEBUG)
async def create_clojure_repl_request(
    repl: ClojureRepl,
    bash: BashBinary,
    clojure_repl_subsystem: ClojureReplSubsystem,
    jvm: JvmSubsystem,
) -> ReplRequest:
    setup = await _prepare_repl_setup(
        repl.addresses,
        clojure_repl_subsystem.load_resolve_sources,
        jvm,
        clojure_repl_subsystem,
    )

    # Only REPL-specific customization
    argv = [
        *setup.jdk.args(bash, [*sorted(setup.source_roots), *setup.classpath.args()]),
        "clojure.main",
        "--repl",
    ]

    # Prepare for workspace and return ReplRequest
```

**Estimated Impact:** Reduces ~300 lines to ~150 lines, eliminates triplication.

---

### 1.3 Medium Priority: Source Root Determination Duplication

**Locations:**
- `clj_repl.py:165-194` - `_determine_source_root`
- `generate_deps_edn.py:141-180` - `determine_source_root`

**Problem:**
Nearly identical logic for determining source roots from file paths and namespaces. Both:
- Convert namespace to expected path
- Walk backwards matching path components
- Fall back to target directory

**Recommended Refactoring:**
Create a shared utilities module:

```python
# clojure_backend/utils/source_roots.py
def determine_source_root(file_path: str, namespace: str) -> str | None:
    """Determine the source root directory for a Clojure file.

    For a file like projects/foo/src/example/core.clj with namespace example.core,
    the source root is projects/foo/src.
    """
    # Shared implementation
```

Then import and use in both modules.

**Estimated Impact:** Eliminates ~40 lines of duplication, single source of truth.

---

## 2. Missing Abstractions

### 2.1 Namespace Parsing Utilities Module

**Problem:**
Namespace parsing functions are scattered:
- `parse_clojure_namespace` (dependency_inference.py:47-54)
- `parse_clojure_requires` (dependency_inference.py:57-95)
- `parse_clojure_imports` (dependency_inference.py:98-152)
- Used in: dependency_inference.py, package_clojure_deploy_jar.py, check.py, generate_deps_edn.py

**Recommendation:**
Create `clojure_backend/utils/namespace_parser.py`:
```python
"""Utilities for parsing Clojure namespace declarations."""

from __future__ import annotations
import re

# Namespace declaration pattern
NS_PATTERN = re.compile(r'\(ns\s+([\w\.\-]+)', re.MULTILINE)

def parse_namespace(source_content: str) -> str | None:
    """Extract the namespace from a Clojure source file."""

def parse_requires(source_content: str) -> set[str]:
    """Extract required namespaces from :require forms."""

def parse_imports(source_content: str) -> set[str]:
    """Extract Java class imports from :import forms."""

def namespace_to_path(namespace: str) -> str:
    """Convert namespace to file path (foo.bar-baz -> foo/bar_baz.clj)."""

def path_to_namespace(file_path: str) -> str:
    """Convert file path to namespace (foo/bar_baz.clj -> foo.bar-baz)."""
```

**Benefits:**
- Single location for all parsing logic
- Easier to test in isolation
- Can add more sophisticated parsing later
- Clear import dependencies

---

### 2.2 Constants/Configuration Module

**Problem:**
Magic strings and version numbers scattered throughout:
- `DEFAULT_CLOJURE_VERSION = "1.11.1"` in `aot_compile.py:25` and `check.py:31`
- nREPL version in `clj_repl.py:111` (default "1.4.0")
- Rebel version in `clj_repl.py:133` (default "0.1.4")

**Recommendation:**
Create `clojure_backend/config.py`:
```python
"""Configuration and constants for the Clojure backend."""

# Default Clojure version for AOT compilation and checking
DEFAULT_CLOJURE_VERSION = "1.11.1"

# Tool versions (defaults, can be overridden via subsystems)
DEFAULT_NREPL_VERSION = "1.4.0"
DEFAULT_REBEL_VERSION = "0.1.4"
DEFAULT_CLJFMT_VERSION = "0.14.0"
DEFAULT_CLJ_KONDO_VERSION = "2024.09.27"

# File patterns
CLOJURE_SOURCE_EXTENSIONS = (".clj", ".cljc")
CLOJURE_TEST_PATTERNS = ("*_test.clj", "*_test.cljc", "test_*.clj", "test_*.cljc")

# JDK package prefixes (for filtering)
JDK_PACKAGE_PREFIXES = ("java.", "javax.", "sun.", "jdk.")
```

**Benefits:**
- Single source of truth for configuration
- Easy to update versions
- Clear documentation of defaults

---

### 2.3 Custom Exception Types

**Problem:**
Code uses generic `ValueError` and `Exception` throughout:
- `aot_compile.py:186` - generic Exception for AOT failures
- `package_clojure_deploy_jar.py:140` - ValueError for missing namespace
- `clj_test_runner.py:103` - ValueError for missing ns declaration

**Recommendation:**
Create `clojure_backend/exceptions.py`:
```python
"""Custom exceptions for the Clojure backend."""

class ClojureBackendError(Exception):
    """Base exception for all Clojure backend errors."""

class NamespaceNotFoundError(ClojureBackendError):
    """Raised when a required namespace cannot be found."""

class AOTCompilationError(ClojureBackendError):
    """Raised when AOT compilation fails."""

class InvalidNamespaceError(ClojureBackendError):
    """Raised when namespace declaration is invalid or missing."""

class MissingGenClassError(ClojureBackendError):
    """Raised when main namespace is missing (:gen-class)."""
```

**Benefits:**
- More specific error handling
- Better error messages for users
- Easier to catch specific failure modes

---

## 3. Architectural Improvements

### 3.1 Regex-Based Parsing Limitations

**Current State:**
All Clojure code parsing uses regex patterns:
- `NS_PATTERN = re.compile(r'\(ns\s+([\w\.\-]+)', re.MULTILINE)`
- Complex multi-line patterns for :require, :use, :import

**Limitations:**
1. **Doesn't handle edge cases:**
   - Multi-line strings containing "(ns"
   - Comments with namespace-like patterns
   - Reader conditionals (#?(:clj ...))
   - Nested forms

2. **Fragile maintenance:**
   - Complex regex patterns hard to understand
   - Easy to introduce bugs when extending

3. **Limited expressiveness:**
   - Can't easily extract additional metadata
   - Can't validate well-formed s-expressions

**Recommendation - Short Term:**
Document regex limitations and add test cases for edge cases:
```python
def parse_clojure_namespace(source_content: str) -> str | None:
    """Extract the namespace from a Clojure source file.

    Limitations:
    - Uses regex, may not handle all edge cases
    - Assumes namespace declaration is outside strings/comments
    - Does not validate s-expression structure

    Known edge cases:
    - Multi-line strings: "(ns fake.ns)" in docstring - may cause false match
    - Reader conditionals: Complex #?(:clj ...) forms may confuse parser

    For production use, consider tools.reader or proper s-expression parser.
    """
```

**Recommendation - Long Term (Future Enhancement):**
Consider using one of:
1. **clojure.tools.reader** via subprocess (most accurate)
2. **parseclj** library (Python port of Clojure reader)
3. **proper s-expression parser** like `sexpdata`

**Trade-offs:**
- More accurate parsing vs. added complexity
- External dependency vs. self-contained
- Performance (regex is fast)

**Decision:** Keep regex for now but document limitations. Consider upgrade when:
- Users report parsing issues
- Need to extract more complex metadata
- Want to validate code structure

---

### 3.2 Resolve Filtering Logic Brittleness

**Location:** `dependency_inference.py:313-324` and `dependency_inference.py:416-421`

**Current Implementation:**
```python
for addr in owners:
    # Check if the address contains a resolve indicator
    # Generated targets have addresses like "path/file.clj:../../resolve_name"
    if f":../../{my_resolve}" in str(addr) or f":{my_resolve}" in str(addr):
        matching_owners.append(addr)
```

**Problems:**
1. **String-based filtering is fragile:**
   - Relies on specific address format conventions
   - Could break if Pants changes address format
   - Magic string patterns (":../../")

2. **Doesn't query actual target:**
   - Should check the target's JvmResolveField directly
   - Current approach infers from address string

**Recommended Refactoring:**
```python
# Get actual targets to check their resolve fields
owner_targets = await Get(Targets, Addresses(owners))

matching_owners = []
for target in owner_targets:
    if target.has_field(JvmResolveField):
        target_resolve = target[JvmResolveField].normalized_value(jvm)
        if target_resolve == my_resolve:
            matching_owners.append(target.address)
```

**Benefits:**
- More robust - queries actual field
- Clearer intent
- Won't break with address format changes

**Trade-off:**
- Requires additional Get to fetch targets
- Slightly slower, but more correct

---

### 3.3 Directory Organization Inconsistency

**Current State:**
Goals are split between top-level and goals/ directory:
- `goals/check.py` - in goals directory 
- `clj_fmt.py` - top level 
- `clj_lint.py` - top level 
- `clj_test_runner.py` - top level 
- `clj_repl.py` - top level 
- `package_clojure_deploy_jar.py` - top level 
- `generate_deps_edn.py` - top level 

**Recommendation:**
Move all goal implementations to `goals/` directory:
```
pants-plugins/clojure_backend/
  goals/
    __init__.py
    check.py           # Already here
    fmt.py             # Rename from clj_fmt.py
    lint.py            # Rename from clj_lint.py
    test.py            # Rename from clj_test_runner.py
    repl.py            # Rename from clj_repl.py
    package.py         # Rename from package_clojure_deploy_jar.py
    generate_deps.py   # Rename from generate_deps_edn.py
```

**Benefits:**
- Clear organization - all goals in one place
- Easier to discover functionality
- Follows common plugin patterns

**Migration Steps:**
1. Move files to goals/
2. Update imports in register.py
3. Update any cross-references
4. Update tests

---

## 4. Performance Optimizations

### 4.1 Sequential Processing in Check Goal

**Location:** `goals/check.py:110`

**Current Implementation:**
```python
results = []

for field_set in request.field_sets:
    # Get JDK and classpath for this target
    jdk_request = JdkRequest.from_field(field_set.jdk_version)
    # ... process one at a time
    result = await Get(...)
    results.append(result)
```

**Problem:**
Processes each field set sequentially, even though they're independent.

**Recommended Optimization:**
```python
# Batch process all field sets in parallel
check_requests = [
    Get(CheckResult, ClojureCheckFieldSet, field_set)
    for field_set in request.field_sets
]

results = await MultiGet(check_requests)
```

**Alternative - Partition-based:**
```python
# If targets share JDK/resolve, group them
@dataclass(frozen=True)
class CheckPartition:
    field_sets: tuple[ClojureCheckFieldSet, ...]
    jdk_request: JdkRequest

# Create partitions by JDK version
partitions = _group_by_jdk(request.field_sets)

# Process each partition in parallel
partition_results = await MultiGet(
    Get(CheckResults, CheckPartition, partition)
    for partition in partitions
)
```

**Expected Impact:**
- Sequential: O(n) time
- Parallel: O(1) time (assuming sufficient parallelism)
- Particularly beneficial for large projects

---

### 4.2 Namespace Parsing Caching

**Problem:**
Same source files are parsed multiple times:
- Once in dependency inference
- Once in check goal
- Once in AOT compilation
- Once in generate-deps-edn

**Recommendation:**
Pants caching should handle this at the file content level, but we could also:

```python
from pants.util.memo import memoized

@memoized
def parse_clojure_namespace_cached(content_hash: str, content: str) -> str | None:
    """Cached version of namespace parsing."""
    return parse_clojure_namespace(content)

# Use digest as cache key
def parse_namespace_from_digest(digest: Digest, file_contents: DigestContents) -> str | None:
    content = file_contents[0].content.decode('utf-8')
    # Use digest as hash for caching
    return parse_clojure_namespace_cached(str(digest.fingerprint), content)
```

**Note:** This may be premature optimization - Pants' own caching likely handles this.

---

## 5. Code Quality Improvements

### 5.1 Enhanced Error Messages

**Current State:**
Some error messages could be more helpful:

```python
# aot_compile.py:186
raise Exception(
    f"AOT compilation failed for namespaces {request.namespaces}:\n"
    f"stdout:\n{process_result.stdout.decode('utf-8')}\n"
    f"stderr:\n{process_result.stderr.decode('utf-8')}"
)
```

**Recommendation:**
Add troubleshooting hints:
```python
raise AOTCompilationError(
    f"AOT compilation failed for namespaces {request.namespaces}.\n\n"
    f"Common causes:\n"
    f"  - Syntax errors in namespace code\n"
    f"  - Missing dependencies\n"
    f"  - Circular namespace dependencies\n"
    f"  - Missing (:gen-class) for main namespace\n\n"
    f"Stdout:\n{stdout}\n\n"
    f"Stderr:\n{stderr}\n\n"
    f"Troubleshooting:\n"
    f"  1. Check the namespace compiles with: pants check {target}\n"
    f"  2. Verify dependencies: pants dependencies {target}\n"
    f"  3. Try compiling directly: clj -M -e '(compile '{namespace})'\n"
)
```

---

### 5.2 Type Safety Improvements

**Current State:**
Some functions use `Any` or untyped returns:

```python
# clj_test_runner.py:180
async def run_clojure_test(
    test_subsystem: TestSubsystem,
    batch: ClojureTestRequest.Batch[ClojureTestFieldSet, Any],  # Any here
) -> TestResult:
```

**Recommendation:**
Use proper generic types or create specific types:
```python
from typing import TypeVar

T = TypeVar('T', bound=FieldSet)

async def run_clojure_test(
    test_subsystem: TestSubsystem,
    batch: ClojureTestRequest.Batch[ClojureTestFieldSet, None],
) -> TestResult:
```

---

### 5.3 Documentation Improvements

**Good Practices Already Present:**
- Most functions have docstrings 
- Complex algorithms have comments 
- Type hints throughout 

**Areas for Improvement:**

1. **Regex patterns need explanation:**
```python
# Current
IMPORT_MATCH = re.search(r'\(:import\s+(.*?)(?=\(:|$)', ns_body, re.DOTALL)

# Better
# Match :import forms in namespace declaration
# Pattern explanation:
#   \(:import    - Literal "(:import"
#   \s+          - One or more whitespace
#   (.*?)        - Non-greedy capture of import body
#   (?=\(:|$)    - Lookahead for next directive or end
IMPORT_MATCH = re.search(r'\(:import\s+(.*?)(?=\(:|$)', ns_body, re.DOTALL)
```

2. **Add module-level documentation:**
```python
"""AOT compilation for Clojure namespaces.

This module provides ahead-of-time (AOT) compilation of Clojure namespaces
to JVM bytecode (.class files). AOT compilation is required for:

1. Creating executable JARs with a -main entry point
2. Improving startup time for production deployments
3. Generating Java classes from (:gen-class) declarations

The compilation process:
1. Resolves all transitive dependencies
2. Sets up classpath with dependencies and sources
3. Invokes Clojure compiler (compile 'namespace)
4. Captures generated .class files
5. Returns as ClasspathEntry for packaging

See: https://clojure.org/reference/compilation
"""
```

---

## 6. Feature Enhancements

### 6.1 Enhanced Check Goal

**Current State:**
Check goal only loads namespaces to verify compilation.

**Potential Enhancements:**

1. **Reflection Warnings Detection:**
```python
class ClojureCheckSubsystem(Subsystem):
    warn_on_reflection = BoolOption(
        default=False,
        help="Enable reflection warnings (*warn-on-reflection*)."
    )

    fail_on_reflection = BoolOption(
        default=False,
        help="Fail if reflection warnings are detected."
    )

# In check script:
loader_script = f'''
(set! *warn-on-reflection* {str(config.warn_on_reflection).lower()})
(def reflection-warnings (atom []))

;; Capture reflection warnings
(binding [*err* (java.io.StringWriter.)]
  (require '{namespace})
  (let [warnings (str *err*)]
    (when-not (empty? warnings)
      (swap! reflection-warnings conj warnings))))
'''
```

2. **Circular Dependency Detection:**
```python
def detect_circular_dependencies(namespaces: dict[str, set[str]]) -> list[list[str]]:
    """Detect circular namespace dependencies."""
    # Build dependency graph
    # Use Tarjan's algorithm to find strongly connected components
    # Return any cycles found
```

3. **Unused Namespace Detection:**
```python
def find_unused_requires(source_content: str) -> set[str]:
    """Find namespaces that are required but not used."""
    # Parse requires
    # Parse namespace usage in body
    # Return set difference
```

---

### 6.2 REPL Enhancements

**Potential Features:**

1. **Custom Init Scripts:**
```python
class ClojureReplSubsystem(Subsystem):
    init_script = StrOption(
        default=None,
        help="Path to Clojure file to eval on REPL startup."
    )

# In REPL creation:
if repl_subsystem.init_script:
    argv.extend(["-i", repl_subsystem.init_script])
```

2. **Hot Reload Integration:**
```python
class ClojureReplSubsystem(Subsystem):
    enable_hot_reload = BoolOption(
        default=False,
        help="Add tools.namespace for hot reloading."
    )

# Add as dependency:
if subsystem.enable_hot_reload:
    tools_namespace_artifact = ArtifactRequirement(
        coordinate=Coordinate(
            group="org.clojure",
            artifact="tools.namespace",
            version="1.5.0",
        )
    )
```

3. **REPL History Persistence:**
```python
class ClojureReplSubsystem(Subsystem):
    history_file = StrOption(
        default=".clojure_repl_history",
        help="File to persist REPL history."
    )
```

---

### 6.3 Dependency Inference Improvements

**Potential Enhancements:**

1. **ClojureScript Support:**
```python
# Add .cljs to file extensions
class ClojureScriptSourceField(SingleSourceField):
    expected_file_extensions = (".cljs", ".cljc")

# Handle JS imports
def parse_clojurescript_requires(source_content: str) -> set[str]:
    """Parse :require-macros and JavaScript imports."""
```

2. **Gen-Class Dependency Inference:**
```python
def parse_gen_class_dependencies(source_content: str) -> set[str]:
    """Extract Java classes referenced in :gen-class forms.

    Example:
        (:gen-class
          :extends java.lang.Thread
          :implements [java.io.Closeable])
    """
```

3. **Unused Dependency Warning:**
```python
async def check_unused_dependencies(
    field_set: ClojureFieldSet,
    jvm: JvmSubsystem,
) -> list[Address]:
    """Find dependencies that are declared but not used."""
    # Get explicit dependencies
    # Get inferred dependencies
    # Return difference
```

---

### 6.4 AOT Compilation Enhancements

**Potential Improvements:**

1. **Parallel Compilation:**
```python
async def aot_compile_clojure_parallel(
    request: CompileClojureAOTRequest,
) -> CompiledClojureClasses:
    """AOT compile namespaces in parallel where possible."""

    # Build dependency graph
    dependency_graph = await _build_namespace_dependency_graph(
        request.namespaces, request.source_addresses
    )

    # Topological sort for compilation order
    compilation_order = _topological_sort(dependency_graph)

    # Compile independent namespaces in parallel
    for level in compilation_order:
        # All namespaces in same level can compile in parallel
        results = await MultiGet(
            Get(CompiledNamespace, CompileNamespaceRequest, ns)
            for ns in level
        )
```

2. **Incremental AOT:**
```python
class ClojureAOTSubsystem(Subsystem):
    incremental = BoolOption(
        default=True,
        help="Only recompile changed namespaces."
    )

# Track compiled artifact digests
# Only recompile if source changed
```

3. **Compilation Cache:**
```python
# Use Pants' built-in caching more effectively
@rule(desc="AOT compile Clojure namespace", level=LogLevel.DEBUG)
async def compile_single_namespace(
    namespace: str,
    sources: SourceFiles,
    dependencies: ClasspathEntry,
) -> CompiledNamespace:
    """Compile a single namespace with caching."""
    # Pants will cache based on input digests
```

---

### 6.5 Test Runner Enhancements

**Potential Features:**

1. **Test Filtering:**
```python
class ClojureTestSubsystem(Subsystem):
    test_selectors = StrListOption(
        default=[],
        help="Metadata selectors for test filtering (e.g., '^:integration')."
    )

# Use clojure.test/run-tests with selector
test_code = f"""
(require '[clojure.test :as t])
(t/run-tests
  (t/test-vars
    (filter (comp {selector} meta)
            (vals (ns-interns '{namespace})))))
"""
```

2. **Test Output Formats:**
```python
class ClojureTestSubsystem(Subsystem):
    output_format = StrOption(
        default="summary",
        help="Test output format: summary|detailed|junit-xml"
    )
```

3. **Property-Based Testing Support:**
```python
# Detect test.check usage and include in classpath
if uses_test_check(source_content):
    test_check_artifact = ArtifactRequirement(
        coordinate=Coordinate(
            group="org.clojure",
            artifact="test.check",
            version="1.1.1",
        )
    )
```

---

### 6.6 Linting Enhancements

**Potential Improvements:**

1. **Custom Lint Rules:**
```python
class CljKondo(ExternalTool):
    custom_rules_dir = StrOption(
        default=None,
        help="Directory containing custom clj-kondo rules."
    )
```

2. **Lint Configuration Per Target:**
```python
class SkipCljKondoRulesField(StringSequenceField):
    alias = "skip_clj_kondo_rules"
    help = "Specific clj-kondo rules to skip for this target."

# In lint execution:
if field_set.skip_rules.value:
    argv.extend(["--config", f"{{:linters {{{' '.join(f'{rule} {{:level :off}}' for rule in field_set.skip_rules.value)}}}}"])
```

3. **Auto-Fix Support:**
```python
class CljKondo(ExternalTool):
    auto_fix = BoolOption(
        default=False,
        help="Automatically fix issues where possible."
    )
```

---

## 7. Testing Improvements

### 7.1 Test Coverage Analysis

**Recommended Actions:**

1. **Verify test coverage for parsing functions:**
   - `parse_clojure_namespace` - edge cases?
   - `parse_clojure_requires` - multi-line, complex forms?
   - `parse_clojure_imports` - all syntax variants?

2. **Add property-based tests:**
```python
from hypothesis import given, strategies as st

@given(st.text())
def test_namespace_parsing_never_crashes(source_code):
    """Parsing should never crash, even on invalid input."""
    result = parse_clojure_namespace(source_code)
    assert result is None or isinstance(result, str)
```

3. **Add integration tests:**
```python
def test_end_to_end_repl():
    """Test full REPL workflow."""
    # Create test project
    # Start REPL
    # Verify namespaces are loadable
    # Test requiring project namespaces

def test_end_to_end_deploy_jar():
    """Test full deploy jar workflow."""
    # Create test project
    # Build deploy jar
    # Verify JAR is executable
    # Test JAR runs successfully
```

---

### 7.2 Test Organization

**Current State:**
All tests in `pants-plugins/tests/` directory.

**Recommendation:**
Consider organizing tests to mirror source structure:
```
pants-plugins/
  tests/
    unit/
      test_namespace_parser.py
      test_source_roots.py
    integration/
      test_repl.py
      test_package_jar.py
      test_check.py
    fixtures/
      sample-project/
        src/example/core.clj
        test/example/core_test.clj
        BUILD
```

---

## 8. Priority Recommendations

### Immediate (High Impact, Low Effort)

1. **Extract common dependency inference logic** (Section 1.1)
   - Impact: High - eliminates 200 lines of duplication
   - Effort: Medium - requires careful refactoring
   - Risk: Medium - core functionality

2. **Create shared namespace utilities module** (Section 2.1)
   - Impact: Medium - improves code organization
   - Effort: Low - simple extraction
   - Risk: Low - pure functions

3. **Create constants module** (Section 2.2)
   - Impact: Low - easier maintenance
   - Effort: Low - simple extraction
   - Risk: Very Low - no logic changes

### Short Term (1-2 weeks)

4. **Extract common REPL setup logic** (Section 1.2)
   - Impact: High - eliminates 300 lines of duplication
   - Effort: Medium - moderate refactoring
   - Risk: Medium - affects all REPL types

5. **Fix resolve filtering brittleness** (Section 3.2)
   - Impact: Medium - more robust
   - Effort: Low - straightforward fix
   - Risk: Low - improved correctness

6. **Reorganize goals directory** (Section 3.3)
   - Impact: Low - better organization
   - Effort: Low - move files, update imports
   - Risk: Low - no logic changes

### Medium Term (1 month)

7. **Parallelize check goal** (Section 4.1)
   - Impact: Medium - faster checking
   - Effort: Medium - refactor to batch processing
   - Risk: Medium - changes processing model

8. **Add custom exception types** (Section 2.3)
   - Impact: Medium - better error handling
   - Effort: Low - define exceptions, update raises
   - Risk: Low - backward compatible

9. **Enhanced error messages** (Section 5.1)
   - Impact: High - better user experience
   - Effort: Low - add text
   - Risk: Very Low - only messages

### Long Term (Future Enhancements)

10. **Replace regex with proper parser** (Section 3.1)
    - Impact: High - more accurate parsing
    - Effort: High - significant rewrite
    - Risk: High - core functionality change

11. **Add reflection warnings to check** (Section 6.1)
    - Impact: Medium - catches performance issues
    - Effort: Medium - new feature
    - Risk: Low - additive feature

12. **Add ClojureScript support** (Section 6.3)
    - Impact: High - new capability
    - Effort: High - substantial new code
    - Risk: Medium - new target type

---

## 9. Implementation Roadmap

### Phase 1: Foundation (Week 1-2)
- [x] Create `utils/` directory structure
- [x] Extract namespace parsing utilities
- [x] Create constants module
- [x] Create custom exception types
- [x] Update all imports

### Phase 2: Core Refactoring (Week 3-4)
- [ ] Refactor dependency inference duplication
- [ ] Extract common REPL setup logic
- [ ] Fix resolve filtering logic
- [ ] Reorganize goals directory

### Phase 3: Enhancements (Week 5-6)
- [ ] Parallelize check goal
- [ ] Enhance error messages
- [ ] Add test coverage for parsing edge cases
- [ ] Document regex limitations

### Phase 4: Features (Future)
- [ ] Enhanced check goal features
- [ ] REPL enhancements
- [ ] Additional linting features
- [ ] Consider parser replacement

---

## 10. Conclusion

The Clojure Pants plugin is well-structured and functional, but has several opportunities for improvement:

**Strengths:**
- Clear separation of concerns
- Good use of Pants' rule system
- Comprehensive feature set
- Good type annotations

**Primary Issues:**
- Significant code duplication in dependency inference and REPL implementations
- Missing abstractions for common operations
- Some brittle string-based logic

**Recommended Focus:**
1. Eliminate code duplication first (highest impact)
2. Create shared utilities for common operations
3. Improve robustness of resolve filtering
4. Consider long-term architectural improvements

**Expected Outcomes:**
- ~40% reduction in code size through deduplication
- Improved maintainability and testability
- Better error messages and user experience
- Foundation for future enhancements

This analysis provides a clear path forward for improving the codebase while maintaining backward compatibility and existing functionality.
