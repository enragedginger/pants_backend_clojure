(ns example.project-c.core
  (:use [example.project-a.core]))

(defn transform-project-a []
  (clojure.string/upper-case thing))