"""Build uberjars using Clojure tools.build.

This module provides rules for building AOT-compiled uberjars using tools.build,
the official Clojure build library. tools.build handles the complexity of AOT
compilation correctly, including protocol classes, macro-generated classes,
and transitive compilation.

Key insight: Pants/Coursier already resolves all dependencies. We don't need
tools.deps for dependency resolution. We simply:
1. Lay out compile-time JARs in one directory (includes provided deps)
2. Lay out runtime JARs in another directory (excludes provided deps)
3. Pass these directories to tools.build as pre-resolved classpaths
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pants.core.util_rules.stripped_source_files import StrippedSourceFiles
from pants.engine.addresses import Addresses
from pants.engine.fs import AddPrefix, CreateDigest, Digest, FileContent, MergeDigests
from pants.engine.internals.selectors import Get, MultiGet
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import collect_rules, rule
from pants.jvm.classpath import Classpath
from pants.jvm.jdk_rules import JdkEnvironment, JdkRequest, JvmProcess
from pants.jvm.resolve.coursier_fetch import ToolClasspath
from pants.jvm.target_types import JvmJdkField
from pants.util.logging import LogLevel

from clojure_backend.subsystems.tools_build import ToolsBuildClasspathRequest

logger = logging.getLogger(__name__)


def generate_build_script(
    main_ns: str,
    main_class: str,
    java_cmd: str,
    provided_namespaces: tuple[str, ...] = (),
    provided_jar_prefixes: tuple[str, ...] = (),
    class_dir: str = "classes",
    uber_file: str = "app.jar",
) -> str:
    """Generate a tools.build script that uses Pants-provided classpaths.

    This script:
    1. Uses compile-libs/ directory for AOT compilation (all deps including provided)
    2. Uses uber-libs/ directory for packaging (excludes provided deps)
    3. Only compiles the main namespace - Clojure handles transitive compilation
    4. No tools.deps resolution needed - Pants already resolved everything

    The script is self-contained and only depends on tools.build being on the classpath.

    Args:
        main_ns: The main namespace to compile (e.g., my.app.core)
        main_class: The main class for the manifest (e.g., my.app.core or com.example.MyApp)
        java_cmd: Path to the Java executable (tools.build forks a subprocess for AOT)
        provided_namespaces: Namespaces to exclude from the final JAR (e.g., ("api.interface",))
        provided_jar_prefixes: JAR filename prefixes to exclude (e.g., ("org.clojure_clojure_",))
        class_dir: Directory for compiled classes
        uber_file: Output uberjar filename
    """
    # Convert provided namespaces to regex patterns for exclusion
    # api.interface -> "^api/interface.*" (matches all related class files)
    exclusion_patterns = []
    for ns in provided_namespaces:
        # Convert namespace to path format (dots -> slashes)
        ns_path = ns.replace(".", "/").replace("-", "_")
        # Create regex pattern to match all class files for this namespace
        # This matches: api/interface.class, api/interface$fn.class, api/interface__init.class, etc.
        exclusion_patterns.append(f'"^{ns_path}.*\\\\.class"')

    # Always exclude LICENSE files
    exclusion_patterns.append('#"^LICENSE"')
    exclusion_vec = " ".join(exclusion_patterns) if exclusion_patterns else '#"^LICENSE"'

    # Format JAR prefixes as a Clojure vector of strings
    jar_prefixes_vec = " ".join(f'"{p}"' for p in provided_jar_prefixes) if provided_jar_prefixes else ""

    # Use double braces to escape braces in the f-string
    return f'''
(ns build
  (:require [clojure.tools.build.api :as b]
            [clojure.java.io :as io]
            [clojure.string :as str]))

(def class-dir "{class_dir}")
(def uber-file "{uber_file}")
(def main-ns '{main_ns})
(def main-class '{main_class})
(def java-cmd "{java_cmd}")
(def exclusion-patterns [{exclusion_vec}])
(def provided-jar-prefixes [{jar_prefixes_vec}])

(defn list-jars
  "List all JAR files in a directory, returning relative paths."
  [dir]
  (let [dir-file (io/file dir)]
    (if (.exists dir-file)
      (->> (.listFiles dir-file)
           (filter #(and (.isFile %) (.endsWith (.getName %) ".jar")))
           (map #(str dir "/" (.getName %)))
           vec)
      [])))

(defn jar-matches-prefix?
  "Check if a JAR path matches any of the provided prefixes."
  [jar-path prefixes]
  (let [jar-name (-> (io/file jar-path) .getName)]
    (some #(str/starts-with? jar-name %) prefixes)))

(defn filter-provided-jars
  "Filter out JARs whose filenames start with any of the provided prefixes."
  [jar-paths prefixes]
  (if (empty? prefixes)
    jar-paths
    (filterv #(not (jar-matches-prefix? % prefixes)) jar-paths)))

(defn build-libs-map
  "Build a libs map for tools.build uber function.
  The uber function expects {{:libs {{lib-sym {{:paths [...jars...]}}}}}}."
  [jar-paths]
  (into {{}} (map-indexed
               (fn [idx path]
                 [(symbol (str "dep" idx)) {{:paths [path]}}])
               jar-paths)))

(defn uberjar [_]
  (try
    ;; Classpaths pre-resolved by Pants - no tools.deps needed
    ;; provided-src contains sources needed for compilation but not packaging
    (let [compile-jars (list-jars "compile-libs")
          all-uber-jars (list-jars "uber-libs")
          ;; Filter out provided JARs from uber-libs
          uber-jars (filter-provided-jars all-uber-jars provided-jar-prefixes)
          ;; Include provided-src in compile classpath so transitive deps resolve,
          ;; but only compile src-dirs (not provided-src)
          compile-cp (vec (concat ["src" "provided-src"] [class-dir] compile-jars))
          uber-cp (vec (concat ["src"] [class-dir] uber-jars))
          ;; Construct basis maps with required structure
          ;; compile-clj uses :classpath-roots
          compile-basis {{:classpath-roots compile-cp}}
          ;; uber uses :libs map where each lib has :paths
          uber-basis {{:libs (build-libs-map uber-jars)}}]

      (println "compile-libs:" (count compile-jars) "JARs")
      (println "uber-libs:" (count all-uber-jars) "JARs," (- (count all-uber-jars) (count uber-jars)) "excluded")

      ;; Clean previous output
      (b/delete {{:path class-dir}})
      (.mkdirs (io/file class-dir))

      ;; AOT compile main namespace (Clojure transitively compiles all required namespaces)
      ;; Note: compile-clj forks a subprocess, so we must provide :java-cmd
      ;; Note: provided-src is on compile classpath but NOT in src-dirs,
      ;; so its namespaces won't be compiled (only resolved during require)
      (println "Compiling" (str main-ns "..."))
      (b/compile-clj {{:basis compile-basis
                       :src-dirs ["src"]
                       :class-dir class-dir
                       :ns-compile [main-ns]
                       :java-cmd java-cmd}})

      ;; Build uberjar with runtime classpath (excludes provided deps)
      (println "Building uberjar" (str uber-file "..."))
      (println "Exclusion patterns:" (count exclusion-patterns) "patterns")
      (b/uber {{:basis uber-basis
                :class-dir class-dir
                :uber-file uber-file
                :main main-class
                ;; Exclude provided namespace classes and LICENSE files
                :exclude exclusion-patterns}})

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


@dataclass(frozen=True)
class ToolsBuildUberjarRequest:
    """Request to build an uberjar using tools.build.

    The two separate classpaths allow provided dependencies to be available
    during AOT compilation but excluded from the final JAR.
    """

    main_namespace: str  # Namespace to compile (e.g., my.app.core)
    main_class: str  # Main class for manifest (e.g., my.app.core or com.example.MyApp)
    compile_classpath: Classpath  # All deps including provided (for AOT)
    runtime_classpath: Classpath  # Deps excluding provided (for JAR)
    source_digest: Digest  # Runtime source files to compile (stripped roots)
    provided_source_digest: Digest  # Provided source files (available for compilation, not packaged)
    provided_namespaces: tuple[str, ...]  # Namespaces to exclude from the final JAR
    provided_jar_prefixes: tuple[str, ...]  # JAR filename prefixes to exclude (e.g., "org.clojure_clojure_")
    jdk: JvmJdkField | None = None


@dataclass(frozen=True)
class ToolsBuildUberjarResult:
    """Result of building an uberjar with tools.build."""

    digest: Digest  # Contains the uberjar
    jar_path: str  # Relative path to the JAR in the digest


@rule(desc="Build uberjar with tools.build", level=LogLevel.DEBUG)
async def build_uberjar_with_tools_build(
    request: ToolsBuildUberjarRequest,
) -> ToolsBuildUberjarResult:
    """Build an uberjar using tools.build with Pants-provided classpaths.

    This rule:
    1. Fetches the tools.build classpath (tools.build + Clojure + tools.deps)
    2. Sets up a working directory with:
       - build.clj: Generated build script
       - src/: Source files with stripped roots
       - compile-libs/: All JARs including provided (for AOT)
       - uber-libs/: Runtime JARs excluding provided (for packaging)
    3. Invokes tools.build via clojure.main to compile and package

    Key insight: tools.build forks a new JVM for AOT compilation with only the
    application's classpath. The tools.build execution classpath is completely
    separate from the application's classpath.
    """
    # 1. Get tools.build classpath and JDK in parallel
    jdk_request = JdkRequest.from_field(request.jdk) if request.jdk else JdkRequest.SOURCE_DEFAULT

    tools_classpath, jdk = await MultiGet(
        Get(ToolClasspath, ToolsBuildClasspathRequest()),
        Get(JdkEnvironment, JdkRequest, jdk_request),
    )

    # 2. Generate build script
    # tools.build's compile-clj forks a subprocess, so we need to provide the path
    # to the Java executable. In the Pants sandbox, Coursier sets up the JDK at
    # __java_home via a symlink created by the jdk preparation script.
    java_cmd = f"{JdkEnvironment.java_home}/bin/java"
    build_script = generate_build_script(
        main_ns=request.main_namespace,
        main_class=request.main_class,
        java_cmd=java_cmd,
        provided_namespaces=request.provided_namespaces,
        provided_jar_prefixes=request.provided_jar_prefixes,
        class_dir="classes",
        uber_file="app.jar",
    )

    # 3. Create working directory structure
    # Structure:
    #   build.clj           <- Generated build script
    #   src/                <- Runtime source files (to be compiled and packaged)
    #   provided-src/       <- Provided source files (available for compilation, not packaged)
    #   compile-libs/       <- All JARs including provided (for AOT)
    #   uber-libs/          <- Runtime JARs excluding provided (for packaging)

    build_script_digest = await Get(
        Digest,
        CreateDigest([FileContent("build.clj", build_script.encode())]),
    )

    # Put runtime sources under src/ (these get compiled and packaged)
    src_digest = await Get(Digest, AddPrefix(request.source_digest, "src"))

    # Put provided sources under provided-src/ (on classpath for compilation, not packaged)
    provided_src_digest = await Get(Digest, AddPrefix(request.provided_source_digest, "provided-src"))

    # Put compile-time JARs under compile-libs/
    compile_jars_digest = await Get(Digest, MergeDigests(request.compile_classpath.digests()))
    compile_libs_digest = await Get(Digest, AddPrefix(compile_jars_digest, "compile-libs"))

    # Put runtime JARs under uber-libs/
    runtime_jars_digest = await Get(Digest, MergeDigests(request.runtime_classpath.digests()))
    uber_libs_digest = await Get(Digest, AddPrefix(runtime_jars_digest, "uber-libs"))

    # Merge everything
    input_digest = await Get(
        Digest,
        MergeDigests([
            build_script_digest,
            src_digest,
            provided_src_digest,
            compile_libs_digest,
            uber_libs_digest,
        ]),
    )

    # 4. Run tools.build
    # NOTE: tools_classpath contains tools.build + ALL its transitive deps:
    #   - org.clojure:clojure (provides clojure.main entry point)
    #   - org.clojure:tools.deps
    #   - org.clojure:tools.namespace
    #   - org.slf4j:slf4j-nop
    # These are resolved automatically by Coursier.
    # This is SEPARATE from the application classpath (compile-libs/uber-libs).
    #
    # We mount the tools classpath digest at a prefix and use classpath_entries(prefix)
    # to get the properly prefixed paths.
    toolcp_relpath = "__toolcp"
    extra_immutable_input_digests = {
        toolcp_relpath: tools_classpath.digest,
    }

    process = JvmProcess(
        jdk=jdk,
        classpath_entries=tools_classpath.classpath_entries(toolcp_relpath),
        argv=["clojure.main", "build.clj"],
        input_digest=input_digest,
        extra_immutable_input_digests=extra_immutable_input_digests,
        output_files=("app.jar",),
        description=f"Build uberjar for {request.main_namespace}",
        timeout_seconds=600,
        level=LogLevel.DEBUG,
        extra_env={},
        extra_jvm_options=(),
        extra_nailgun_keys=(),
        output_directories=(),
        cache_scope=None,
        use_nailgun=False,
    )

    process_obj = await Get(Process, JvmProcess, process)
    result = await Get(FallibleProcessResult, Process, process_obj)

    if result.exit_code != 0:
        stdout = result.stdout.decode("utf-8")
        stderr = result.stderr.decode("utf-8")
        raise Exception(
            f"tools.build failed for {request.main_namespace}.\n\n"
            f"Common causes:\n"
            f"  - Syntax errors in namespace code\n"
            f"  - Missing dependencies\n"
            f"  - Missing (:gen-class) in main namespace\n"
            f"  - Circular namespace dependencies\n\n"
            f"Stdout:\n{stdout}\n\n"
            f"Stderr:\n{stderr}\n"
        )

    return ToolsBuildUberjarResult(
        digest=result.output_digest,
        jar_path="app.jar",
    )


def rules():
    return collect_rules()
