# Releasing ModMux

Short checklist for cutting a release and publishing to PyPI.

## Before tagging
- Update `CHANGELOG.md` (move entries from `Unreleased` into the new version).
- Bump `version` in `pyproject.toml`.
- Ensure CI is green (or run local checks if you prefer).
- Optional local build check:

```bash
python -m pip install --upgrade pip build twine
python -m build
python -m twine check dist/*
```

## Tag and publish
- Create a tag matching the version, e.g. `v0.1.1`.
- Push the tag to GitHub.

The GitHub Actions workflow publishes to PyPI when a tag starting with `v` is pushed.

### Optional: one-click release (GitHub Actions)
- Use the `Release` workflow.
- The workflow reads the version from `pyproject.toml`, checks the changelog, creates the tag, and publishes.

## After publishing
- Verify install:

```bash
python -m pip install --upgrade modmux
python -m modmux --help
```

## One-time setup
- Add a GitHub Actions secret named `PYPI_API_TOKEN` with a PyPI API token.
