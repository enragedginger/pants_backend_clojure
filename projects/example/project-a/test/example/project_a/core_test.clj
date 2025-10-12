(ns example.project-a.core-test
  (:require [clojure.test :refer [deftest is testing]]
            [example.project-a.core :as core]))

(deftest test-thing
  (testing "thing value"
    (is (= "example common value" core/thing))))