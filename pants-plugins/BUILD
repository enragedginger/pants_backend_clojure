PLUGIN_VERSION = "0.2.3"

python_sources(
    name="pants-plugin-sources",
    sources=["pants_backend_clojure/**/*.py"],
    **parametrize("pants-2.31", resolve="pants-2.31"),
    **parametrize("pants-2.30", resolve="pants-2.30"),
)

python_sources(
    name="test-fixtures",
    sources=["tests/**/clojure_test_fixtures.py"],
    **parametrize("pants-2.31", resolve="pants-2.31"),
    **parametrize("pants-2.30", resolve="pants-2.30"),
)

python_tests(
    name="test-plugin-sources",
    sources=["tests/**/test_*.py"],
    **parametrize("pants-2.31", resolve="pants-2.31"),
    **parametrize("pants-2.30", resolve="pants-2.30"),
)

# The !! (strong exclude) dependencies below prevent pantsbuild.pants and
# pantsbuild.pants.testutil from appearing in the wheel's install_requires.
# Pants supplies these at runtime from its own venv, so they must not be
# declared as dependencies of the published plugin.
python_distribution(
    name="wheel",
    dependencies=[
        ":pants-plugin-sources@parametrize=pants-2.30",
        "!!3rdparty/python:pants-2.30#pantsbuild.pants",
        "!!3rdparty/python:pants-2.30#pantsbuild.pants.testutil",
    ],
    provides=setup_py(
        name="pants-backend-clojure",
        version=PLUGIN_VERSION,
        description="A Clojure backend for the Pants build system",
        author="Stephen Hopper",
        author_email="stephenmhopper@gmail.com",
        url="https://github.com/enragedginger/pants_backend_clojure",
        license="Apache-2.0",
        python_requires=">=3.11",
        classifiers=[
            "Development Status :: 4 - Beta",
            "Intended Audience :: Developers",
            "License :: OSI Approved :: Apache Software License",
            "Programming Language :: Python :: 3",
            "Programming Language :: Python :: 3.11",
            "Topic :: Software Development :: Build Tools",
        ],
        keywords=["pantsbuild", "pants", "clojure", "jvm", "build-system"],
        long_description_content_type="text/markdown",
    ),
    long_description_path="README.md",
    wheel=True,
    sdist=True,
)
