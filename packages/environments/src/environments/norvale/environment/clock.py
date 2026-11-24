"""One shared fake clock: libfaketime reads "+<seconds>" from env/clock/offset on every time call.

Simulated now = real now + offset. The clock keeps ticking in real time between jumps (as in codex T9.1).
Only forward moves are allowed, so recorded timestamps never go backwards.
"""

import threading
from datetime import datetime, timezone
from pathlib import Path


class Clock:
    def __init__(self, env_dir):
        self.file = Path(env_dir) / "clock" / "offset"
        self.local = threading.local()  # parallel sessions: each thread keeps its own sim time; file untouched

    def real_now(self):
        return datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + self.offset(), timezone.utc)

    def offset(self):
        return int(self.file.read_text().strip().lstrip("+").rstrip("s") or 0) if self.file.exists() else 0

    def now(self):
        own = getattr(self.local, "now", None)
        return own.astimezone(timezone.utc) if own is not None else self.real_now()  # always UTC in records

    def _write(self, seconds):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".new")
        tmp.write_text(f"+{int(seconds)}\n")
        tmp.replace(self.file)  # atomic: processes never read a half-written offset

    def start_at(self, when):
        """Initial placement, before any service has recorded anything."""
        self._write(when.timestamp() - datetime.now(timezone.utc).timestamp())

    def advance_to(self, when):
        """Jump forward to `when`; no-op if real time already passed it. Returns seconds jumped."""
        if getattr(self.local, "now", None) is not None:  # inside a parallel session: move only its own time
            gap = (when - self.local.now).total_seconds()
            self.local.now = max(self.local.now, when)
            return max(0, gap)
        gap = when.timestamp() - self.now().timestamp()
        if gap > 0:
            self._write(self.offset() + gap)
        return max(0, gap)
