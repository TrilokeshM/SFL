import os
import zipfile
import tempfile
import pytest
from fastapi import HTTPException

from app.zip_analyzer import safe_extract


def _make_zip(path, entries: dict):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def test_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "evil.zip")
        _make_zip(zip_path, {"../../etc/passwd": "root:x:0:0"})
        extract_to = os.path.join(tmp, "out")
        os.makedirs(extract_to)
        with pytest.raises(HTTPException):
            safe_extract(zip_path, extract_to)


def test_rejects_absolute_path():
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "evil2.zip")
        _make_zip(zip_path, {"/etc/passwd": "root:x:0:0"})
        extract_to = os.path.join(tmp, "out")
        os.makedirs(extract_to)
        with pytest.raises(HTTPException):
            safe_extract(zip_path, extract_to)


def test_rejects_empty_zip():
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "empty.zip")
        with zipfile.ZipFile(zip_path, "w"):
            pass
        extract_to = os.path.join(tmp, "out")
        os.makedirs(extract_to)
        with pytest.raises(HTTPException):
            safe_extract(zip_path, extract_to)


def test_accepts_valid_zip():
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "good.zip")
        _make_zip(zip_path, {"main.py": "print('hello')", "sub/util.py": "x = 1"})
        extract_to = os.path.join(tmp, "out")
        os.makedirs(extract_to)
        files = safe_extract(zip_path, extract_to)
        assert set(files) == {"main.py", "sub/util.py"}
