"""Style clustering for FandomForge reference video profiles.

Loads all .json style profiles from a profiles directory, extracts 8 numeric
features per video, normalises them with z-score standardisation, and runs
K-means for k in {3, 4, 5, 6}. The k with the best silhouette score is
chosen automatically.

Each cluster gets a human-readable archetype name inferred from centroid
values. The result is saved as .style-clusters.json alongside per-cluster
template JSON files that shot_optimizer can use in place of the global average.

Features extracted per profile:
  - tempo_bpm
  - cuts_per_second
  - shot_duration_median
  - vo_coverage_pct
  - downbeat_alignment_pct
  - opening_black_sec
  - color_saturation_avg
  - duration_sec
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

FEATURE_KEYS = (
    "tempo_bpm",
    "cuts_per_second",
    "shot_duration_median",
    "vo_coverage_pct",
    "downbeat_alignment_pct",
    "opening_black_sec",
    "color_saturation_avg",
    "duration_sec",
)


def _extract_features(profile: dict[str, Any]) -> list[float]:
    """Extract the 8 numeric features from a style profile dict.

    Missing values are filled with sensible defaults (the median of typical
    multifandom videos) rather than zero, to avoid pulling cluster centroids.

    Args:
        profile: Parsed JSON dict from a .ref-profiles/*.json file.

    Returns:
        List of 8 floats in the order defined by FEATURE_KEYS.
    """
    # Defaults derived from manual inspection of the ref pool
    defaults: dict[str, float] = {
        "tempo_bpm": 120.0,
        "cuts_per_second": 0.5,
        "shot_duration_median": 2.0,
        "vo_coverage_pct": 20.0,
        "downbeat_alignment_pct": 10.0,
        "opening_black_sec": 1.0,
        "color_saturation_avg": 0.5,
        "duration_sec": 240.0,
    }

    # shot_duration_median lives inside shot_duration_stats in most profiles
    shot_stats = profile.get("shot_duration_stats", {})
    shot_dur_median = shot_stats.get("median", profile.get("shot_duration_median", defaults["shot_duration_median"]))

    # downbeat_alignment_pct: derive from cuts_aligned dict if not top-level
    cuts_aligned = profile.get("cuts_aligned", {})
    total_cuts = profile.get("num_cuts", 1) or 1
    downbeat_pct = profile.get("downbeat_alignment_pct")
    if downbeat_pct is None:
        downbeat_count = cuts_aligned.get("downbeat", 0)
        downbeat_pct = (downbeat_count / total_cuts) * 100.0

    values: list[float] = [
        float(profile.get("tempo_bpm", defaults["tempo_bpm"])),
        float(profile.get("cuts_per_second", defaults["cuts_per_second"])),
        float(shot_dur_median),
        float(profile.get("vo_coverage_pct", defaults["vo_coverage_pct"])),
        float(downbeat_pct),
        float(profile.get("opening_black_sec", defaults["opening_black_sec"])),
        float(profile.get("color_saturation_avg", defaults["color_saturation_avg"])),
        float(profile.get("duration_sec", defaults["duration_sec"])),
    ]

    # Guard against NaN / Inf from bad data
    for i, v in enumerate(values):
        if not math.isfinite(v):
            values[i] = defaults[FEATURE_KEYS[i]]

    return values


# ---------------------------------------------------------------------------
# Z-score normalisation (pure Python, no numpy required)
# ---------------------------------------------------------------------------

def _zscore_normalize(matrix: list[list[float]]) -> tuple[list[list[float]], list[float], list[float]]:
    """Normalise each column to zero mean and unit variance.

    Args:
        matrix: List of feature vectors, one per sample.

    Returns:
        Tuple of (normalised_matrix, means, stds). stds are floored at 1e-8
        to avoid division by zero for constant features.
    """
    n = len(matrix)
    if n == 0:
        return matrix, [], []

    n_features = len(matrix[0])
    means: list[float] = [0.0] * n_features
    stds: list[float] = [0.0] * n_features

    for j in range(n_features):
        col = [matrix[i][j] for i in range(n)]
        mean = sum(col) / n
        variance = sum((x - mean) ** 2 for x in col) / n
        means[j] = mean
        stds[j] = max(math.sqrt(variance), 1e-8)

    normalised = [
        [(matrix[i][j] - means[j]) / stds[j] for j in range(n_features)]
        for i in range(n)
    ]
    return normalised, means, stds


# ---------------------------------------------------------------------------
# K-means (pure Python)
# ---------------------------------------------------------------------------

def _euclidean(a: list[float], b: list[float]) -> float:
    """Return Euclidean distance between two equal-length vectors."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _kmeans(
    vectors: list[list[float]],
    k: int,
    max_iter: int = 200,
    n_restarts: int = 5,
    seed: int = 42,
) -> tuple[list[int], list[list[float]], float]:
    """Run K-means with multiple random restarts.

    Uses K-means++ initialisation for each restart to improve convergence.

    Args:
        vectors: Normalised feature vectors.
        k: Number of clusters.
        max_iter: Maximum iterations per run.
        n_restarts: Number of independent runs; best inertia wins.
        seed: Random seed for reproducibility.

    Returns:
        Tuple of (labels, centroids, inertia).
        - labels: Cluster index for each sample.
        - centroids: List of k centroid vectors.
        - inertia: Sum of squared distances to nearest centroid.
    """
    import random
    rng = random.Random(seed)

    n = len(vectors)
    n_features = len(vectors[0]) if vectors else 0

    best_labels: list[int] = [0] * n
    best_centroids: list[list[float]] = [[0.0] * n_features] * k
    best_inertia = float("inf")

    for restart in range(n_restarts):
        # K-means++ initialisation
        centroids: list[list[float]] = []
        first_idx = rng.randint(0, n - 1)
        centroids.append(list(vectors[first_idx]))

        for _ in range(k - 1):
            dists = [
                min(_euclidean(v, c) ** 2 for c in centroids)
                for v in vectors
            ]
            total = sum(dists)
            if total < 1e-12:
                # All points are identical, just pick randomly
                centroids.append(list(vectors[rng.randint(0, n - 1)]))
                continue
            r = rng.random() * total
            cumulative = 0.0
            chosen = n - 1
            for idx, d in enumerate(dists):
                cumulative += d
                if cumulative >= r:
                    chosen = idx
                    break
            centroids.append(list(vectors[chosen]))

        labels = [0] * n

        for _ in range(max_iter):
            # Assignment step
            new_labels = [
                min(range(k), key=lambda j: _euclidean(v, centroids[j]))
                for v in vectors
            ]

            if new_labels == labels:
                break
            labels = new_labels

            # Update step
            new_centroids: list[list[float]] = []
            for j in range(k):
                members = [vectors[i] for i in range(n) if labels[i] == j]
                if not members:
                    # Dead cluster: reinitialise to a random point
                    new_centroids.append(list(vectors[rng.randint(0, n - 1)]))
                else:
                    new_centroids.append([
                        sum(m[f] for m in members) / len(members)
                        for f in range(n_features)
                    ])
            centroids = new_centroids

        inertia = sum(
            _euclidean(vectors[i], centroids[labels[i]]) ** 2
            for i in range(n)
        )

        if inertia < best_inertia:
            best_inertia = inertia
            best_labels = list(labels)
            best_centroids = [list(c) for c in centroids]

    return best_labels, best_centroids, best_inertia


# ---------------------------------------------------------------------------
# Silhouette score
# ---------------------------------------------------------------------------

def _silhouette_score(vectors: list[list[float]], labels: list[int], k: int) -> float:
    """Compute mean silhouette coefficient.

    For each sample i:
      a(i) = mean distance to other samples in the same cluster.
      b(i) = min over other clusters c: mean distance to samples in c.
      s(i) = (b(i) - a(i)) / max(a(i), b(i))

    Global score = mean(s(i)).

    Returns -1.0 if k == 1 or any cluster has only one member.

    Args:
        vectors: Feature vectors.
        labels: Cluster assignment per sample.
        k: Number of clusters.

    Returns:
        Silhouette score in [-1, 1]. Higher is better.
    """
    n = len(vectors)
    if k <= 1 or n < 2:
        return -1.0

    # Precompute cluster membership
    clusters: dict[int, list[int]] = {j: [] for j in range(k)}
    for i, lbl in enumerate(labels):
        clusters[lbl].append(i)

    scores: list[float] = []
    for i in range(n):
        own_cluster = labels[i]
        own_members = [j for j in clusters[own_cluster] if j != i]

        if not own_members:
            scores.append(0.0)
            continue

        a_i = sum(_euclidean(vectors[i], vectors[j]) for j in own_members) / len(own_members)

        b_i = float("inf")
        for c in range(k):
            if c == own_cluster:
                continue
            other_members = clusters[c]
            if not other_members:
                continue
            mean_dist = sum(_euclidean(vectors[i], vectors[j]) for j in other_members) / len(other_members)
            b_i = min(b_i, mean_dist)

        if b_i == float("inf"):
            scores.append(0.0)
            continue

        denom = max(a_i, b_i)
        s_i = (b_i - a_i) / denom if denom > 1e-12 else 0.0
        scores.append(s_i)

    return sum(scores) / len(scores) if scores else -1.0


# ---------------------------------------------------------------------------
# Archetype naming
# ---------------------------------------------------------------------------

def _name_archetype(centroid_raw: list[float], means: list[float], stds: list[float]) -> str:
    """Assign a human-readable archetype name based on centroid characteristics.

    Denormalises the centroid back to original scale for interpretation, then
    applies a heuristic decision tree.

    Args:
        centroid_raw: Centroid in normalised (z-score) space.
        means: Per-feature means used for normalisation.
        stds: Per-feature standard deviations used for normalisation.

    Returns:
        One of: "action trailer", "emotional slow-build", "fast AMV",
        "multi-era montage", "single-character arc".
    """
    # Denormalise
    denorm = [c * stds[j] + means[j] for j, c in enumerate(centroid_raw)]
    feat = dict(zip(FEATURE_KEYS, denorm))

    tempo = feat["tempo_bpm"]
    cuts_ps = feat["cuts_per_second"]
    shot_dur = feat["shot_duration_median"]
    vo_cov = feat["vo_coverage_pct"]
    db_pct = feat["downbeat_alignment_pct"]
    duration = feat["duration_sec"]

    # Decision heuristics
    is_fast = cuts_ps > 0.7 or shot_dur < 1.2
    is_slow = shot_dur > 2.5 or cuts_ps < 0.3
    is_high_tempo = tempo > 140
    is_low_tempo = tempo < 100
    has_lots_of_vo = vo_cov > 30.0
    is_beat_precise = db_pct > 15.0
    is_long = duration > 300.0

    if is_fast and is_high_tempo and not has_lots_of_vo:
        return "fast AMV"
    if is_fast and is_beat_precise:
        return "action trailer"
    if is_slow and has_lots_of_vo:
        return "emotional slow-build"
    if is_long and not is_fast:
        return "multi-era montage"
    if has_lots_of_vo and not is_fast and not is_long:
        return "single-character arc"

    # Fallback: score each archetype by feature proximity to archetype profile
    archetype_profiles = {
        "action trailer":       dict(cuts_per_second=0.9, shot_duration_median=1.1, tempo_bpm=145, vo_coverage_pct=10, downbeat_alignment_pct=20),
        "emotional slow-build": dict(cuts_per_second=0.25, shot_duration_median=3.5, tempo_bpm=85, vo_coverage_pct=40, downbeat_alignment_pct=8),
        "fast AMV":             dict(cuts_per_second=1.2, shot_duration_median=0.8, tempo_bpm=155, vo_coverage_pct=5, downbeat_alignment_pct=12),
        "multi-era montage":    dict(cuts_per_second=0.4, shot_duration_median=2.5, tempo_bpm=120, vo_coverage_pct=15, downbeat_alignment_pct=10),
        "single-character arc": dict(cuts_per_second=0.5, shot_duration_median=2.0, tempo_bpm=105, vo_coverage_pct=35, downbeat_alignment_pct=9),
    }
    compare_keys = ["cuts_per_second", "shot_duration_median", "tempo_bpm", "vo_coverage_pct", "downbeat_alignment_pct"]
    best_name = "multi-era montage"
    best_dist = float("inf")
    for name, profile in archetype_profiles.items():
        d = math.sqrt(sum(
            ((feat.get(k, 0) - profile[k]) / max(abs(profile[k]), 1.0)) ** 2
            for k in compare_keys
        ))
        if d < best_dist:
            best_dist = d
            best_name = name
    return best_name


# ---------------------------------------------------------------------------
# Cluster template builder
# ---------------------------------------------------------------------------

def _build_cluster_template(
    archetype_name: str,
    centroid_raw: list[float],
    means: list[float],
    stds: list[float],
    member_profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a shot_optimizer-compatible style template for a cluster.

    Aggregates profile values across all cluster members to produce robust
    percentile estimates, then formats them as a .style-template.json dict.

    Args:
        archetype_name: Human-readable archetype label.
        centroid_raw: Cluster centroid in normalised space.
        means: Normalisation means.
        stds: Normalisation stds.
        member_profiles: Full raw profile dicts for members of this cluster.

    Returns:
        Dict compatible with the style_profile argument of plan_edit().
    """
    # Denormalised centroid
    denorm = {k: c * stds[j] + means[j] for j, (k, c) in enumerate(zip(FEATURE_KEYS, centroid_raw))}

    # Aggregate shot duration stats across members
    all_medians = [
        p.get("shot_duration_stats", {}).get("median", 2.0)
        for p in member_profiles
        if isinstance(p.get("shot_duration_stats"), dict)
    ]
    all_p25 = [
        p.get("shot_duration_stats", {}).get("p25", 0.8)
        for p in member_profiles
        if isinstance(p.get("shot_duration_stats"), dict)
    ]
    all_p75 = [
        p.get("shot_duration_stats", {}).get("p75", 3.5)
        for p in member_profiles
        if isinstance(p.get("shot_duration_stats"), dict)
    ]

    def _safe_mean(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    # Compute downbeat alignment from members
    def _downbeat_pct(p: dict[str, Any]) -> float:
        cuts_aligned = p.get("cuts_aligned", {})
        total_cuts = p.get("num_cuts", 1) or 1
        return (cuts_aligned.get("downbeat", 0) / total_cuts) * 100.0

    def _beat_pct(p: dict[str, Any]) -> float:
        cuts_aligned = p.get("cuts_aligned", {})
        total_cuts = p.get("num_cuts", 1) or 1
        beat_total = cuts_aligned.get("beat", 0) + cuts_aligned.get("downbeat", 0)
        return (beat_total / total_cuts) * 100.0

    db_pcts = [_downbeat_pct(p) for p in member_profiles]
    beat_pcts = [_beat_pct(p) for p in member_profiles]

    return {
        "_archetype": archetype_name,
        "_cluster_size": len(member_profiles),
        "_source_path": f"auto-generated:{archetype_name}",
        "shot_dur_median": round(_safe_mean(all_medians) if all_medians else denorm["shot_duration_median"], 4),
        "shot_dur_p25": round(_safe_mean(all_p25) if all_p25 else denorm["shot_duration_median"] * 0.6, 4),
        "shot_dur_p75": round(_safe_mean(all_p75) if all_p75 else denorm["shot_duration_median"] * 1.8, 4),
        "tempo_bpm": round(denorm["tempo_bpm"], 2),
        "cuts_per_second": round(denorm["cuts_per_second"], 4),
        "vo_coverage_pct": round(denorm["vo_coverage_pct"], 2),
        "downbeat_alignment_pct": round(_safe_mean(db_pcts) if db_pcts else denorm["downbeat_alignment_pct"], 2),
        "beat_alignment_pct": round(_safe_mean(beat_pcts) if beat_pcts else 22.0, 2),
        "opening_black_sec": round(denorm["opening_black_sec"], 3),
        "color_saturation_avg": round(denorm["color_saturation_avg"], 4),
        "duration_sec": round(denorm["duration_sec"], 1),
    }


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ClusterInfo:
    """One cluster in the result.

    Attributes:
        cluster_id: Zero-based cluster index.
        archetype_name: Human-readable name.
        size: Number of reference videos in this cluster.
        centroid: Denormalised centroid feature values keyed by feature name.
        member_video_paths: Video path strings for all cluster members.
        template: Style template dict for shot_optimizer.
    """

    cluster_id: int
    archetype_name: str
    size: int
    centroid: dict[str, float]
    member_video_paths: list[str]
    template: dict[str, Any]


@dataclass
class ClusterResult:
    """Full output of cluster_references().

    Attributes:
        k: The chosen k value.
        silhouette_score: Best silhouette score for chosen k.
        clusters: List of ClusterInfo, one per cluster.
        feature_means: Per-feature means used for normalisation.
        feature_stds: Per-feature standard deviations.
        profiles_dir: Directory that was scanned.
        n_profiles_loaded: How many profiles were successfully loaded.
    """

    k: int
    silhouette_score: float
    clusters: list[ClusterInfo]
    feature_means: list[float]
    feature_stds: list[float]
    profiles_dir: str
    n_profiles_loaded: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cluster_references(
    profiles_dir: Path | str,
    k: int | None = None,
) -> ClusterResult:
    """Load reference profiles, cluster them, and return archetype results.

    Args:
        profiles_dir: Directory containing .json style profile files.
        k: If provided, force this number of clusters. If None, tries
            k in {3, 4, 5, 6} and picks the k with the best silhouette score.

    Returns:
        ClusterResult with cluster assignments and per-cluster templates.

    Raises:
        FileNotFoundError: If profiles_dir does not exist.
        ValueError: If fewer than 4 valid profiles are found.
    """
    profiles_dir = Path(profiles_dir)
    if not profiles_dir.exists():
        raise FileNotFoundError(f"Profiles directory not found: {profiles_dir}")

    # Load all profiles
    json_files = sorted(profiles_dir.glob("*.json"))
    profiles: list[dict[str, Any]] = []
    profile_paths: list[str] = []

    for jf in json_files:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            profiles.append(data)
            # Prefer video_path from profile, fall back to filename
            profile_paths.append(str(data.get("video_path", jf.stem)))
        except Exception as exc:
            logger.warning("Skipping %s: %s", jf.name, exc)

    n = len(profiles)
    if n < 4:
        raise ValueError(f"Need at least 4 valid profiles, found {n} in {profiles_dir}")

    logger.info("Loaded %d profiles from %s", n, profiles_dir)

    # Extract features
    raw_matrix = [_extract_features(p) for p in profiles]

    # Z-score normalise
    norm_matrix, feature_means, feature_stds = _zscore_normalize(raw_matrix)

    # Determine k range
    k_candidates = [k] if k is not None else [3, 4, 5, 6]
    # Clamp k to at most n // 2 (need at least 2 points per cluster for silhouette)
    k_candidates = [min(kc, n // 2) for kc in k_candidates]
    k_candidates = list(dict.fromkeys(k_candidates))  # deduplicate, preserve order

    best_k = k_candidates[0]
    best_labels: list[int] = []
    best_centroids: list[list[float]] = []
    best_sil = -2.0

    for kc in k_candidates:
        labels, centroids, _ = _kmeans(norm_matrix, kc)
        sil = _silhouette_score(norm_matrix, labels, kc)
        logger.info("k=%d  silhouette=%.4f", kc, sil)
        if sil > best_sil:
            best_sil = sil
            best_k = kc
            best_labels = labels
            best_centroids = centroids

    logger.info("Chosen k=%d  silhouette=%.4f", best_k, best_sil)

    # Build ClusterInfo objects
    clusters: list[ClusterInfo] = []
    for j in range(best_k):
        member_indices = [i for i, lbl in enumerate(best_labels) if lbl == j]
        member_profiles = [profiles[i] for i in member_indices]
        member_paths = [profile_paths[i] for i in member_indices]

        centroid = best_centroids[j]
        archetype = _name_archetype(centroid, feature_means, feature_stds)

        # Denormalise centroid for display
        denorm_centroid = {
            k: round(centroid[j_] * feature_stds[j_] + feature_means[j_], 4)
            for j_, k in enumerate(FEATURE_KEYS)
        }

        template = _build_cluster_template(
            archetype, centroid, feature_means, feature_stds, member_profiles
        )

        clusters.append(ClusterInfo(
            cluster_id=j,
            archetype_name=archetype,
            size=len(member_indices),
            centroid=denorm_centroid,
            member_video_paths=member_paths,
            template=template,
        ))

    # Sort clusters by size descending for readability
    clusters.sort(key=lambda c: -c.size)

    return ClusterResult(
        k=best_k,
        silhouette_score=round(best_sil, 6),
        clusters=clusters,
        feature_means=feature_means,
        feature_stds=feature_stds,
        profiles_dir=str(profiles_dir),
        n_profiles_loaded=n,
    )


def save_cluster_result(
    result: ClusterResult,
    output_path: Path | str,
    templates_dir: Path | str | None = None,
) -> None:
    """Serialise cluster results to JSON and optionally write per-cluster templates.

    Args:
        result: ClusterResult from cluster_references().
        output_path: Path for the main .style-clusters.json output.
        templates_dir: If provided, write one .style-template-<archetype>.json
            per cluster here, suitable for direct use with plan_edit().
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cluster_data: list[dict[str, Any]] = []
    for c in result.clusters:
        cluster_data.append({
            "cluster_id": c.cluster_id,
            "archetype_name": c.archetype_name,
            "size": c.size,
            "centroid": c.centroid,
            "member_video_paths": c.member_video_paths,
            "template": c.template,
        })

    doc = {
        "k": result.k,
        "silhouette_score": result.silhouette_score,
        "n_profiles_loaded": result.n_profiles_loaded,
        "profiles_dir": result.profiles_dir,
        "feature_keys": list(FEATURE_KEYS),
        "feature_means": result.feature_means,
        "feature_stds": result.feature_stds,
        "clusters": cluster_data,
    }

    output_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    logger.info("Cluster results saved to %s", output_path)

    if templates_dir is not None:
        templates_dir = Path(templates_dir)
        templates_dir.mkdir(parents=True, exist_ok=True)
        for c in result.clusters:
            safe_name = c.archetype_name.replace(" ", "_").replace("-", "_")
            tpl_path = templates_dir / f".style-template-{safe_name}.json"
            tpl_path.write_text(json.dumps(c.template, indent=2), encoding="utf-8")
            logger.info("Cluster template written: %s", tpl_path)


def get_cluster_template(
    cluster_name: str,
    clusters_json_path: Path | str,
) -> dict[str, Any]:
    """Load and return the template dict for a named archetype cluster.

    Args:
        cluster_name: Archetype name, e.g. "fast AMV". Case-insensitive.
        clusters_json_path: Path to the .style-clusters.json file.

    Returns:
        Template dict compatible with plan_edit()'s style_profile argument.

    Raises:
        FileNotFoundError: If clusters_json_path does not exist.
        KeyError: If no cluster with that archetype name is found.
    """
    clusters_json_path = Path(clusters_json_path)
    if not clusters_json_path.exists():
        raise FileNotFoundError(f"Clusters file not found: {clusters_json_path}")

    doc = json.loads(clusters_json_path.read_text(encoding="utf-8"))
    needle = cluster_name.lower().strip()
    for c in doc.get("clusters", []):
        if c.get("archetype_name", "").lower() == needle:
            return c["template"]

    available = [c.get("archetype_name", "") for c in doc.get("clusters", [])]
    raise KeyError(
        f"No cluster named '{cluster_name}'. Available: {available}"
    )


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    import os as _os
    _PROJECT = Path(_os.environ.get(
        "FF_PROJECT",
        "/Users/damato/Video Project/projects/leon-badass-monologue",
    ))
    _PROFILES_DIR = _PROJECT / ".ref-profiles"
    _OUTPUT_CLUSTERS = _PROJECT / ".style-clusters.json"
    _TEMPLATES_DIR = _PROJECT

    if not _PROFILES_DIR.exists():
        print(f"ERROR: profiles directory not found: {_PROFILES_DIR}", file=sys.stderr)
        sys.exit(1)

    result = cluster_references(_PROFILES_DIR)

    print(f"\nChosen k={result.k}  silhouette={result.silhouette_score:.4f}")
    print(f"Loaded {result.n_profiles_loaded} profiles\n")
    for c in result.clusters:
        bar = "#" * c.size
        print(f"  [{c.cluster_id}] {c.archetype_name:<28} {c.size:3d} refs  {bar}")
        print(f"       BPM={c.centroid['tempo_bpm']:.0f}  "
              f"cuts/s={c.centroid['cuts_per_second']:.2f}  "
              f"shot_dur={c.centroid['shot_duration_median']:.2f}s  "
              f"VO={c.centroid['vo_coverage_pct']:.0f}%  "
              f"db_align={c.centroid['downbeat_alignment_pct']:.0f}%")

    save_cluster_result(result, _OUTPUT_CLUSTERS, templates_dir=_TEMPLATES_DIR)
    print(f"\nClusters saved to: {_OUTPUT_CLUSTERS}")
