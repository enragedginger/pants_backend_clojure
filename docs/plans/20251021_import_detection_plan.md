# Clojure Import Detection: Moving from Regex to Data-Driven Parsing

**Date**: 2025-10-21
**Status**: Draft
**Author**: Claude Code

## Executive Summary

Currently, the Pants Clojure backend detects namespace dependencies using regex patterns in `pants-plugins/clojure_backend/utils/namespace_parser.py`. While this works for common cases, it has inherent limitations with edge cases like multi-line strings, comments, reader conditionals, and complex nested forms.

Since Clojure code is valid data for a Clojure program, we should leverage Clojure's own data-reading capabilities to parse source files without evaluation. This plan proposes replacing the regex-based approach with a proper Clojure parser.

**Recommended Approach**: Use **clj-kondo's analysis output** to extract namespace dependencies.

## Current State & Limitations

### Current Implementation

The current implementation in `namespace_parser.py` provides three main functions:
- `parse_namespace()` - Extracts the namespace name using regex
- `parse_requires()` - Extracts required namespaces from `:require` and `:use` forms
- `parse_imports()` - Extracts Java class imports from `:import` forms

### Known Limitations (Documented in Code)

The current regex-based approach has several documented limitations:

1. **Comment and String Handling**
   - May match `(ns ...)` patterns in comments or strings before the real namespace
   - Does not strip comments before parsing
   - Multi-line docstrings containing "(ns fake.namespace)" will be matched

2. **Complex Forms**
   - Reader conditionals (`#?(:clj ...)`) may confuse the parser
   - Does not validate s-expression structure
   - May miss prefix list notation: `(:require [example [bar] [baz]])`

3. **Filtering Issues**
   - Only finds namespaces that contain at least one dot (filters single-word requires)
   - Assumes standard formatting with square brackets

4. **Metadata**
   - May not handle metadata (e.g., `^:deprecated`) correctly

See `pants-plugins/tests/test_namespace_parser_edge_cases.py` for specific test cases documenting these limitations.

### Why Regex is Fundamentally Inadequate

Regex is a pattern-matching tool designed for regular languages, but Clojure is a context-free language with:
- Nested structures (arbitrary depth)
- Multiple syntax forms (reader macros, metadata, etc.)
- Whitespace significance in some contexts
- Comments that can appear anywhere
- String literals that can contain code-like patterns

Attempting to parse Clojure with regex is like parsing HTML with regex - it works until it doesn't, and the edge cases are numerous and subtle.

## Research Findings

### Available Parsing Libraries

Research identified several mature Clojure parsing libraries that read Clojure code as data without evaluation:

#### 1. **clj-kondo** (Recommended)
- **Status**: Already integrated in the codebase (see `clojure_backend/subsystems/clj_kondo.py`)
- **Parser**: Uses a modified fork of rewrite-clj (MIT license)
- **Key Features**:
  - Provides structured analysis output in JSON or EDN format
  - Specifically designed for static analysis without code execution
  - Includes `--config` flag with `{:output {:analysis true}}` to output namespace data
  - Battle-tested across thousands of Clojure projects
  - Fast and reliable
- **Analysis Output**: Provides structured data including:
  - `:namespace-definitions` - namespace names, metadata (deprecated, doc, author, etc.)
  - `:namespace-usages` - `:from`, `:to`, `:alias` for each require
  - `:java-class-usages` - Java imports with `:import true` flag
- **Example Usage**:
  ```bash
  clj-kondo --lint src/foo.clj --config '{:output {:analysis true :format :json}}'
  ```

#### 2. **clojure.tools.namespace**
- **Status**: Official Clojure library from Clojure core team
- **Key Functions**:
  - `read-ns-decl` - Reads the `(ns ...)` form from a file
  - `deps-from-ns-decl` - Extracts dependencies from the ns form
  - `name-from-ns-decl` - Extracts namespace name
- **Pros**:
  - Official and well-maintained
  - Specifically designed for namespace parsing
  - Handles all standard Clojure namespace forms including prefix lists
- **Cons**:
  - Requires running a Clojure process
  - Would need to add as a dependency and manage via subprocess

#### 3. **edamame**
- **Status**: Modern parser used by clj-kondo internally
- **Author**: Michiel Borkdude (also author of clj-kondo, babashka)
- **Key Features**:
  - Configurable EDN/Clojure parser with location metadata
  - `parse-string` and `parse-string-all` functions
  - `parse-ns-form` helper function for namespace declarations
  - Deterministic parsing (e.g., `#(inc %)` � `(fn* [%1] (inc %1))`)
