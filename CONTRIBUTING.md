# Contributing to pants-backend-clojure

Thanks for your interest in contributing!

## Filing Issues

Please open a GitHub issue for bug reports or feature requests. Include:

- Pants version and plugin version
- Steps to reproduce (for bugs)
- Expected vs actual behavior

## Running Tests

```bash
pants test pants-plugins::
```

## Submitting Pull Requests

1. Fork the repository and create a branch
2. Make your changes
3. Ensure all tests pass with `pants test pants-plugins::`
4. Submit a pull request with a clear description of the change

## Development Setup

You'll need:

- Python 3.14 (the regular build, not the free-threaded `3.14t`; see "Python interpreter for
  the resolves" in [docs/releasing.md](docs/releasing.md) if both are installed)
- Pants 2.33.2 (pinned in `pants.toml`; needs the `pants` launcher, scie-pants, 0.13.2 or newer)
- JDK 11+ (17 or 21 recommended)

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
