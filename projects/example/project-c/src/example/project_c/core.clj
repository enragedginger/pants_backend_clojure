(ns example.project-c.core
  (:require [example.project-a.core :as project-a]))

(defn transform-project-a []
  (clojure.string/upper-case project-a/thing))