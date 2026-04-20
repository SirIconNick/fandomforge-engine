"""Phase 4 review metric submodules — coherence, arc_shape, engagement.

These compute fine-grained quality dimensions that the top-level
`fandomforge.review` aggregates into the final post-render-review.
"""

from fandomforge.intelligence.review_metrics.coherence import (
    CoherenceReport, score_coherence,
)
from fandomforge.intelligence.review_metrics.arc_shape import (
    ArcShapeReport, score_arc_shape,
)
from fandomforge.intelligence.review_metrics.engagement import (
    EngagementReport, score_engagement,
)

__all__ = [
    "CoherenceReport", "score_coherence",
    "ArcShapeReport", "score_arc_shape",
    "EngagementReport", "score_engagement",
]
