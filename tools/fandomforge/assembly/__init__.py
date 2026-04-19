"""Assembly — produce rough cuts from shot lists + raw sources + beat maps."""

from fandomforge.assembly.parser import parse_shot_list, ShotEntry
from fandomforge.assembly.assemble import assemble_rough_cut, AssemblyResult
from fandomforge.assembly.mixer import mix_audio, MixResult, DialogueCue
from fandomforge.assembly.color import apply_base_grade, ColorPreset
from fandomforge.assembly.scenes import detect_scenes
from fandomforge.assembly.trim import trim_silence
from fandomforge.assembly.orchestrator import build_rough_cut, RoughCutResult
from fandomforge.assembly.export_presets import (
    export_preset,
    export_all_presets,
    list_presets,
    ExportResult,
)

__all__ = [
    "parse_shot_list",
    "ShotEntry",
    "assemble_rough_cut",
    "AssemblyResult",
    "mix_audio",
    "MixResult",
    "DialogueCue",
    "apply_base_grade",
    "ColorPreset",
    "detect_scenes",
    "trim_silence",
    "build_rough_cut",
    "RoughCutResult",
    "export_preset",
    "export_all_presets",
    "list_presets",
    "ExportResult",
]
