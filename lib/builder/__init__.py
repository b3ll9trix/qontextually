"""Builder package.

Turns extraction results into graph mutations with Tier-1 entity resolution
(in writer) and Tier-2 predicate resolution (in resolver). The writer is
transactional per chunk; the resolver is idempotent and can run inline
(one predicate at a time as new ones appear) or as a batch pass.
"""

from lib.builder.resolver import (  # noqa: F401
    ResolutionSummary,
    resolve_all,
    resolve_one,
)
from lib.builder.writer import (  # noqa: F401
    WriteSummary,
    write_extraction,
)
