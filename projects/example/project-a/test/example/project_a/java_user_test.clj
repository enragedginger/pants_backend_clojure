(ns example.project-a.java-user-test
  (:require [clojure.test :refer [deftest is]]
            [example.project-a.java-user :as ju]))

(deftest test-use-java-class
  (is (= 5 (ju/use-java-class))))
