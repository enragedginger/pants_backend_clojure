"""Configuration and constants for the Clojure backend.

This module provides centralized configuration and constant values used
throughout the Clojure Pants plugin.
"""

from __future__ import annotations

# Default Clojure version for AOT compilation and checking
DEFAULT_CLOJURE_VERSION = "1.11.1"

# Default tool versions (can be overridden via subsystems)
DEFAULT_NREPL_VERSION = "1.4.0"
DEFAULT_REBEL_VERSION = "0.1.4"
DEFAULT_CLJFMT_VERSION = "0.14.0"
DEFAULT_CLJ_KONDO_VERSION = "2025.10.23"

# File extensions
CLOJURE_SOURCE_EXTENSIONS = (".clj", ".cljc")
CLOJURE_TEST_EXTENSIONS = (".clj", ".cljc")

# Test file patterns
CLOJURE_TEST_PATTERNS = ("*_test.clj", "*_test.cljc", "test_*.clj", "test_*.cljc")

# JDK package prefixes (for filtering Java stdlib imports)
JDK_PACKAGE_PREFIXES = ("java.", "javax.", "sun.", "jdk.")
