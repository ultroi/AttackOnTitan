"""Stat helpers — centralize buff semantics.

Semantics (canonical):
- For stat keys (ATK, DEF, ACC, INT, SPD, HP):
  - float buff values are treated as multipliers (e.g. 1.25 -> +25%)
  - int buff values are treated as additive bonuses (e.g. 10 -> +10 flat)
- Other buff keys (attack_boost, crit_boost, shield, etc.) are left to their callers.

Use these helpers from BattleSystem / PvP so abilities and systems observe identical rules.
"""
from typing import Dict, Any, Iterable, List

STAT_KEYS: List[str] = ["ATK", "DEF", "ACC", "INT", "SPD", "HP"]


def apply_stat_buffs(stats: Dict[str, int], buffs: Dict[str, Any], stat_keys: Iterable[str] = STAT_KEYS) -> Dict[str, int]:
    """Return a new dict with `stat_keys` adjusted by `buffs` using canonical semantics.

    - `stats` may be a dict-like mapping of stat -> int.
    - `buffs` may include stat-name keys (floats = multiplier, ints = additive).
    - Unknown or non-numeric buff values are ignored.
    """
    if not stats:
        stats = {k: 0 for k in stat_keys}

    # make a shallow copy so original is not mutated
    out = {k: int(stats.get(k, 0)) for k in stat_keys}

    if not buffs:
        return out

    for key in stat_keys:
        if key not in buffs:
            continue
        try:
            v = buffs[key]
            base = int(stats.get(key, 0))
            # floats -> multiplier, ints -> additive
            if isinstance(v, float):
                out[key] = int(round(base * v))
            elif isinstance(v, int):
                out[key] = base + int(v)
            else:
                # try to coerce numeric-like strings (best-effort)
                if isinstance(v, str):
                    if '.' in v:
                        fv = float(v)
                        out[key] = int(round(base * fv))
                    else:
                        iv = int(v)
                        out[key] = base + iv
                else:
                    # unknown type; ignore
                    out[key] = base
        except Exception:
            out[key] = int(stats.get(key, 0))

    return out


def get_effective_stat(stats: Dict[str, int], buffs: Dict[str, Any], stat_name: str) -> int:
    """Convenience wrapper to return single effective stat value."""
    return apply_stat_buffs(stats, buffs).get(stat_name, int(stats.get(stat_name, 0)))
