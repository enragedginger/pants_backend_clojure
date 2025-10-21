"""Custom exceptions for the Clojure backend.

This module defines specific exception types for various error conditions
in the Clojure Pants plugin, allowing for more precise error handling and
better error messages.
"""

from __future__ import annotations


class ClojureBackendError(Exception):
    """Base exception for all Clojure backend errors."""


class NamespaceNotFoundError(ClojureBackendError):
    """Raised when a required namespace cannot be found."""


class AOTCompilationError(ClojureBackendError):
    """Raised when AOT compilation fails."""


class InvalidNamespaceError(ClojureBackendError):
    """Raised when namespace declaration is invalid or missing."""


class MissingGenClassError(ClojureBackendError):
    """Raised when main namespace is missing (:gen-class) directive."""
