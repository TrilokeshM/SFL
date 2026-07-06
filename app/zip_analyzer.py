"""
Safe ZIP extraction + lightweight static inspection of the codebase.
No third-party static analysis tools required — this builds a compact
"evidence pack" (file tree, dependencies, snippets) that gets handed to
the LLM so it can ground its answers in real files.
"""
from __future__ import annotations
import os
import zipfile
from dataclasses import dataclass, field
from typing import List, Dict
from fastapi import HTTPException

from app.config import settings

TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".txt", ".md",
    ".yml", ".yaml", ".toml", ".cfg", ".ini", ".html", ".css",
    ".sql", ".env.example", ".java", ".go", ".rb", ".php",
}

DEPENDENCY_FILES = {
    "requirements.txt", "pyproject.toml", "Pipfile", "package.json",
    "go.mod", "pom.xml", "build.gradle", "Gemfile",
}


@dataclass
class CodeEvidence:
    file_tree: List[str] = field(default_factory=list)
    dependencies: Dict[str, str] = field(default_factory=dict)  # filename -> raw content (trimmed)
    snippets: Dict[str, str] = field(default_factory=dict)      # path -> trimmed content
    files_analyzed: int = 0


def _is_path_safe(base_dir: str, target_path: str) -> bool:
    """Guards against zip-slip / path traversal."""
    abs_base = os.path.abspath(base_dir)
    abs_target = os.path.abspath(os.path.join(base_dir, target_path))
    return abs_target.startswith(abs_base + os.sep) or abs_target == abs_base


def safe_extract(zip_bytes_path: str, extract_to: str) -> List[str]:
    """
    Extracts a zip file safely:
      - rejects path traversal / absolute paths / symlinks
      - rejects archives over the configured size limit (zip bomb guard)
      - rejects empty archives
    Returns the list of extracted relative file paths.
    """
    if not zipfile.is_zipfile(zip_bytes_path):
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid ZIP archive.")

    extracted_paths: List[str] = []
    total_uncompressed = 0
    max_bytes = settings.MAX_ZIP_SIZE_MB * 1024 * 1024

    with zipfile.ZipFile(zip_bytes_path) as zf:
        infos = zf.infolist()
        if len(infos) == 0:
            raise HTTPException(status_code=400, detail="ZIP archive is empty.")

        for info in infos:
            name = info.filename

            if name.startswith("/") or name.startswith("\\") or ".." in name.split("/"):
                raise HTTPException(status_code=400, detail=f"Unsafe path in ZIP rejected: {name}")

            if not _is_path_safe(extract_to, name):
                raise HTTPException(status_code=400, detail=f"Path traversal attempt rejected: {name}")

            # crude symlink guard (unix zip symlink flag)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise HTTPException(status_code=400, detail=f"Symlink entries are not allowed: {name}")

            total_uncompressed += info.file_size
            if total_uncompressed > max_bytes:
                raise HTTPException(
                    status_code=400,
                    detail=f"ZIP exceeds the {settings.MAX_ZIP_SIZE_MB}MB uncompressed size limit.",
                )

        zf.extractall(extract_to)
        extracted_paths = [i.filename for i in infos if not i.is_dir()]

    if len(extracted_paths) == 0:
        raise HTTPException(status_code=400, detail="ZIP contains no files (only directories).")

    return extracted_paths


def build_evidence(extract_dir: str, file_paths: List[str]) -> CodeEvidence:
    evidence = CodeEvidence()
    evidence.file_tree = sorted(file_paths)

    # Dependencies first
    for rel_path in file_paths:
        base_name = os.path.basename(rel_path)
        if base_name in DEPENDENCY_FILES:
            full_path = os.path.join(extract_dir, rel_path)
            try:
                with open(full_path, "r", errors="ignore") as f:
                    content = f.read(settings.MAX_FILE_READ_BYTES)
                evidence.dependencies[rel_path] = content
            except OSError:
                continue

    # Then a bounded sample of source snippets, prioritizing likely entry points
    def priority(path: str) -> int:
        lower = path.lower()
        score = 0
        if any(k in lower for k in ("main.", "app.", "server.", "index.")):
            score -= 5
        if "test" in lower:
            score += 3
        depth = path.count("/")
        score += depth
        return score

    candidates = [
        p for p in sorted(file_paths, key=priority)
        if os.path.splitext(p)[1].lower() in TEXT_EXTENSIONS
        and os.path.basename(p) not in DEPENDENCY_FILES
    ]

    read_count = 0
    for rel_path in candidates:
        if read_count >= settings.MAX_FILES_READ:
            break
        full_path = os.path.join(extract_dir, rel_path)
        try:
            with open(full_path, "r", errors="ignore") as f:
                content = f.read(settings.MAX_FILE_READ_BYTES)
        except OSError:
            continue
        if not content.strip():
            continue
        evidence.snippets[rel_path] = content
        read_count += 1

    evidence.files_analyzed = len(file_paths)
    return evidence