- **Pros**:
  - Lightweight and focused
  - Designed specifically for parsing without evaluation
  - Used as the foundation for clj-kondo
- **Cons**:
  - Would require writing a custom Clojure script
  - Need to integrate with Python via subprocess

#### 4. **rewrite-clj**
- **Status**: Mature library for reading/writing Clojure code
- **Key Features**:
  - Zipper API for traversing and modifying code
  - Preserves whitespace and comments
  - Provides positional metadata (`:row`, `:col`, `:end-row`, `:end-col`)
  - Used by cljfmt and other refactoring tools
- **Pros**:
  - Full-featured and battle-tested
  - Excellent for code transformation
  - Comprehensive zipper API
- **Cons**:
  - More complex than needed for simple dependency extraction
  - Would require writing custom traversal code
  - Need to integrate with Python via subprocess

### Calling Clojure from Python

All Clojure-based solutions require executing Clojure code from Python. Options include:

1. **Subprocess with clj-kondo binary** (Recommended)
   - Already available in the codebase as an ExternalTool
   - No additional dependencies needed
   - Simple subprocess call with JSON output

2. **Subprocess with Clojure CLI**
   - Requires Clojure installation on the system
   - Can use `deps.edn` to specify dependencies
   - Slower startup time (JVM startup penalty)

3. **Subprocess with Babashka**
   - Fast-starting native Clojure interpreter
   - Compatible with most parsing libraries
   - Would need to add as an ExternalTool dependency
   - Startup time: ~10-50ms vs ~1-2s for JVM

## Recommended Approach: clj-kondo Analysis Output

### Why clj-kondo?

1. **Already Integrated**: The codebase already has clj-kondo as an ExternalTool subsystem
2. **Zero Additional Dependencies**: No new tools or libraries needed
3. **Battle-Tested**: Used by thousands of projects, handles all edge cases
4. **Fast**: Native binary with quick startup and execution
5. **Structured Output**: Provides exactly the data we need in JSON/EDN format
6. **Purpose-Built**: Designed for static analysis without code execution
7. **Maintained**: Actively developed with regular releases

### Analysis Output Format

clj-kondo with `--config '{:output {:analysis true :format :json}}'` provides:

```json
{
  "namespace-definitions": [
    {
      "filename": "/path/to/file.clj",
      "row": 1,
      "col": 1,
      "name": "example.project-a.core",
      "lang": "clj",
      "deprecated": false,
      "doc": "Core namespace for project A"
    }
  ],
  "namespace-usages": [
    {
      "filename": "/path/to/file.clj",
      "row": 3,
      "col": 5,
      "from": "example.project-a.core",
      "to": "example.project-a.utils",
      "alias": "utils"
    },
    {
      "filename": "/path/to/file.clj",
      "row": 4,
      "col": 5,
      "from": "example.project-a.core",
      "to": "clojure.string",
      "alias": "str"
    }
  ],
  "java-class-usages": [
    {
      "filename": "/path/to/file.clj",
      "row": 5,
      "col": 3,
      "class": "java.util.Date",
      "import": true
    }
  ]
}
```

### Integration with Existing Code

The new parser would:
1. Replace `namespace_parser.py` functions with clj-kondo-based equivalents
2. Maintain the same interface for `parse_namespace()`, `parse_requires()`, `parse_imports()`
3. Cache clj-kondo analysis results to avoid redundant parsing
4. Integrate with existing dependency inference in `dependency_inference.py`

## Implementation Plan

### Phase 1: Create clj-kondo Parser Wrapper

**Goal**: Create a new module that uses clj-kondo for parsing while maintaining the existing interface.

**Tasks**:
1. Create `pants-plugins/clojure_backend/utils/clj_kondo_parser.py`
2. Implement subprocess wrapper for clj-kondo with analysis output
3. Parse JSON output and extract namespace information
4. Implement functions:
   - `parse_namespace_with_kondo(source_content: str) -> str | None`
   - `parse_requires_with_kondo(source_content: str) -> set[str]`
   - `parse_imports_with_kondo(source_content: str) -> set[str]`

