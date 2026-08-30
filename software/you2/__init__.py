"""You2 — the brain.

A memory system built as a brain rather than as a vector store with a cron
job. Four commitments, from docs/brain.md:

  1. Retrieval is bounded spreading activation, so its cost is independent of
     how much is stored. The system structurally cannot read everything.
  2. The token budget is a global workspace. Selection is a competition with
     lateral inhibition, not a top-k truncation.
  3. Salience is prediction error, computed free from the retrieval that has
     already run -- not a model's opinion of importance.
  4. Nightly multiplicative synaptic downscaling is what makes retrieval
     sharpen with age instead of degrading.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .clock import DAY, HOUR, MINUTE, WEEK, YEAR, Clock, RealClock, SimClock
from .config import DEFAULT, BrainConfig

__all__ = [
    "BrainConfig", "DEFAULT",
    "Clock", "RealClock", "SimClock",
    "DAY", "HOUR", "MINUTE", "WEEK", "YEAR",
    "__version__",
]
