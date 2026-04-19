"""Per-source color plan — different color presets per source ID.

Color plans are JSON files at projects/<slug>/color-plan.json:

    {
      "default": "tactical",
      "sources": {
        "re2r-leon-shirrako": "desaturated_warm",
        "re4r-all-glp": "teal_orange",
        "re6-leon-edition": "cool_cinematic",
        "vendetta-full-rental": "crushed_noir"
      },
      "act_overrides": {
        "1": "nostalgic",
        "3": "crushed_noir",
        "5": "tactical"
      }
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fandomforge.assembly.color import ColorPreset


@dataclass
class ColorPlan:
    """Per-source / per-act color configuration."""

    default: ColorPreset = ColorPreset.TACTICAL
    sources: dict[str, ColorPreset] = field(default_factory=dict)
    act_overrides: dict[int, ColorPreset] = field(default_factory=dict)

    def preset_for(self, source_id: str, act: int) -> ColorPreset:
        """Resolve which preset to use for a given shot."""
        # Source-specific > act override > default
        if source_id in self.sources:
            return self.sources[source_id]
        if act in self.act_overrides:
            return self.act_overrides[act]
        return self.default


def load_color_plan(path: str | Path) -> ColorPlan:
    """Load a color-plan.json file."""
    path = Path(path)
    if not path.exists():
        return ColorPlan()

    with path.open("r") as f:
        data = json.load(f)

    def _to_preset(value: Any) -> ColorPreset:
        if isinstance(value, str):
            try:
                return ColorPreset(value)
            except ValueError:
                return ColorPreset.NONE
        return ColorPreset.NONE

    plan = ColorPlan(
        default=_to_preset(data.get("default", "tactical")),
    )
    for src_id, preset_name in data.get("sources", {}).items():
        plan.sources[src_id] = _to_preset(preset_name)
    for act_str, preset_name in data.get("act_overrides", {}).items():
        try:
            act_num = int(act_str)
            plan.act_overrides[act_num] = _to_preset(preset_name)
        except ValueError:
            continue

    return plan


def save_color_plan(plan: ColorPlan, path: str | Path) -> None:
    """Serialize a ColorPlan to JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "default": plan.default.value,
        "sources": {k: v.value for k, v in plan.sources.items()},
        "act_overrides": {str(k): v.value for k, v in plan.act_overrides.items()},
    }
    with path.open("w") as f:
        json.dump(data, f, indent=2)
