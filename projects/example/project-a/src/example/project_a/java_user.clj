(ns example.project-a.java-user
  (:import [com.enragedginger.java_project SomeJava]))

(defn use-java-class []
  (let [java-obj (SomeJava.)]
    (.adder java-obj 2 3)))
