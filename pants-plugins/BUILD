pants_requirements(name="pants", resolve="pants-plugins")

python_requirements(name="pants-reqs", resolve="pants-plugins", source="requirements.txt")

python_requirements(name="pants-test-reqs", resolve="pants-plugins", source="test-requirements.txt")

python_sources(name="pants-plugin-sources", resolve="pants-plugins", sources=["clojure_backend/**/*.py"], dependencies=[":pants", ":pants-reqs"])

python_tests(name="test-plugin-sources", resolve="pants-plugins", sources=["tests/**/test_*.py"], dependencies=[":pants-reqs", ":pants-test-reqs", ":pants-plugin-sources"])