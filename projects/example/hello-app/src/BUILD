# Source files for the hello app
clojure_sources(
    name="java17",
    resolve="java17",
    sources=["**/*.clj"],
)

# Deploy jar target for creating an executable uberjar
clojure_deploy_jar(
    name="hello-jar",
    main="hello.core",
    dependencies=[":java17"],
    resolve="java17",
    jdk="zulu:17",
)
