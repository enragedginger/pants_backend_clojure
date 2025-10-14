(ns example.project-c.core-test
  (:require [clojure.test :refer [deftest is]]
            [example.project-c.core :as core]
            [example.project-a.core :as project-a]))

(deftest test-transform-project-a
  (is (= "EXAMPLE COMMON VALUE" (core/transform-project-a))))

(deftest test-project-a-value
  (is (= "example common value" project-a/thing)))