# Plan: Fix deps.edn Exclusions Format and Add Repository Support

## Problem Statement

Two issues with the current `generate-deps-edn` goal:

1. **Exclusions format is incorrect**: The current code generates `:exclusions [*]` but this is not valid deps.edn syntax. Exclusions must be fully-qualified symbols like `group/artifact`. The correct wildcard format is `*/*` (group `*` and artifact `*`).

2. **Missing repository configuration**: The generated deps.edn doesn't include `:mvn/repos`, which means the Clojure CLI will only use default Maven Central. If the user has configured custom repos (like Clojars) in their `pants.toml`, these should be passed through to the generated deps.edn.

## Research Findings

### Exclusions Format

From the official Clojure deps.edn documentation:
- `:exclusions` accepts "a vector of lib symbols to exclude as transitive deps"
- Library symbols must be qualified: `group/artifact`
- The wildcard format `*/*` should work to exclude all transitives (symbol with namespace `*` and name `*`)

Current code in `format_deps_edn_deps()`:
```python
dep_value = f'{{:mvn/version "{entry.version}" :exclusions [*]}}'
```

Should be:
```python
dep_value = f'{{:mvn/version "{entry.version}" :exclusions [*/*]}}'
```

### Repository Configuration

The `:mvn/repos` key format in deps.edn:
```clojure
{:mvn/repos
 {"central" {:url "https://repo1.maven.org/maven2/"}
  "clojars" {:url "https://repo.clojars.org/"}}}
```

Coursier repos are configured in `pants.toml`:
```toml
[coursier]
repos = [
  "https://repo.clojars.org/",
  "https://maven-central.storage-download.googleapis.com/maven2",
  "https://repo1.maven.org/maven2",
]
```

Access in Pants rules via `CoursierSubsystem.repos` property (returns a tuple of strings).

## Implementation Plan

### Phase 1: Fix Exclusions Format [DONE]

**File**: `pants-plugins/clojure_backend/goals/generate_deps.py`

1. Update `format_deps_edn_deps()` function:
   - Change `:exclusions [*]` to `:exclusions [*/*]` in the dep_value f-string

2. Update `format_deps_edn()` function - nREPL alias:
   - Change `:exclusions [*]` to `:exclusions [*/*]`

3. Update `format_deps_edn()` function - Rebel readline alias:
   - Change `:exclusions [*]` to `:exclusions [*/*]`

**File**: `pants-plugins/tests/test_generate_deps_edn.py`

4. Update all test assertions that check for `:exclusions [*]` to expect `:exclusions [*/*]`:
   - `test_format_deps_edn_deps()` - multiple assertions
   - `test_format_deps_edn_complete()` - dependency format assertion
   - `test_format_deps_edn_deps_special_characters()` - assertion

**File**: `docs/generate-deps-edn.md`

5. Update documentation examples showing `:exclusions [*]` to use `:exclusions [*/*]`

### Phase 2: Add Repository Support [DONE]

**File**: `pants-plugins/clojure_backend/goals/generate_deps.py`

1. Add import for `CoursierSubsystem`:
   ```python
   from pants.jvm.resolve.coursier_setup import CoursierSubsystem
   ```

2. Create helper function `format_mvn_repos()` with collision detection:
   ```python
   def format_mvn_repos(repos: tuple[str, ...] | list[str]) -> str:
       """Format repository URLs as deps.edn :mvn/repos map.

       Generates unique names for each repository, handling collisions
       by appending numeric suffixes when needed.
       """
       if not repos:
           return "{}"

       seen_names: dict[str, int] = {}
       repo_entries = []

       for url in repos:
           base_name = _repo_name_from_url(url)

           # Handle name collisions by appending index
           if base_name in seen_names:
               seen_names[base_name] += 1
               name = f"{base_name}-{seen_names[base_name]}"
           else:
               seen_names[base_name] = 0
               name = base_name

           repo_entries.append(f'   "{name}" {{:url "{url}"}}')

       return "{\n" + "\n".join(repo_entries) + "}"


   def _repo_name_from_url(url: str) -> str:
       """Generate a reasonable repo name from URL."""
       if "clojars" in url.lower():
           return "clojars"
       if "maven-central" in url.lower() or "repo1.maven.org" in url.lower():
           return "central"
       # Generate name from hostname
       from urllib.parse import urlparse
       parsed = urlparse(url)
       hostname = parsed.netloc or "repo"
       return hostname.replace(".", "-").replace(":", "-")
   ```

3. Update `format_deps_edn()` function signature to accept repos:
   ```python
   def format_deps_edn(
       sources_info: ClojureSourcesInfo,
       deps_entries: list[LockFileEntry],
       resolve_name: str,
       repos: tuple[str, ...] | None = None,
   ) -> str:
   ```

