"""Source management — download clips from YouTube, extract specific ranges, fetch transcripts."""

from fandomforge.sources.store import SourceCatalog, Source
from fandomforge.sources.download import download_source
from fandomforge.sources.extract import extract_range
from fandomforge.sources.transcript import fetch_transcript

__all__ = [
    "SourceCatalog",
    "Source",
    "download_source",
    "extract_range",
    "fetch_transcript",
]
