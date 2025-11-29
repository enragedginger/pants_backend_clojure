# Provided Dependencies in Clojure Deploy JARs

## Overview

Provided dependencies (equivalent to Maven's "provided" scope) are dependencies that are needed during compilation but should be excluded from the final JAR file. This is useful when deploying applications to environments that already provide certain libraries.

## When to Use Provided Dependencies

Use provided dependencies when:

1. **Servlet Containers**: Deploying to Tomcat, Jetty, or other servlet containers that provide `javax.servlet` APIs
2. **Platform Libraries**: Deploying to platforms that provide their own versions of common libraries (e.g., AWS Lambda, Google Cloud Functions)
3. **Interface Dependencies**: You're building against an API/interface that will be provided at runtime by a different component
4. **Avoiding Version Conflicts**: The runtime environment provides a specific version of a library that must be used

## Maven Comparison

For users familiar with Maven, Pants' `provided` field is equivalent to Maven's "provided" scope:

**Maven**:
```xml
<dependency>
    <groupId>javax.servlet</groupId>
    <artifactId>javax.servlet-api</artifactId>
    <version>4.0.1</version>
    <scope>provided</scope>
</dependency>
```

**Pants**:
```python
jvm_artifact(
    name="servlet-api",
    group="javax.servlet",
    artifact="javax.servlet-api",
    version="4.0.1",
)

clojure_deploy_jar(
    name="app",
    main="my.app.core",
    dependencies=[..., "//3rdparty/jvm:servlet-api"],
    provided=["//3rdparty/jvm:servlet-api"],
)
```

## How It Works

### Dependency Declaration

Dependencies marked as provided must appear in **both** fields:

1. `dependencies` - So they're available during compilation and dependency inference
2. `provided` - To mark them for exclusion from the JAR

**Example**:
```python
clojure_deploy_jar(
    name="app",
    main="my.app.core",
    dependencies=[":handler", ":servlet-api"],  # All deps for compilation
    provided=[":servlet-api"],                   # Subset to exclude from JAR
)
```

### Coordinate-Based Matching

For `jvm_artifact` targets, matching is based on Maven **groupId:artifactId** (version is ignored). This means if you mark `org.example:lib:1.0` as provided, any version of `org.example:lib` will be excluded from the JAR.

This is particularly useful when:
- Different transitive dependencies pull in different versions of the same artifact
- You want to exclude an entire artifact family regardless of version

### Transitive Exclusion

When you mark a dependency as provided, **all of its transitive dependencies are also excluded** from the JAR. This includes:

1. **Pants target graph transitives**: First-party sources and `jvm_artifact` targets that are explicit dependencies of the provided target
2. **Maven lockfile transitives**: Dependencies that are resolved by Coursier and stored in the lockfile, even if they don't have explicit Pants targets

This is particularly important for large libraries like Apache Spark or Rama that bring in dozens or hundreds of transitive Maven dependencies. Without Maven transitive exclusion, only the direct artifact would be excluded, leaving all its dependencies bundled in the JAR.

**Example - Simple transitive**:
```python
# If servlet-api depends on commons-logging
jvm_artifact(
    name="servlet-api",
    group="javax.servlet",
    artifact="javax.servlet-api",
    version="4.0.1",
    # Has transitive dependency on commons-logging
)

clojure_deploy_jar(
    name="app",
    dependencies=[":servlet-api"],
    provided=[":servlet-api"],
)
# Result: Both servlet-api AND commons-logging are excluded from the JAR
```

**Example - Large library with many transitives**:
```python
# Guava has many transitive dependencies (jsr305, failureaccess, error_prone_annotations, etc.)
jvm_artifact(
    name="guava",
    group="com.google.guava",
    artifact="guava",
    version="31.1-jre",
)

clojure_deploy_jar(
    name="app",
    main="my.app",
    dependencies=[":guava"],
    provided=[":guava"],
)
# Result: Guava AND all its Maven transitives (jsr305, failureaccess, etc.) are excluded
# These transitives are looked up automatically from the lockfile
```

**How it works**:

The provided dependencies system uses the Coursier lockfile to determine the full transitive closure of Maven dependencies. When Pants generates the lockfile (`pants generate-lockfiles`), Coursier pre-computes the complete list of transitive dependencies for each artifact. The `provided` feature uses this pre-computed closure to exclude all transitive dependencies without needing to traverse the dependency graph at package time.

### AOT Compilation

Provided dependencies are **available during AOT compilation**. The Pants backend ensures that:

1. AOT compilation has access to all dependencies (including provided ones)
2. The final JAR excludes provided dependencies
3. Both source files and compiled `.class` files from provided dependencies are excluded

## Complete Example: Web Application

Here's a complete example of a web application that uses provided dependencies:

### Directory Structure
```
myapp/
├── BUILD
├── 3rdparty/
│   └── jvm/
│       └── BUILD
└── src/
    └── myapp/
        ├── BUILD
        ├── core.clj
        └── handler.clj
```

### 3rdparty/jvm/BUILD
```python
# Provided by the servlet container at runtime
jvm_artifact(
    name="servlet-api",
    group="javax.servlet",
    artifact="javax.servlet-api",
    version="4.0.1",
)

# Included in the JAR
jvm_artifact(
    name="compojure",
    group="compojure",
    artifact="compojure",
    version="1.7.0",
)
```

### src/myapp/BUILD
```python
clojure_sources(
    name="lib",
    dependencies=[
        "//3rdparty/jvm:servlet-api",  # Needed to compile
        "//3rdparty/jvm:compojure",    # Included in JAR
    ],
)

clojure_deploy_jar(
    name="app",
    main="myapp.core",
    dependencies=[
        ":lib",
        "//3rdparty/jvm:servlet-api",
        "//3rdparty/jvm:compojure",
    ],
    # Only servlet-api is provided by the container
    provided=["//3rdparty/jvm:servlet-api"],
)
```

### src/myapp/handler.clj
```clojure
(ns myapp.handler
  (:require [compojure.core :refer :all])
  (:import [javax.servlet.http HttpServlet HttpServletRequest HttpServletResponse]))

(defn handle-request [^HttpServletRequest request ^HttpServletResponse response]
  ;; Uses both servlet-api (provided) and compojure (bundled)
  ...)
```

### src/myapp/core.clj
```clojure
(ns myapp.core
  (:require [myapp.handler])
  (:gen-class))

(defn -main [& args]
  (println "Starting application..."))
```

### Building and Deploying

```bash
# Package the application
pants package src/myapp:app

# Result: dist/src.myapp/app.jar
# - Contains: myapp classes, compojure, and all their dependencies
# - EXCLUDES: servlet-api (provided by Tomcat/Jetty)
```

## First-Party Provided Dependencies

Provided dependencies also work with first-party Clojure code. This is useful when building libraries with optional platform-specific implementations:

```python
# Platform interface (provided for implementations)
clojure_source(
    name="platform-interface",
    source="interface.clj",
)

# AWS-specific implementation
clojure_source(
    name="aws-impl",
    source="aws_impl.clj",
    dependencies=[":platform-interface"],
)

# Lambda function that uses the interface
clojure_deploy_jar(
    name="lambda",
    main="mylambda.core",
    dependencies=[":aws-impl", ":platform-interface"],
    # Interface is provided by the platform layer at runtime
    provided=[":platform-interface"],
)
```

## Verification

To verify what's included in your JAR:

```bash
# Build the JAR
pants package src/myapp:app

# List contents
unzip -l dist/src.myapp/app.jar | grep -i servlet
# Should return empty if servlet-api was correctly excluded

# Or use jar command
jar tf dist/src.myapp/app.jar | grep -i servlet
# Should return empty
```

## Common Pitfalls

### 1. Forgetting to List in Both Fields

❌ **Wrong**:
```python
clojure_deploy_jar(
    name="app",
    dependencies=[":handler"],  # Missing servlet-api!
    provided=[":servlet-api"],
)
```

✅ **Correct**:
```python
clojure_deploy_jar(
    name="app",
    dependencies=[":handler", ":servlet-api"],  # Listed
    provided=[":servlet-api"],                   # And marked for exclusion
)
```

### 2. Not Understanding Transitive Exclusion

If `lib-a` depends on `lib-b`, marking `lib-a` as provided also excludes `lib-b`. This is intentional and matches Maven behavior.

### 3. Using with Runtime-Required Dependencies

Don't mark dependencies as provided if they're actually needed at runtime. This will cause `ClassNotFoundException` at runtime.

## Troubleshooting

### ClassNotFoundException at Runtime

If you get `ClassNotFoundException` for a class that should be provided:

1. Verify the runtime environment actually provides that library
2. Check the version compatibility between your code and the provided library
3. Ensure the library is on the runtime classpath

### JAR Still Contains Excluded Dependencies

If dependencies marked as provided still appear in the JAR:

1. Verify the syntax in `provided` field
2. Run `pants dependencies src/myapp:app` to check the dependency graph
3. Check if the dependency is also pulled in transitively by a non-provided dependency

### AOT Compilation Fails

If AOT compilation fails with `ClassNotFoundException`:

1. Ensure the provided dependency is in the regular `dependencies` field
2. Check that the dependency is correctly resolved in your lockfile
3. Verify the namespace declarations in your Clojure files

## See Also

- [Clojure Deploy JAR Documentation](./deploy_jar.md) - General deploy JAR documentation
- [Pants JVM Documentation](https://www.pantsbuild.org/docs/jvm-overview) - Pants JVM support
- [Maven Dependency Scopes](https://maven.apache.org/guides/introduction/introduction-to-dependency-mechanism.html#dependency-scope) - Maven's "provided" scope