4. Add `:mvn/repos` section to the generated deps.edn output in `format_deps_edn()`:
   - Insert after the `:deps` section, before the `:aliases` section
   - Only include if repos is provided and non-empty
   - Format as ` :mvn/repos {repos_str}` to match existing indentation

5. Update `generate_deps_edn_goal()` function:
   - Add `coursier: CoursierSubsystem` parameter to the function signature
   - Pass `coursier.repos` to `format_deps_edn()`

**File**: `pants-plugins/tests/test_generate_deps_edn.py`

6. Add unit tests for `format_mvn_repos()` function:
   - `test_format_mvn_repos_empty()` - empty list returns "{}"
   - `test_format_mvn_repos_single()` - single repo
   - `test_format_mvn_repos_multiple()` - multiple repos
   - `test_format_mvn_repos_clojars_detection()` - Clojars URL gets "clojars" name
   - `test_format_mvn_repos_central_detection()` - Maven Central URLs get "central" name
   - `test_format_mvn_repos_collision_handling()` - duplicate URLs get unique names

7. Add unit tests for `_repo_name_from_url()` function:
   - `test_repo_name_from_url_clojars()` - various Clojars URLs
   - `test_repo_name_from_url_central()` - various Maven Central URLs
   - `test_repo_name_from_url_custom()` - custom repo URLs use hostname

8. Update `test_format_deps_edn_complete()`:
   - Pass repos parameter
   - Verify `:mvn/repos` is included in output

9. Add test for `format_deps_edn()` without repos:
   - `test_format_deps_edn_no_repos()` - verify `:mvn/repos` is omitted when repos is None or empty

**File**: `docs/generate-deps-edn.md`

10. Add new section "3. Maven Repositories (`:mvn/repos`)" explaining:
    - Repositories from `[coursier]` config are passed through
    - Format of the generated `:mvn/repos` map
    - How repo names are derived from URLs

### Phase 3: Verification

1. Run unit tests: `pants test pants-plugins/tests/test_generate_deps_edn.py`
2. Run all plugin tests: `pants test pants-plugins::`
3. Manual verification: Generate a deps.edn and verify:
   - Exclusions use `*/*` format
   - Repositories from `pants.toml` appear in `:mvn/repos`

## Generated Output Example

After implementation, the generated deps.edn will look like:

```clojure
;; Generated by Pants (pants generate-deps-edn --resolve=java21)
;; DO NOT EDIT - This file is auto-generated
;;
;; To regenerate: pants generate-deps-edn --resolve=java21
;;
;; This deps.edn file includes all Clojure sources and dependencies for the 'java21' resolve.
;; Use with standard Clojure tooling (clj, Cursive, Calva, etc.)

{:paths ["projects/example/src"]

 :deps {org.clojure/clojure {:mvn/version "1.12.0" :exclusions [*/*]}
        com.google.guava/guava {:mvn/version "33.0.0-jre" :exclusions [*/*]}}

 :mvn/repos {"clojars" {:url "https://repo.clojars.org/"}
             "central" {:url "https://maven-central.storage-download.googleapis.com/maven2"}
             "central-1" {:url "https://repo1.maven.org/maven2"}}

 :aliases {:test {:extra-paths ["projects/example/test"]}
           :nrepl {:extra-deps {nrepl/nrepl {:mvn/version "1.4.0" :exclusions [*/*]}}}
           :rebel {:extra-deps {com.bhauman/rebel-readline {:mvn/version "0.1.4" :exclusions [*/*]}}}}}
```

## Edge Cases Handled

1. **Empty repos list**: `:mvn/repos` section is omitted entirely
2. **Duplicate repo names**: Handled by appending numeric suffix (e.g., "central", "central-1")
3. **Malformed URLs**: Uses hostname replacement with dashes, falling back to "repo" if empty
4. **CoursierSubsystem not configured**: Uses default repos from Pants

## Risk Assessment

- **Low risk**: These are additive changes that improve compatibility
- **Exclusions change**: The `*/*` format is the correct qualified symbol format for tools.deps
- **Repos addition**: Optional enhancement that improves IDE integration with custom repos
- **Backwards compatible**: If no repos configured, behavior matches current (uses Clojure defaults)

## Notes

- The `*/*` exclusion is a qualified Clojure symbol where the namespace is `*` and the name is `*`
- Repository names are derived from URLs to provide human-readable identifiers
- CoursierSubsystem.repos returns a tuple[str, ...], not a list
- The `:mvn/repos` section is only added when repos are configured
