(ns hello.core
  (:gen-class))

(defn -main
  "A simple main function for testing clojure_deploy_jar"
  [& args]
  (println "Hello from Clojure AOT compilation!")
  (println "Args:" (pr-str args)))
