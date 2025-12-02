# pants-backend-clojure

Clojure build plugin for the Pants build tool.

This plugin enables REPL driven development of Clojure systems within the Pants build ecosystem.

## Setup

1. Install pyenv with `brew install pyenv`
2. Install Python 3.11 `pyenv install 3.11`
3. Install Pants `brew install pantsbuild/tap/pants`

## System Requirements

The following system utilities must be installed for certain features:

- **`zip` / `unzip`**: Required for linting with clj-kondo (`pants lint`). The clj-kondo binary is distributed as a `.zip` archive.
  - Debian/Ubuntu: `apt-get install zip unzip`
  - RHEL/CentOS/Fedora: `dnf install zip unzip`
  - Alpine: `apk add zip unzip`
  - macOS: Pre-installed
