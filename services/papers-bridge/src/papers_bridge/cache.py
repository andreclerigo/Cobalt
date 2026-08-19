from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from uuid import uuid4

from .models import CacheMetadata

ITEM_COMPONENT = re.compile(r"^[A-Za-z0-9]{1,32}$")
VERSION_COMPONENT = re.compile(r"^[a-f0-9]{1,64}$")
FIGURE_COMPONENT = re.compile(r"^figure-[0-9]{3}\.(?:png|jpg)$")


class ConversionCache:
    def __init__(self, root: Path, *, max_bytes: int, ttl_seconds: int) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()

    def prepare(self) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.root, 0o700)
            for path in self.root.glob(".pending-*"):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
            for path in self.root.glob("*/.current.pending"):
                path.unlink(missing_ok=True)
            self.prune()

    def find(self, item_key: str, document_version: str | None = None) -> CacheMetadata | None:
        with self._lock:
            return self._find(item_key, document_version)

    def _find(self, item_key: str, document_version: str | None = None) -> CacheMetadata | None:
        if not ITEM_COMPONENT.fullmatch(item_key) or (
            document_version is not None and not VERSION_COMPONENT.fullmatch(document_version)
        ):
            return None
        pointer = self.root / item_key / "current"
        try:
            selected = pointer.read_text(encoding="ascii").strip()
        except OSError:
            return None
        if not VERSION_COMPONENT.fullmatch(selected):
            return None
        if document_version is not None and selected != document_version:
            return None
        directory = self.root / item_key / selected
        metadata = self._metadata(directory)
        if (
            metadata is None
            or not _safe_metadata(metadata)
            or metadata.item_key != item_key
            or metadata.document_version != selected
        ):
            return None
        now = time.time()
        try:
            os.utime(directory, (now, now), follow_symlinks=False)
        except OSError:
            return None
        return metadata

    def publish(
        self,
        metadata: CacheMetadata,
        html: bytes,
        figures: dict[str, bytes],
    ) -> CacheMetadata:
        with self._lock:
            return self._publish(metadata, html, figures)

    def _publish(
        self, metadata: CacheMetadata, html: bytes, figures: dict[str, bytes]
    ) -> CacheMetadata:
        if (
            not ITEM_COMPONENT.fullmatch(metadata.item_key)
            or not ITEM_COMPONENT.fullmatch(metadata.attachment_key)
            or not VERSION_COMPONENT.fullmatch(metadata.document_version)
            or any(not FIGURE_COMPONENT.fullmatch(name) for name in figures)
            or set(metadata.figures) != set(figures)
        ):
            raise ValueError("cache paths must be safe components")
        item_dir = self.root / metadata.item_key
        item_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        pending = self.root / f".pending-{uuid4().hex}"
        figures_dir = pending / "figures"
        figures_dir.mkdir(parents=True, mode=0o700)
        try:
            _exclusive_write(pending / "document.html", html)
            for name, body in figures.items():
                _exclusive_write(figures_dir / name, body)
            final_metadata = metadata.model_copy(
                update={"bytes": len(html) + sum(len(body) for body in figures.values())}
            )
            _exclusive_write(
                pending / "metadata.json",
                final_metadata.model_dump_json().encode("utf-8"),
            )
            _sync_directory(figures_dir)
            _sync_directory(pending)
            target = item_dir / final_metadata.document_version
            if target.exists():
                shutil.rmtree(pending)
            else:
                os.replace(pending, target)
                _sync_directory(item_dir)
                _sync_directory(self.root)
            pointer_pending = item_dir / ".current.pending"
            pointer_pending.unlink(missing_ok=True)
            _exclusive_write(pointer_pending, final_metadata.document_version.encode("ascii"))
            os.replace(pointer_pending, item_dir / "current")
            _sync_directory(item_dir)
            self.prune()
            if self._find(final_metadata.item_key, final_metadata.document_version) is None:
                raise ValueError("published conversion exceeds cache capacity")
            return final_metadata
        finally:
            if pending.exists():
                shutil.rmtree(pending, ignore_errors=True)

    def document(self, metadata: CacheMetadata) -> bytes | None:
        with self._lock:
            if not _safe_metadata(metadata):
                return None
            return _read_bounded(
                self.root / metadata.item_key / metadata.document_version / "document.html",
                768 * 1024,
            )

    def current_document(self, item_key: str, document_version: str | None = None) -> bytes | None:
        """Resolve and read one current version without a publish/prune race."""
        with self._lock:
            metadata = self._find(item_key, document_version)
            if metadata is None:
                return None
            return _read_bounded(
                self.root / item_key / metadata.document_version / "document.html",
                768 * 1024,
            )

    def figure(self, metadata: CacheMetadata, name: str) -> bytes | None:
        with self._lock:
            if not _safe_metadata(metadata) or not FIGURE_COMPONENT.fullmatch(name):
                return None
            if name not in metadata.figures:
                return None
            return _read_bounded(
                self.root / metadata.item_key / metadata.document_version / "figures" / name,
                4 * 1024 * 1024,
            )

    def current_figure(
        self,
        item_key: str,
        name: str,
        document_version: str | None = None,
    ) -> bytes | None:
        """Resolve and read one current figure without a publish/prune race."""
        with self._lock:
            metadata = self._find(item_key, document_version)
            if (
                metadata is None
                or not FIGURE_COMPONENT.fullmatch(name)
                or name not in metadata.figures
            ):
                return None
            return _read_bounded(
                self.root / item_key / metadata.document_version / "figures" / name,
                4 * 1024 * 1024,
            )

    def prune(self) -> None:
        with self._lock:
            self._prune()

    def _prune(self) -> None:
        if not self.root.exists():
            return
        now = time.time()
        versions: list[tuple[float, int, Path]] = []
        for item_dir in self.root.iterdir():
            if not item_dir.is_dir() or item_dir.name.startswith(".pending-"):
                continue
            for version_dir in item_dir.iterdir():
                if not version_dir.is_dir():
                    continue
                try:
                    modified = version_dir.stat().st_mtime
                except OSError:
                    continue
                size = _tree_bytes(version_dir)
                if now - modified > self.ttl_seconds:
                    shutil.rmtree(version_dir, ignore_errors=True)
                else:
                    versions.append((modified, size, version_dir))
        total = sum(size for _, size, _ in versions)
        for _, size, directory in sorted(versions):
            if total <= self.max_bytes:
                break
            shutil.rmtree(directory, ignore_errors=True)
            total -= size
        self._repair_pointers()

    def _repair_pointers(self) -> None:
        for item_dir in self.root.iterdir():
            if not item_dir.is_dir() or item_dir.name.startswith(".pending-"):
                continue
            pointer = item_dir / "current"
            try:
                selected = pointer.read_text(encoding="ascii").strip()
            except OSError:
                selected = ""
            if not selected or not (item_dir / selected).is_dir():
                pointer.unlink(missing_ok=True)
            if not any(child.is_dir() for child in item_dir.iterdir()):
                shutil.rmtree(item_dir, ignore_errors=True)

    @staticmethod
    def _metadata(directory: Path) -> CacheMetadata | None:
        raw = _read_bounded(directory / "metadata.json", 64 * 1024)
        if raw is None:
            return None
        try:
            return CacheMetadata.model_validate(json.loads(raw))
        except (ValueError, json.JSONDecodeError):
            return None


def _exclusive_write(path: Path, body: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)


def _read_bounded(path: Path, maximum: int) -> bytes | None:
    try:
        if path.stat().st_size > maximum:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _tree_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _safe_metadata(metadata: CacheMetadata) -> bool:
    return bool(
        ITEM_COMPONENT.fullmatch(metadata.item_key)
        and ITEM_COMPONENT.fullmatch(metadata.attachment_key)
        and VERSION_COMPONENT.fullmatch(metadata.document_version)
        and all(FIGURE_COMPONENT.fullmatch(name) for name in metadata.figures)
    )


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
