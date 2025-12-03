jvm_artifact(
    name="clojure-java17",
    group="org.clojure",
    artifact="clojure",
    version="1.12.3",
    resolve="java17",
)

jvm_artifact(
    name="clojure-java21",
    group="org.clojure",
    artifact="clojure",
    version="1.12.3",
    resolve="java21",
)

clojure_sources(name="java17",resolve="java17",sources=["**/*.clj"],dependencies=[
        "//projects/example/project-b/src:clojure-java17",
    ])
clojure_sources(name="java21",resolve="java21",sources=["**/*.clj"],dependencies=[
        "//projects/example/project-b/src:clojure-java21",
    ])