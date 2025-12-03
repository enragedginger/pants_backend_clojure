# Fix AOT-Compiled Third-Party Library Classes Overriding JAR Contents

**Date:** 2025-12-03
**Status:** Completed
**Issue:** AOT-compiled third-party library classes may override original JAR contents, breaking protocol extensions

## Summary

When building a `clojure_deploy_jar`, AOT compilation transitively compiles all required namespaces, including third-party libraries. This can cause AOT-compiled `.class` files for third-party code to be included in the uberjar, potentially breaking protocol extensions due to class loading issues.

## Bug Report Analysis

### Reported Error
```
java.lang.ExceptionInInitializerError
...
Caused by: java.lang.IllegalArgumentException: No implementation of method: :spec of protocol: #'rpl.schema.core/Schema found for class: rpl.rama.util.schema.Volatile
```

### Root Cause Analysis

**The bug report is partially correct but misidentifies the actual issue.**

#### What the bug report claims:
1. JAR extraction happens first (lines 304-334)
2. AOT classes are added second (lines 336-355)
3. AOT classes override the original JAR classes

#### What actually happens:
1. JAR extraction happens first and adds entries to `added_entries` set
2. AOT class addition checks `arcname not in added_entries` (line 353)
3. **This means JAR contents WIN, not AOT classes**

#### The Real Issue:

The current implementation has **reverse logic**: dependency JARs win over AOT-compiled classes. However, this is actually **correct behavior** for third-party libraries, but **incorrect for first-party code**.

The actual bug is more subtle:

1. **Transitive AOT compilation**: When we AOT compile `my.app.core`, Clojure's `compile` function transitively compiles ALL required namespaces, including third-party libraries like `rpl.rama.*`

2. **Protocol extension timing**: Protocol extensions in Clojure must be loaded in a specific order. The protocol must be defined before extensions are added. AOT compilation can break this because:
   - The AOT-compiled class references the protocol interface directly
   - If the protocol was re-compiled separately (different compile run), the class identities don't match
   - This causes "No implementation of method" errors

3. **Current filtering is incomplete**: The current code only filters out AOT classes for `provided_namespaces` (first-party provided dependencies), not for ALL third-party library namespaces.

## Research Findings

### Clojure AOT Compilation Behavior

From official Clojure documentation and community sources:

1. **Transitive compilation is inherent**: `compile` generates classes for all transitively required namespaces
2. **Protocol extensions are dynamic**: They rely on runtime evaluation order
3. **Best practice**: Third-party classes should come from their original JARs, not from transitive AOT compilation
4. **Timestamp issues**: When AOT classes and source files coexist, class loading becomes unpredictable