**Pseudo-code**:
```python
def _run_clj_kondo_analysis(source_content: str) -> dict:
    """Run clj-kondo analysis on source content and return parsed JSON."""
    # Write source to temp file
    # Run: clj-kondo --lint <file> --config '{:output {:analysis true :format :json}}'
    # Parse JSON output
    # Return analysis dict
    pass

def parse_namespace_with_kondo(source_content: str) -> str | None:
    """Extract namespace using clj-kondo analysis."""
    analysis = _run_clj_kondo_analysis(source_content)
    ns_defs = analysis.get("namespace-definitions", [])
    return ns_defs[0]["name"] if ns_defs else None

def parse_requires_with_kondo(source_content: str) -> set[str]:
    """Extract required namespaces using clj-kondo analysis."""
    analysis = _run_clj_kondo_analysis(source_content)
    ns_usages = analysis.get("namespace-usages", [])
    return {usage["to"] for usage in ns_usages}

def parse_imports_with_kondo(source_content: str) -> set[str]:
    """Extract Java imports using clj-kondo analysis."""
    analysis = _run_clj_kondo_analysis(source_content)
    java_usages = analysis.get("java-class-usages", [])
    return {usage["class"] for usage in java_usages if usage.get("import")}
```

**Integration with Pants**:
- Use `Get(CljKondo)` to obtain the clj-kondo subsystem
- Use `Process` to run clj-kondo as a subprocess
- Handle the binary through Pants' ExternalTool mechanism

### Phase 2: Update Tests

**Goal**: Ensure the new parser handles all existing test cases and edge cases.

**Tasks**:
1. Update `test_namespace_parser_edge_cases.py` to test both implementations
2. Add new tests for previously-failing edge cases that now work
3. Ensure all tests pass with the clj-kondo implementation
4. Add regression tests for complex namespace forms:
   - Reader conditionals
   - Prefix list notation
   - Metadata on namespace forms
   - Comments interspersed in ns forms

### Phase 3: Integration and Migration

**Goal**: Switch dependency inference to use the new parser.

**Tasks**:
1. Update `namespace_parser.py` to use clj-kondo internally
   - Option A: Replace implementation of existing functions
   - Option B: Deprecate old functions, add new ones with different names
2. Update `dependency_inference.py` if needed (should be transparent if using Option A)
3. Test full dependency inference pipeline:
   - Run `pants dependencies` on test projects
   - Verify correct dependency detection
   - Check performance (should be comparable or better)

### Phase 4: Performance Optimization

**Goal**: Ensure the new approach is performant at scale.

**Tasks**:
1. Implement caching of clj-kondo analysis results
   - Cache by file content hash
   - Store in Pants' cache directory
2. Consider batch processing for multiple files
   - clj-kondo can lint multiple files in one invocation
   - May be beneficial for bulk dependency analysis
3. Benchmark against regex implementation
   - Measure parse time for small, medium, large files
   - Measure memory usage
   - Profile in real-world Pants workflows

### Phase 5: Cleanup and Documentation

**Goal**: Clean up old implementation and document the new approach.

**Tasks**:
1. Remove or archive the old regex-based implementation
2. Update docstrings to reflect the new parsing approach
3. Update any relevant documentation about dependency inference
4. Add notes about clj-kondo as a parsing tool (not just linter)

## Alternative Approaches

While clj-kondo is recommended, here are documented alternatives for future reference:

### Alternative 1: clojure.tools.namespace

**When to use**: If we need official Clojure library support or want to parse other namespace metadata not provided by clj-kondo.

**Implementation**:
```clojure
#!/usr/bin/env bb
;; analyze_ns.clj
(require '[clojure.tools.namespace.parse :as parse]
         '[clojure.java.io :as io])

(defn analyze-file [file-path]
  (with-open [reader (io/reader file-path)]
    (let [ns-decl (parse/read-ns-decl reader)]
      {:namespace (parse/name-from-ns-decl ns-decl)
       :requires (parse/deps-from-ns-decl ns-decl)})))

(println (json/write-str (analyze-file (first *command-line-args*))))
```

**Pros**: Official library, designed specifically for this use case
**Cons**: Requires Babashka or Clojure CLI as additional dependency

### Alternative 2: Custom Edamame Script

**When to use**: If we need custom parsing logic or want minimal dependencies.

**Implementation**:
```clojure
#!/usr/bin/env bb
;; parse_ns_edamame.clj
(require '[edamame.core :as e])

(defn extract-requires [ns-form]
  (let [[_ ns-name & clauses] ns-form
        require-clauses (filter #(and (seq? %) (= :require (first %))) clauses)]
    (for [clause require-clauses
          form (rest clause)
          :when (vector? form)]
      (first form))))

(defn analyze [source]
  (let [forms (e/parse-string-all source)
        ns-form (first (filter #(and (seq? %) (= 'ns (first %))) forms))
        ns-name (second ns-form)]
    {:namespace ns-name
     :requires (set (extract-requires ns-form))}))

(println (json/write-str (analyze (slurp (first *command-line-args*)))))
```

