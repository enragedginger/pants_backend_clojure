(ns example.project-b.core
  (:require [example.project-a.core :as project-a]))

(defn use-project-a []
  (str "Project B using: " project-a/thing))