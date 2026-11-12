"""Managed immutable source-file snapshots."""

import hashlib
import os
import tempfile
from pathlib import Path
from uuid import UUID

from sentinel.domain.exceptions import StorageError


class FileStore:
    """Filesystem storage rooted outside the source repository by default."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def stage(self, source: Path) -> tuple[Path, str, int]:
        """Copy source bytes to a temporary file while calculating SHA-256."""
        digest = hashlib.sha256()
        size = 0
        try:
            with (
                source.open("rb") as incoming,
                tempfile.NamedTemporaryFile(
                    dir=self.root, prefix=".staged-", delete=False
                ) as outgoing,
            ):
                temp = Path(outgoing.name)
                while chunk := incoming.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    outgoing.write(chunk)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            return temp, digest.hexdigest(), size
        except OSError as exc:
            try:
                temp.unlink(missing_ok=True)
            except UnboundLocalError, OSError:
                pass
            raise StorageError(f"Unable to stage source file: {exc}") from exc

    def promote(
        self, staged: Path, dataset_id: UUID, version: int, filename: str
    ) -> tuple[Path, str]:
        """Atomically install a staged snapshot at a UUID-based relative key."""
        key = (
            Path("datasets")
            / str(dataset_id)
            / "versions"
            / str(version)
            / f"source{Path(filename).suffix.lower()}"
        )
        target = self.root / key
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
        except OSError as exc:
            raise StorageError(f"Unable to install dataset snapshot: {exc}") from exc
        return target, key.as_posix()

    def discard(self, path: Path) -> None:
        """Remove a staged or newly installed file if present."""
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"Unable to remove unreferenced snapshot: {exc}") from exc

    def absolute(self, key: str) -> Path:
        """Resolve a persisted relative key under the configured root."""
        return self.root / key