**Pros**: Lightweight, full control over parsing logic
**Cons**: Requires maintaining custom parsing code, potential for bugs

### Alternative 3: rewrite-clj with Zipper API

**When to use**: If we need to extract complex metadata or perform code transformations.

**Implementation**: More complex, involves zipper traversal. See [this blog post](https://blog.exupero.org/updating-requires-with-rewrite-clj/) for examples.

**Pros**: Most powerful, handles all edge cases, useful for refactoring tools
**Cons**: Most complex to implement, overkill for simple dependency extraction

## Testing Strategy

### Unit Tests

1. **Existing Tests**: All tests in `test_namespace_parser_edge_cases.py` must pass
2. **New Edge Case Tests**:
   - Reader conditionals: `#?(:clj ... :cljs ...)`
   - Prefix lists: `(:require [example [foo] [bar]])`
   - Metadata: `(ns ^:deprecated my.ns)`
   - Comments in ns form: `(ns foo (:require [bar] ; comment`
   - Multi-line strings before ns declaration
   - Multiple ns forms (should use first)

### Integration Tests

1. **Dependency Inference**: Test with real Clojure projects
2. **Performance**: Benchmark against regex implementation
3. **Error Handling**: Test with malformed Clojure files

### Regression Tests

1. Run full test suite: `pants test pants-plugins/::`
2. Test on sample projects with complex namespace declarations
3. Verify no regressions in dependency graph generation

## Performance Considerations

### Expected Performance Characteristics

1. **Startup Time**: clj-kondo binary starts in ~10-50ms (native binary)
2. **Parse Time**: Comparable to or faster than regex for complex files
3. **Memory**: Slightly higher due to JSON parsing, but negligible
4. **Caching**: Can cache analysis results by file content hash

### Optimization Strategies

1. **Batch Processing**: Analyze multiple files in single clj-kondo invocation
2. **Incremental Analysis**: Only re-analyze changed files
3. **Result Caching**: Store parsed results in Pants cache
4. **Lazy Evaluation**: Only parse when dependency information is needed

## Migration Path

### Backward Compatibility

The migration can be done transparently:
1. Keep the same function signatures in `namespace_parser.py`
2. Switch implementation from regex to clj-kondo internally
3. No changes needed in calling code (`dependency_inference.py`)

### Rollout Strategy

1. **Phase 1**: Implement clj-kondo parser alongside regex parser
2. **Phase 2**: Add feature flag to switch between implementations
3. **Phase 3**: Default to clj-kondo parser, keep regex as fallback
4. **Phase 4**: Remove regex implementation after validation period

### Validation

1. Compare output of both implementations across test suite
2. Run on real projects and verify identical dependency graphs
3. Measure performance impact
4. Gather user feedback during beta period

## Future Enhancements

Once the clj-kondo parser is in place, we can leverage it for additional features:

1. **Unused Require Detection**: Use analysis data to detect unused requires
2. **Namespace Metadata**: Extract deprecation warnings, documentation
3. **Symbol Resolution**: Track which symbols are used from each namespace
4. **Refactoring Support**: Use clj-kondo analysis for rename/move operations
5. **IDE Integration**: Provide richer IDE support for Pants projects

## Conclusion

Replacing regex-based parsing with clj-kondo's analysis output provides a robust, maintainable solution for Clojure namespace dependency detection. The implementation is straightforward since clj-kondo is already integrated, and the benefits include:

- **Correctness**: Handles all edge cases properly
- **Maintainability**: Delegates parsing to specialized tool
- **Performance**: Fast native binary with caching support
- **Future-Proof**: Can leverage additional analysis data as needed
- **Zero New Dependencies**: Uses existing tooling

The recommended approach is to proceed with Phase 1 implementation using clj-kondo analysis output, validate with comprehensive testing, and migrate incrementally while maintaining backward compatibility.

## References

- [clj-kondo GitHub](https://github.com/clj-kondo/clj-kondo)
- [clj-kondo Analysis Data Documentation](https://cljdoc.org/d/clj-kondo/clj-kondo/2024.09.27/doc/analysis-data)
- [edamame GitHub](https://github.com/borkdude/edamame)
- [rewrite-clj User Guide](https://cljdoc.org/d/rewrite-clj/rewrite-clj/1.1.45/doc/user-guide)
- [clojure.tools.namespace](https://github.com/clojure/tools.namespace)
- [Babashka](https://babashka.org/)
