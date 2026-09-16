"""The single blob-hash implementation.

`sha1(b"blob <len>\\0" + data)` — exactly `git hash-object --no-filters`.
Both `pins.py` (whole-file pins) and `regions.py` (region pins) need this
same hash; it lives here so neither has to duplicate it. The duplication
this replaces existed only to avoid a circular import: `pins.py` imports
`regions` at module scope, so `regions.py` could not import `pins` back.
"""
import hashlib


def blob_of_bytes(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()
