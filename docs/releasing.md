# Releasing lab-compiler

The PyPI distribution is `lab-compiler`; the Python import is `lab`. The version is defined in `src/lab/_version.py`. Hatchling reads that file for both the wheel and source distribution, and the compiler records the same version in its output.

## One-time setup

Create the `pypi` environment in the [repository settings](https://github.com/MyersResearchGroup/LabCompiler/settings/environments). Restrict deployment tags to `v*` and configure any required reviewers there.

The `lab-compiler` project already exists on PyPI. Add a GitHub Trusted Publisher in the [project's publishing settings](https://pypi.org/manage/project/lab-compiler/settings/publishing/) with these values:

| Field | Value |
| --- | --- |
| PyPI project name | `lab-compiler` |
| GitHub owner | `MyersResearchGroup` |
| Repository name | `LabCompiler` |
| Workflow filename | `publish.yml` |
| Environment name | `pypi` |

This uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/) and requires no stored PyPI API token.

## Repository and package names

The GitHub repository is `MyersResearchGroup/LabCompiler`. The PyPI distribution is `lab-compiler`, and Python code uses `import lab`. The publisher's repository field must be `LabCompiler`, matching the GitHub name, while its PyPI project field remains `lab-compiler`.

After a repository transfer or rename, update the publisher in PyPI before publishing. PyPI matches the GitHub owner, repository, workflow filename, and environment; a [repository rename requires updating the publisher configuration](https://docs.pypi.org/trusted-publishers/troubleshooting/#token-minting). Updating `pyproject.toml` does not update that configuration.

## Validate a release

Update `src/lab/_version.py` and add the changes and migration guidance to `CHANGELOG.md` when preparing a new version, then run from the repository root:

```sh
uv lock
uv sync --locked --all-extras --python 3.12
uv run --no-sync ruff check .
uv run --no-sync mypy
uv run --no-sync pytest -q
uv build --no-sources --out-dir dist/0.3.0
uv run --no-sync twine check --strict dist/0.3.0/*
```

Use an empty directory for the version being prepared so old releases are not included. `uv build` creates a source distribution and builds the wheel from it. Confirm both artifacts can be installed in fresh environments; for version `0.3.0`:

```sh
uv venv /tmp/lab-compiler-0.3.0-wheel --python 3.12
uv pip install --python /tmp/lab-compiler-0.3.0-wheel/bin/python dist/0.3.0/lab_compiler-0.3.0-py3-none-any.whl
/tmp/lab-compiler-0.3.0-wheel/bin/python -I scripts/check_install.py

uv venv /tmp/lab-compiler-0.3.0-sdist --python 3.12
uv pip install --python /tmp/lab-compiler-0.3.0-sdist/bin/python dist/0.3.0/lab_compiler-0.3.0.tar.gz
/tmp/lab-compiler-0.3.0-sdist/bin/python -I scripts/check_install.py
```

The install check verifies distribution metadata, version agreement, the license and typing marker, and worksheet rendering from the installed templates and assets without importing Pydantic or robot SDKs. `-I` ensures Python imports the installed package. CI runs the test suite with core dependencies and both optional SDKs on Python 3.11 and 3.12, then builds and checks both artifacts. Robot tests use offline simulation and command recording.

CI also checks out PUDU at the revision pinned in `.github/workflows/ci.yml` and requires OT-2 equivalence before building distributions. To run the same comparison locally, check out that revision separately and set its source path explicitly:

```sh
PUDU_SOURCE=/path/to/pinned/PUDU/src uv run --no-sync pytest -q tests/test_ot2_equivalence.py
```

With `PUDU_SOURCE` set, missing reference source or a missing Opentrons SDK is an error. The ordinary local suite can skip these tests when no reference checkout is available. CI also validates the optional upstream LabOP integration in its separate environment.

## Publish

1. Merge the release changes and confirm CI passes on the intended commit.
2. Create a GitHub release for that commit with a tag matching the package version exactly, such as `v0.3.0`, and use the corresponding section of `CHANGELOG.md` as its release notes.
3. Publish the GitHub release. Draft releases do not upload packages.
4. The `Publish to PyPI` workflow runs CI again for the release, checks the tag against the package version, and uploads the validated artifacts through the `pypi` environment. Approve the environment deployment if required reviewers are configured.
5. Confirm the version appears on [PyPI](https://pypi.org/project/lab-compiler/) and install it in a fresh environment:

   ```sh
   python -m pip install "lab-compiler==0.3.0"
   python -c "import lab; print(lab.__version__)"
   ```

Publishing a GitHub release triggers a real PyPI upload, including releases marked as prereleases. Use a PEP 440 prerelease version such as `0.3.0rc1` and a matching tag when that is intended. PyPI does not allow replacing an uploaded file; corrections require a new version.
