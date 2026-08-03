"""Generation of application-owned identifiers."""

import threading
from uuid import UUID

from uuid6 import uuid7 as _uuid7

_uuid7_lock = threading.Lock()


def uuid7() -> UUID:
    """Return an RFC 9562 UUIDv7.

    The selected library keeps process-local monotonic state. Serializing calls
    avoids races when synchronous database work runs in multiple worker threads.
    """
    with _uuid7_lock:
        return _uuid7()
