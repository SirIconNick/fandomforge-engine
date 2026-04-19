"""Tests for the lyric + song-point sync planner."""

from __future__ import annotations

from fandomforge.intelligence.sync_planner import (
    LyricSection,
    build_sync_plan,
    classify_emotion,
    derive_song_points,
    match_shots_to_song_points,
)


class TestClassifyEmotion:
    def test_empty_is_neutral(self) -> None:
        assert classify_emotion("") == "neutral"

    def test_intense_lyrics(self) -> None:
        assert classify_emotion("fire in my blood, unleash the storm") == "intense"

    def test_somber_lyrics(self) -> None:
        assert classify_emotion("alone in the rain, tears and regret") == "somber"

    def test_defiant_lyrics(self) -> None:
        assert classify_emotion("i won't stand down, never again") == "defiant"

    def test_unknown_is_neutral(self) -> None:
        assert classify_emotion("the quick brown fox") == "neutral"


class TestDeriveSongPoints:
    def test_merges_lyrics_and_structure(self) -> None:
        beat_map = {
            "duration_sec": 180,
            "drops": [{"time": 60.0, "intensity": 0.9, "type": "main"}],
            "buildups": [{"start": 55.0, "end": 60.0, "curve": "exponential"}],
            "breakdowns": [{"start": 120.0, "end": 130.0, "intensity": 0.3}],
        }
        lyrics = [
            LyricSection(10.0, 12.0, "hello", "neutral", 0.9),
            LyricSection(30.0, 32.0, "i won't back down", "defiant", 0.9),
        ]
        points = derive_song_points(beat_map, lyrics)
        # Should include: 2 lyrics + 1 drop + 1 buildup + 1 breakdown = 5 points
        assert len(points) == 5
        # Drops should carry intense emotion automatically
        assert any(p.type == "drop" and p.emotion == "intense" for p in points)
        # Sorted by time
        times = [p.time_sec for p in points]
        assert times == sorted(times)


class TestMatchShotsToSongPoints:
    def _shot(self, id: str, *, role: str = "action", mood: list[str] | None = None,
              act: int = 1, start_frame: int = 0, fandom: str = "") -> dict:
        return {
            "id": id, "role": role, "mood_tags": mood or [],
            "act": act, "start_frame": start_frame, "fandom": fandom,
        }

    def test_intense_point_prefers_action_shot(self) -> None:
        from fandomforge.intelligence.sync_planner import SongPoint
        shots = [
            self._shot("s1", role="action", mood=["combat"]),
            self._shot("s2", role="reaction", mood=["melancholy"]),
        ]
        drop = SongPoint(
            id="drop1", time_sec=60, end_sec=None, type="drop",
            label="drop", emotion="intense", intensity=0.95,
        )
        results = match_shots_to_song_points([drop], shots, song_duration=120)
        assert results[0]["recommended_shots"][0]["shot_id"] == "s1"

    def test_somber_point_prefers_reaction_shot(self) -> None:
        from fandomforge.intelligence.sync_planner import SongPoint
        shots = [
            self._shot("s1", role="action", mood=["combat"]),
            self._shot("s2", role="reaction", mood=["melancholy"]),
        ]
        somber = SongPoint(
            id="lyric1", time_sec=10, end_sec=12, type="lyric",
            label="alone", emotion="somber", intensity=0.3,
        )
        results = match_shots_to_song_points([somber], shots, song_duration=120)
        assert results[0]["recommended_shots"][0]["shot_id"] == "s2"

    def test_no_accidental_reuse_when_options_exist(self) -> None:
        from fandomforge.intelligence.sync_planner import SongPoint
        shots = [
            self._shot("s1", role="action", mood=["combat"]),
            self._shot("s2", role="action", mood=["combat"]),
        ]
        p1 = SongPoint(id="d1", time_sec=60, end_sec=None, type="drop",
                       label="d", emotion="intense", intensity=0.9)
        p2 = SongPoint(id="d2", time_sec=90, end_sec=None, type="drop",
                       label="d", emotion="intense", intensity=0.9)
        results = match_shots_to_song_points([p1, p2], shots, song_duration=120)
        # Top pick for second point should differ from first — reuse penalty works
        first = results[0]["recommended_shots"][0]["shot_id"]
        second = results[1]["recommended_shots"][0]["shot_id"]
        assert first != second


class TestBuildSyncPlan:
    def test_validates_and_returns_plan(self) -> None:
        beat_map = {
            "song": "Test", "artist": "Test", "duration_sec": 60,
            "bpm": 120, "bpm_confidence": 0.9, "time_signature": "4/4",
            "beats": [], "downbeats": [], "onsets": [],
            "drops": [{"time": 30.0, "intensity": 0.9, "type": "main"}],
        }
        shot_list = {
            "schema_version": 1, "project_slug": "t",
            "fps": 24, "resolution": {"width": 1920, "height": 1080},
            "shots": [
                {
                    "id": "s1", "act": 1, "start_frame": 0,
                    "duration_frames": 48, "source_id": "src1",
                    "source_timecode": "0:00:01.000", "role": "action",
                    "mood_tags": ["combat"],
                },
            ],
        }
        plan = build_sync_plan(
            project_slug="t",
            beat_map=beat_map,
            shot_list=shot_list,
        )
        assert plan["schema_version"] == 1
        assert plan["project_slug"] == "t"
        assert len(plan["song_points"]) == 1  # just the drop (no lyrics)
