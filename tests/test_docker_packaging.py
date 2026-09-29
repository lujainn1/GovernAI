"""Tests that the deployed image can actually retrieve SDAIA evidence.

The Policy Compliance Agent reads its evidence from the FAISS index built by
`app/rag/vector_store.py`. Retrieval fails open (see
`app/rag/evidence.py::retrieve_evidence`), so an index that is missing at
runtime does not break a submission - it silently produces governance
findings with no SDAIA passages behind them, which is the failure this file
exists to catch.

Two things have to hold for that not to happen in the container:

1. The index is in the build context and copied into the image. `data/` is
   excluded wholesale by `.dockerignore`, so the index survives only because
   of an explicit re-include.
2. The index is found from wherever the process is started. The container
   runs `python main.py` from `/srv/app`, but nothing guarantees a working
   directory, so the configured path must be absolute rather than relative
   to the process's cwd.

These are static checks on the packaging files - they need no Docker daemon
and no index on disk.
"""
from pathlib import Path

import pytest

from app import config

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"

# The index location as a build-context-relative posix path, which is the
# form both packaging files use.
INDEX_PATH = config.VECTOR_STORE_DIR.relative_to(config.BASE_DIR).as_posix()


def _patterns() -> list[str]:
    """The .dockerignore patterns, comments and blank lines dropped."""
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def test_vector_store_dir_is_absolute():
    """A relative path would resolve against the working directory, so the
    index would be found when the app is started from the repo root and
    missed when it is started from anywhere else."""
    assert config.VECTOR_STORE_DIR.is_absolute()


def test_vector_store_dir_sits_under_the_data_dir():
    """It has to stay inside the tree the Dockerfile copies."""
    assert config.VECTOR_STORE_DIR.is_relative_to(config.DATA_DIR)


def test_dockerfile_copies_the_index():
    """Without this COPY the image has no index, and every Policy finding in
    production is made without SDAIA evidence."""
    body = DOCKERFILE.read_text(encoding="utf-8")
    assert f"COPY {INDEX_PATH} ./{INDEX_PATH}" in body


def test_dockerignore_re_includes_the_index():
    """`data/` is excluded, so the index needs an explicit exception - and
    that exception has to come after the exclusion to win."""
    patterns = _patterns()
    exclusion = "data/"
    exception = f"!{INDEX_PATH}/"

    assert exclusion in patterns
    assert exception in patterns
    assert patterns.index(exception) > patterns.index(exclusion)


@pytest.mark.parametrize("pattern", ["data/knowledge/", "data/evaluation/"])
def test_dockerignore_keeps_the_rest_of_data_out(pattern):
    """The re-include is scoped to the index: the 25 MB of source PDFs and
    the evaluation datasets are build-time inputs and stay out of the image.
    Nothing may re-include them."""
    assert f"!{pattern}" not in _patterns()
