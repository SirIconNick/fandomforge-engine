"""Intelligence layer — AI/ML features for finding, filtering, and matching content.

Modules:
- dialogue_finder: SRT and Whisper-based dialogue line search
- lut: Real .cube LUT application and management
- preview: Thumbnail grid generation for shot-list QA
- face_filter: face_recognition-based character filtering
- clip_search: OpenCLIP semantic shot search
- color_match: Histogram-based per-shot color matching
- transitions: Real whip-pan, flash-stack, speed-ramp generators
- stem_separator: Demucs wrapper for dialogue cleaning
- song_structure: Deep audio analysis -- tempo, beats, downbeats, labeled sections,
    per-beat classification, drop/breath transitions, and JSON persistence.
- narrative_templates: Story arc dataclasses (SingleCharacterJourney, MultiEraFlashback,
    RiseAndFall, HauntedVeteran, EnsembleTribute) with StorySlot definitions and
    a global registry.
- shot_optimizer: Beat-aligned shot planner. Takes a NarrativeTemplate, style profile,
    SongStructure, ShotLibrary, and dialogue cues. Returns an EditPlan with ShotRecord
    list, VOPlacement list, and editorial stats.
- dialogue_planner: Specialised VO placement that refines shot_optimizer's initial pass.
    Enforces pre-cut anchoring (2-4 frames), drop-zone exclusion, no back-to-back rule,
    and hits the exact coverage target from the style profile.
- color_grader: Per-source LAB colour analysis and .cube LUT generation. Includes six
    Leon-era hardcoded recipes (RE2R, RE4R, RE6, Damnation, Vendetta, ID, RE9) and a
    computed path for generic sources.
- qa_loop: End-to-end five-gate quality check (audio, visual, pacing, structural,
    narrative) with prioritised fix suggestions. Extends auto_test.py.
- auto_test: Light-weight dialogue intelligibility and loudness check (subset of qa_loop).
- motion_flow: OpenCV Farneback dense optical flow analysis per shot. Produces
    MotionProfile (direction, magnitude, kind, confidence). Stores results in
    shot_library.db via analyze_library(). Public: analyze_shot(), analyze_library().
- gaze_detector: dlib HOG + 68-pt landmark face detection per shot. Estimates head
    pose and maps to gaze direction tags (left/right/up/down/center/off_screen/none).
    Stores results in shot_library.db. Public: detect_gaze(), analyze_library().
- transition_scorer: Multi-factor cut quality scorer (motion, gaze, luminance, subject
    size, source run, color temperature). Used by shot_optimizer as an extra cost term
    during candidate selection. Public: score_transition(), score_sequence(),
    transition_cost(), ShotData.
- storyboard: Pre-render storyboard PNG grid generator. Extracts representative frames,
    overlays metadata (timecode, source, mood, beat/VO/peak markers, transition warnings),
    outputs a 4-column PNG for editor review. Public: build_storyboard(),
    build_storyboard_from_json().
- multi_era_vo: Multi-era Leon S. Kennedy VO extraction. Whisper API transcription for
    RE2R/RE4R/RE6/Damnation/Vendetta/ID, SRT parsing for RE9. Per-line pipeline:
    ffmpeg extract + 0.5s pre-roll, Demucs mdx_extra vocal isolation, loudnorm to
    -14 LUFS, 15ms fade-in, round-trip Whisper verification. Targets 5-10 lines per era.
    Public API: extract_era_vo(), build_full_vo_library().
- per_section_ducking: Section-aware song ducking. Verse=-15 dB, Chorus=-8 dB,
    Breakdown=-20 dB, Drop=-6 dB + 3 kHz shelf cut, Bridge=-12 dB. Integrates with
    audio_engine.mix() via the new song_structure parameter. Public API:
    compute_duck_envelope(), apply_duck_envelope_to_cues(), log_duck_schedule().
- sfx_layer: Royalty-free SFX synthesis via ffmpeg (whoosh x3, impact x3, gun cock,
    heartbeat). Auto-places whooshes on cuts, impacts on drops, heartbeats on tense/
    brooding shots. Returns SFXCue list for audio_engine integration. Public API:
    generate_sfx_library(), auto_place_sfx().
- song_footage_matcher: Song-to-footage compatibility scorer. Four subscores: arc_match
    (cosine similarity of song section energy arc vs library emotion distribution),
    mood_coverage (fraction of sections with enough library shots), pace_match (BPM vs
    library action pace), coverage_sufficiency (total screen time vs song duration).
    Public API: score_match(), rank_songs(), print_ranking().
- thumbnail_selector: Best-thumbnail picker for rendered videos. Samples 30-50 candidate
    frames, scores on face presence, sharpness (Laplacian variance), rule-of-thirds,
    saturation/contrast, and motion-blur penalty. Optional GPT-4o-mini "which is the
    most compelling thumbnail?" picker over the top-5 results. Tribute-video mode
    emphasises character face + dramatic lighting. Public API: select_thumbnail(),
    ThumbnailResult.
- caption_generator: SRT and WebVTT caption generator. Two modes: edit_plan mode builds
    captions from DialogueEntry / VOPlacement objects already in the plan; Whisper mode
    transcribes the rendered video via OpenAI Whisper API. Speaker labels supported.
    Public API: generate_captions(), generate_captions_from_audio(), CaptionEntry.
- youtube_metadata: YouTube publishing metadata generator. Builds title (< 70 chars),
    description (CHAPTERS + song credit + sources + fair-use), tags (15-20), category
    suggestion, and end-screen timing. Uses GPT-4o-mini when available; falls back to
    templates. Public API: build_youtube_metadata(), SongInfo, YouTubeMetadata.
- copyright_audit: Fair-use audit for tribute edits. MusicBrainz song lookup, per-source
    footage totals, long-clip warnings (> 30 s), DMCA sensitivity flags (four-factor
    test), risk score (low/medium/high), and YouTube-ready fair-use statement. Output as
    Markdown, JSON, or CopyrightAudit dataclass. Public API: audit(), CopyrightAudit,
    SongMetadata, SourceMetadata.
"""
