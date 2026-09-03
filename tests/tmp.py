"""A temp-file context manager the tests share."""

import contextlib
import os
import tempfile


@contextlib.contextmanager
def temp_path(suffix: str):
    """Yield a path for a file the test will create, and remove it after."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    os.unlink(path)
    try:
        yield path
    finally:
        if os.path.exists(path):
            os.unlink(path)
