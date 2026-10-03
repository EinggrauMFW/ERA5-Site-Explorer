"""NetCDF concurrency safety and atomic file publishing."""

import os
from pathlib import Path
import tempfile
import threading

import time

__all__ = [
    "NETCDF_LOCK",
    "atomic_publish",
    "atomic_write_bytes",
    "atomic_write_text",
]

NETCDF_LOCK = threading.RLock()


def _replace_atomic(src, dst):
    """Replace dst with src, retrying transient Windows sharing/permission conflicts."""
    for attempt in range(50):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.005 * (attempt + 1))


def atomic_publish(path, writer):
    """Write atomically to path by executing writer(temp_path) in path's folder, then replacing."""
    target_path = Path(path)
    folder = target_path.parent
    if str(folder):
        folder.mkdir(parents=True, exist_ok=True)
    tf = tempfile.NamedTemporaryFile(dir=folder if str(folder) else None, delete=False)
    temp_path = tf.name
    tf.close()
    try:
        writer(temp_path)
        _replace_atomic(temp_path, target_path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise



def atomic_write_bytes(path, data: bytes):
    """Write bytes atomically to path."""
    target_path = Path(path)
    folder = target_path.parent
    if str(folder):
        folder.mkdir(parents=True, exist_ok=True)
    tf = tempfile.NamedTemporaryFile(dir=folder if str(folder) else None, delete=False)
    temp_path = tf.name
    try:
        try:
            tf.write(data)
            tf.flush()
        finally:
            tf.close()
        _replace_atomic(temp_path, target_path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def atomic_write_text(path, text: str, encoding: str = "utf-8"):
    """Write text atomically to path with the specified encoding."""
    atomic_write_bytes(path, text.encode(encoding))
