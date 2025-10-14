(ns example.project-b.core-test
  (:require [clojure.test :refer [deftest is]]
            [example.project-b.core :as core]
            [example.project-a.core :as project-a]))

(deftest test-use-project-a
  (is (= "Project B using: example common value" (core/use-project-a))))

(deftest test-direct-project-a-access
  (is (= "example common value" project-a/thing)))