### Key Sources:
- [Clojure - Ahead-of-time Compilation](https://clojure.org/reference/compilation)
- [depstar AOT Documentation](https://cljdoc.org/d/com.github.seancorfield/depstar/2.1.303/doc/getting-started/aot-compilation)
- [Leiningen Issue #679](https://github.com/technomancy/leiningen/issues/679) - "protocols or defrecord have broken reload semantics with AOT"
- [gradle-clojure Issue #8](https://github.com/cursive-ide/gradle-clojure/issues/8) - Timestamp-based class loading conflicts

### How Other Clojure Build Tools Handle This

This is a well-known problem in the Clojure ecosystem. All major build tools have mechanisms to handle transitive AOT compilation:

#### tools.build (Official Clojure Build Tool)

tools.build provides the `:filter-nses` parameter on `compile-clj`:

```clojure
(b/compile-clj {:basis basis
                :src-dirs ["src"]
                :class-dir class-dir
                :filter-nses ['my.project]})  ;; Only write classes matching this prefix
```

From the documentation: "`:filter-nses` - collection of symbols representing a namespace prefix to include" - it "filters which classes get written into `class-dir` by their namespace prefix."

**This is exactly the approach we're proposing** - let transitive compilation happen, but filter the output.

#### Leiningen

Leiningen has `:clean-non-project-classes` option:

> "This is a workaround for a problem in the Clojure compiler where there is no way to AOT-compile a class without compiling all the namespaces it requires."

Best practice configuration:
```clojure
:profiles {:uberjar {:aot [my.main.namespace]
                     :target-path "target/uberjar"}}
```

#### depstar (Deprecated, Influential)

depstar was explicit about this problem and provided `:exclude` with regex patterns:

> "AOT compilation is transitive so, in addition to your `project.core` namespace with its `(:gen-class)`, this will also compile everything that `project.core` requires and include those `.class` files. See the `:exclude` option for ways to exclude unwanted compiled `.class` files."

#### Boot

Boot requires manual namespace specification in the `aot` task - no automatic filtering.

#### lein-aot-filter Plugin

Provides fine-grained control:
```clojure
:plugins [[lein-aot-filter "0.1.0"]]
:aot-include [#"my-project\..*"]  ;; Keep these
:aot-exclude [#"some-lib\..*"]    ;; Remove these
```

### Comparison Table

| Tool | Filtering Mechanism | Approach |
|------|---------------------|----------|
| **tools.build** | `:filter-nses` on `compile-clj` | Filters compiled output by namespace prefix |
| **Leiningen** | `:clean-non-project-classes` | Removes transitively compiled non-project classes |
| **Boot** | Manual namespace specification | No automatic filtering |
| **depstar** | `:exclude` with regex patterns | Explicit filtering via patterns |
| **lein-aot-filter** | `:aot-include`/`:aot-exclude` | Plugin with fine-grained regex control |

### Community Consensus

1. **AOT-compile only your main entry-point namespace** - let transitive compilation happen but filter the results
2. **Filter output to include only project classes** - this is the standard approach
3. **This is a fundamental Clojure limitation** - all tools work around it, none prevent it

From [ClojureVerse](https://clojureverse.org/t/deploying-aot-compiled-libraries/2545):
> "This is a bug in Clojure, not a bug in Leiningen. If you have protocols or records that get AOT compiled, even if only transitively, you need to declare them in :aot."

**Our proposed solution aligns with the community best practice and mirrors what tools.build does with `:filter-nses`.**

### Current Implementation (package.py)

```python
# Lines 336-355: Current AOT class filtering
for file_content in digest_contents:
    if file_content.path.startswith('classes/') and file_content.path.endswith('.class'):
        arcname = file_content.path[8:]  # len('classes/') == 8

        # Check if this class file belongs to a provided namespace
        is_provided = False
        for namespace in provided_namespaces:
            namespace_path = namespace.replace('.', '/').replace('-', '_')
            if arcname.startswith(namespace_path):
                is_provided = True
                break

        # Only add if not from a provided dependency
        if not is_provided and arcname not in added_entries:
            jar.writestr(arcname, file_content.content)
            added_entries.add(arcname)
```

**Problem**: This only filters `provided_namespaces` (first-party deps marked as provided), not third-party library classes.

## Proposed Solution

### Approach: Filter AOT Classes to Project Namespaces Only

Only include AOT-compiled classes that belong to project (first-party) namespaces. Third-party library classes should always come from their original JARs.

### Why This Approach?

1. **Consistency**: Third-party libraries ship with their own compiled classes or source; our AOT compilation shouldn't override them
2. **Protocol safety**: Original library JARs have correct class relationships for protocols
3. **Minimal changes**: Builds on existing namespace analysis infrastructure
4. **Explicit control**: Project namespaces are well-defined via Pants targets

### Alternative Approaches Considered

1. **Change order (AOT first, then JARs)**: Rejected - would cause project classes to be overwritten by dependency JARs
2. **Disable transitive AOT**: Not possible - it's inherent to Clojure's `compile`
3. **Use direct linking**: Doesn't solve the protocol extension issue

---

## Implementation Plan

### Phase 1: Implement Project Namespace Filtering for AOT Classes [DONE]

**Goal**: Filter AOT-compiled classes to only include those from project namespaces, using exact namespace matching.

**Files to modify**:
- `pants-plugins/clojure_backend/goals/package.py`

**Changes**:

1. Build a set of all project namespace paths from `namespace_analysis.namespaces`
2. Convert each namespace to its class file path format
3. Use exact matching against namespace paths, handling inner classes and function classes correctly

**Code outline**:
```python
# After line 273, build project namespace paths
# These are all the namespaces from our analyzed source files
project_namespace_paths = set()
for namespace in namespace_analysis.namespaces.values():
    # Convert namespace to path (e.g., "my.app.core" -> "my/app/core")
    namespace_path = namespace.replace('.', '/').replace('-', '_')
    project_namespace_paths.add(namespace_path)

# Helper function to check if a class file belongs to a project namespace
def is_project_class(arcname: str) -> bool:
    """Check if a class file belongs to a project namespace.

    Handles:
    - Direct namespace classes: my/app/core.class
    - Inner classes: my/app/core$fn__123.class
    - Method implementation: my/app/core$_main.class
    - Init classes: my/app/core__init.class
    """
    # Remove .class extension
    class_path = arcname[:-6]  # len('.class') == 6

    # Handle inner classes (split on $) and __init classes
    base_class_path = class_path.split('$')[0]
    if base_class_path.endswith('__init'):
        base_class_path = base_class_path[:-6]  # len('__init') == 6

    # Check for exact match or if it's a subnamespace
    if base_class_path in project_namespace_paths:
        return True

    # Check if this is a class in a subpackage of a project namespace
    # e.g., my/app/core/impl.class is under my/app/core
    for ns_path in project_namespace_paths:
        if base_class_path.startswith(ns_path + '/'):
            return True

    return False
```

Then modify the AOT class addition loop (lines 336-355):

```python
# Add compiled classes (they're in the classes/ directory)
# Only include classes from project namespaces, not transitively compiled third-party
for file_content in digest_contents:
    if file_content.path.startswith('classes/') and file_content.path.endswith('.class'):
        arcname = file_content.path[8:]  # len('classes/') == 8

        # Only include classes from project namespaces
        if not is_project_class(arcname):
            continue

        # Check if this class file belongs to a provided namespace
        is_provided = False
        for namespace in provided_namespaces:
            namespace_path = namespace.replace('.', '/').replace('-', '_')
            if arcname.startswith(namespace_path):
                is_provided = True
                break

        # Only add if not from a provided dependency and not already added
        if not is_provided and arcname not in added_entries:
            jar.writestr(arcname, file_content.content)
            added_entries.add(arcname)
```

**Why exact matching vs top-level prefix**:

The original draft suggested using only the top-level namespace prefix (e.g., `my/` from `my.app.core`). This is too coarse-grained and could cause false positives:
- If project has namespace `api.core`, top-level prefix would be `api/`
- This would incorrectly INCLUDE third-party `api.client.utils` classes

Using exact namespace paths avoids this issue.

**Testing**:
- Unit test that verifies namespace path extraction
- Test inner class handling (`$fn__123`, `$_main`)
- Test `__init` class handling
- Test with hyphenated namespaces

### Phase 2: Add Comprehensive Test Coverage [DONE]

**Goal**: Ensure the fix works correctly and doesn't regress.

**Files to create/modify**:
- `pants-plugins/tests/test_package.py` (or add to existing test file)

**Test cases**:

1. **Basic filtering**:
   - Project namespace `test.app.core` → include `test/app/core.class`
   - Third-party namespace `clojure.core` → exclude `clojure/core.class`

2. **Inner classes and function classes**:
   - Include `test/app/core$fn__123.class` (function class)
   - Include `test/app/core$_main.class` (method impl)
   - Include `test/app/core__init.class` (init class)
   - Include `test/app/core$SomeRecord.class` (record class)

3. **Subpackage handling**:
   - If project has `test.app.core`, include `test/app/core/impl.class` (subpackage)

4. **Hyphenated namespaces**:
   - `test-app.core` → include `test_app/core.class`

5. **Multiple project namespaces**:
   - Project has `api.core` and `web.handlers`
   - Include classes from both, exclude everything else

6. **Edge case - similar prefixes**:
   - Project has `api.core`
   - Third-party has `apiclient.utils`
   - Should NOT include `apiclient/utils.class`

7. **Clojure core exclusion**:
   - `clojure/core.class` should never be included (always third-party)

8. **Namespace collision** (document behavior):
   - If project and third-party both have `com.example.core`
   - The project's AOT-compiled version would be used (project wins)

**Integration test**:
- Create a test fixture with:
  - A project source file (`test_app/core.clj`)
  - Simulated third-party classes in the AOT output
  - Verify the resulting filtering correctly separates them

### Phase 3: Add Debug Logging and Edge Case Handling [DONE]

**Goal**: Make the behavior transparent and handle edge cases gracefully.

**Files to modify**:
- `pants-plugins/clojure_backend/goals/package.py`

**Changes**:

1. **Add debug logging for excluded classes**:
```python
import logging
logger = logging.getLogger(__name__)

# In the filtering loop:
if not is_project_class(arcname):
    logger.debug(f"Excluding transitively AOT-compiled third-party class: {arcname}")
    continue
```

2. **Handle edge case - no project namespaces**:
```python
if not project_namespace_paths:
    logger.warning(
        "No project namespaces detected. All AOT-compiled classes will be excluded. "
        "This may indicate a configuration issue."
    )
```

3. **Handle edge case - provided namespace is also project namespace**:
   - The current logic correctly handles this: first check is_project_class, then check is_provided
   - Document this behavior

**Testing**:
- Verify warning is logged when no project namespaces
- Verify debug logs show excluded classes

### Phase 4: Documentation [DONE]

**Goal**: Document the behavior for users.

**Files to create/modify**:
- Add section to existing docs or create `docs/aot_compilation.md`

**Documentation content**:

1. **How AOT compilation works in clojure_deploy_jar**:
   - AOT compilation is transitive (unavoidable)
   - We filter at packaging time, not compilation time
   - Only classes from project namespaces are included in the JAR
   - Third-party classes always come from their original JARs

2. **Why this matters for protocol extensions**:
   - Protocol extensions rely on runtime evaluation order
   - Mixing AOT and non-AOT protocol code can cause class identity mismatches
   - The error message and how to diagnose it

3. **Troubleshooting guide**:
   - "No implementation of method" errors
   - Class loading issues
   - How to verify which classes are in the JAR

---

## Risk Assessment

### Low Risk
- Change is additive filtering (won't remove anything currently working correctly)
- Existing duplicate detection still applies
- Project classes remain unaffected

### Medium Risk
- Some projects may intentionally override third-party classes (rare but possible)
- **Mitigation**: Could add an `aot_include_third_party=True` field for opt-in in future

### Testing Strategy
1. Run existing test suite
2. Add specific tests for the filtering logic
3. Manual testing with a project that uses protocol extensions (if available)

---

## Success Criteria

1. AOT-compiled classes from third-party libraries are not included in the uberjar
2. AOT-compiled classes from project namespaces are included
3. Inner classes, function classes, and `__init` classes are handled correctly
4. Protocol extension errors (like the Rama example) are resolved
5. No regression in existing functionality
6. Debug logging shows excluded classes for transparency
7. Documentation explains the behavior

---

## Implementation Notes

### Important Considerations

1. **AOT compilation order**: The current code at lines 231-242 shows:
   - `compiled_classes` uses `all_source_addresses` (includes provided deps for compilation)
   - `runtime_classpath` uses `runtime_source_addresses` (excludes provided for JAR)
   - This is correct - we compile with everything, but package selectively

2. **Clojure core is always third-party**: Classes like `clojure/core.class` will never match project namespaces and will be automatically excluded

3. **Provided namespace filtering remains**: The existing logic for filtering provided namespaces is still needed and runs after the project namespace check

---

## Future Considerations

1. **Explicit inclusion list**: Allow users to specify third-party namespaces to AOT compile if needed
2. **Warning on transitive AOT**: Optional flag to warn when third-party classes are being excluded
3. **Direct linking integration**: Explore if `:direct-linking true` helps with protocol issues
4. **Performance optimization**: If project_namespace_paths becomes very large, consider building a trie for faster prefix matching
