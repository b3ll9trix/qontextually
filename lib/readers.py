"""Backwards-compat shim.

This module was renamed to `lib.ingestor` to match the architecture diagram
(Ingestor role). Imports here still work; please update callers when convenient.
"""

from lib.ingestor import *  # noqa: F401,F403
from lib.ingestor import (  # noqa: F401
    CSVRecordReader,
    DEFAULT_FILE_EXTRACTOR,
    JSONRecordReader,
    qontext_reader,
)
