"""Encoding: segmentation and binding. Runs after the reply, never before."""

from __future__ import annotations

from .binding import Encoded, encode
from .segment import Episode, Segmenter

__all__ = ["Encoded", "Episode", "Segmenter", "encode"]
