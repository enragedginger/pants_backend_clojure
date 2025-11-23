# Compile-Only Dependencies in Clojure Deploy JARs

## Overview

Compile-only dependencies (also known as "provided" scope in Maven) are dependencies that are needed during compilation but should be excluded from the final JAR file. This is useful when deploying applications to environments that already provide certain libraries.

## When to Use Compile-Only Dependencies

Use compile-only dependencies when:

1. **Servlet Containers**: Deploying to Tomcat, Jetty, or other servlet containers that provide `javax.servlet` APIs
2. **Platform Libraries**: Deploying to platforms that provide their own versions of common libraries (e.g., AWS Lambda, Google Cloud Functions)
3. **Interface Dependencies**: You're building against an API/interface that will be provided at runtime by a different component
4. **Avoiding Version Conflicts**: The runtime environment provides a specific version of a library that must be used

## Maven Comparison

For users familiar with Maven, Pants' `compile_dependencies` field is equivalent to Maven's "provided" scope:

**Maven**:
```xml
<dependency>
    <groupId>javax.servlet</groupId>
    <artifactId>servlet-api</artifactId>
    <version>2.5</version>
    <scope>provided</scope>
</dependency>
```

**Pants**:
```python
jvm_artifact(
    name="servlet-api",
    group="javax.servlet",
    artifact="servlet-api",
    version="2.5",
)

clojure_deploy_jar(
    name="app",
    main="my.app.core",
    dependencies=[..., "//3rdparty/jvm:servlet-api"],
    compile_dependencies=["//3rdparty/jvm:servlet-api"],
)
```

## How It Works

### Dependency Declaration

Dependencies marked as compile-only must appear in **both** fields:

1. `dependencies` - So they're available during compilation and dependency inference
2. `compile_dependencies` - To mark them for exclusion from the JAR

**Example**:
```python
clojure_deploy_jar(
    name="app",
    main="my.app.core",
    dependencies=[":handler", ":servlet-api"],     # All deps for compilation
    compile_dependencies=[":servlet-api"],          # Subset to exclude from JAR
)
```

### Transitive Exclusion

When you mark a dependency as compile-only, **all of its transitive dependencies are also excluded** from the JAR. This ensures consistency - if library A depends on library B, and you exclude A, then B will also be excluded.

**Example**:
```python
# servlet-api depends on commons-logging
jvm_artifact(
    name="servlet-api",
    group="javax.servlet",
    artifact="servlet-api",
    version="2.5",
    # Has transitive dependency on commons-logging
)

clojure_deploy_jar(
    name="app",
    dependencies=[":servlet-api"],
    compile_dependencies=[":servlet-api"],
)
# Result: Both servlet-api AND commons-logging are excluded from the JAR
```

### AOT Compilation

Compile-only dependencies are **available during AOT compilation**. The Pants backend ensures that:

1. AOT compilation has access to all dependencies (including compile-only ones)
2. The final JAR excludes compile-only dependencies
3. Both source files and compiled `.class` files from compile-only dependencies are excluded

## Complete Example: Web Application

Here's a complete example of a web application that uses compile-only dependencies:

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
    artifact="servlet-api",
    version="2.5",
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
    compile_dependencies=["//3rdparty/jvm:servlet-api"],
)
```

### src/myapp/handler.clj
```clojure
(ns myapp.handler
  (:require [compojure.core :refer :all])
  (:import [javax.servlet.http HttpServlet HttpServletRequest HttpServletResponse]))

(defn handle-request [^HttpServletRequest request ^HttpServletResponse response]
  ;; Uses both servlet-api (compile-only) and compojure (bundled)
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

## First-Party Compile-Only Dependencies

Compile-only dependencies also work with first-party Clojure code. This is useful when building libraries with optional platform-specific implementations:

```python
# Platform interface (compile-only for implementations)
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
    compile_dependencies=[":platform-interface"],
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
    compile_dependencies=[":servlet-api"],
)
```

✅ **Correct**:
```python
clojure_deploy_jar(
    name="app",
    dependencies=[":handler", ":servlet-api"],  # Listed
    compile_dependencies=[":servlet-api"],       # And marked for exclusion
)
```

### 2. Not Understanding Transitive Exclusion

If `lib-a` depends on `lib-b`, marking `lib-a` as compile-only also excludes `lib-b`. This is intentional and matches Maven behavior.

### 3. Using with Runtime-Required Dependencies

Don't mark dependencies as compile-only if they're actually needed at runtime. This will cause `ClassNotFoundException` at runtime.

## Troubleshooting

### ClassNotFoundException at Runtime

If you get `ClassNotFoundException` for a class that should be provided:

1. Verify the runtime environment actually provides that library
2. Check the version compatibility between your code and the provided library
3. Ensure the library is on the runtime classpath

### JAR Still Contains Excluded Dependencies

If dependencies marked as compile-only still appear in the JAR:

1. Verify the syntax in `compile_dependencies` field
2. Run `pants dependencies src/myapp:app` to check the dependency graph
3. Check if the dependency is also pulled in transitively by a non-compile-only dependency

### AOT Compilation Fails

If AOT compilation fails with `ClassNotFoundException`:

1. Ensure the compile-only dependency is in the regular `dependencies` field
2. Check that the dependency is correctly resolved in your lockfile
3. Verify the namespace declarations in your Clojure files

## See Also

- [Clojure Deploy JAR Documentation](./deploy_jar.md) - General deploy JAR documentation
- [Pants JVM Documentation](https://www.pantsbuild.org/docs/jvm-overview) - Pants JVM support
- [Maven Dependency Scopes](https://maven.apache.org/guides/introduction/introduction-to-dependency-mechanism.html#dependency-scope) - Maven's "provided" scope
