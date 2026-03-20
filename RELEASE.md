# Release Guide

This project is ready to be published as `policyrail-ai`.

## One-Time Setup

1. Confirm that the GitHub repository is public and reachable:
   `https://github.com/jeff-tengan/PolicyRail`
2. Create the PyPI project `policyrail-ai` if it does not exist yet.
3. Configure Trusted Publishing in PyPI for this repository:
   owner/repo: `jeff-tengan/PolicyRail`
   workflow: `publish.yml`
   environment: `pypi`
4. Optionally create a TestPyPI project and mirror the same setup there.

## Local Validation

Create a release environment and install the release tooling:

```bash
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
py -3 -m pip install -e ".[release]"
```

Run tests:

```bash
py -3 -m unittest discover -s tests -v
```

Build source and wheel distributions:

```bash
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
py -3 -m build
```

Validate the generated distributions:

```bash
py -3 -m twine check dist/*
```

## GitHub Actions Release Flow

The repository includes:

- `.github/workflows/ci.yml` for tests and build verification
- `.github/workflows/publish.yml` for publishing tagged releases to PyPI

To publish a release:

```bash
git tag v0.5.0
git push origin main --tags
```

The `publish.yml` workflow will:

1. run tests
2. build `sdist` and `wheel`
3. publish to PyPI via Trusted Publishing on tag pushes

## Manual Upload Fallback

If Trusted Publishing is not configured yet, you can still upload manually:

```bash
py -3 -m twine upload dist/*
```

This requires a PyPI API token or username/password flow, depending on your setup.
