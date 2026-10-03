"""Watch a folder of build scripts and report which ones changed (Qt-free polling).

Polling file signatures (modification time and size) is simple and reliable: it works on
every platform, on network drives, and with editors that save by writing a temporary file
and renaming it, which native change notifications often miss.
"""

from __future__ import annotations

from pathlib import Path

Signature = tuple[int, int]


class FolderWatcher:
    """Reports build scripts in a folder that were created or modified since the last poll.

    With ``settle=True`` a change is reported only once the file has stopped changing for
    one poll, so a script is never built while an editor is still writing it.
    """

    def __init__(self, folder: str | Path, pattern: str = "*.json", report_existing: bool = False,
                 settle: bool = True) -> None:
        self.folder = Path(folder)
        self.pattern = pattern
        self.settle = settle
        self._seen: dict[Path, Signature] = {} if report_existing else self.scan()
        self._pending: dict[Path, Signature] = {}

    def scan(self) -> dict[Path, Signature]:
        """Signatures of the scripts currently in the folder (empty if it does not exist)."""
        out: dict[Path, Signature] = {}
        if not self.folder.is_dir():
            return out
        for path in sorted(self.folder.glob(self.pattern)):
            if path.name.startswith((".", "~")) or path.name.endswith("~"):
                continue  # editor backups and lock files
            try:
                st = path.stat()
            except OSError:
                continue
            if path.is_file():
                out[path] = (st.st_mtime_ns, st.st_size)
        return out

    def poll(self) -> list[Path]:
        """Scripts created or modified since the last poll, oldest change first."""
        current = self.scan()
        changed = [p for p, sig in current.items() if self._seen.get(p) != sig]
        if self.settle:
            ready = [p for p in changed if self._pending.get(p) == current[p]]
            self._pending = {p: current[p] for p in changed if p not in ready}
            changed = ready
        for p in changed:
            self._seen[p] = current[p]
        for gone in set(self._seen) - set(current):
            del self._seen[gone]
        return sorted(changed, key=lambda p: current[p][0])

    def latest(self) -> Path | None:
        """The most recently modified script in the folder."""
        current = self.scan()
        return max(current, key=lambda p: current[p][0]) if current else None
