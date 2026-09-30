"""Map the old UI's hard-coded green palette to the new design's blue scale (updated_new_ui tokens).

    python _qa_audit_tmp/redesign_consistency/recolor.py <file> [<file> ...]      # dry run: counts
    python _qa_audit_tmp/redesign_consistency/recolor.py --apply <file> [...]

Maps by ROLE (dark brand, mid, tint, border, green-tinted greys), not nearest hue. Leaves brand
logos (Google #4CAF50/#FFC107/#FF3D00/#1976D2, LinkedIn #0a66c2) and semantic colours
(error red, warning amber) untouched. Case-insensitive; preserves everything else.
"""
import re
import sys
from pathlib import Path

HEX = {
    # dark brand greens -> blue-800/900
    "#0e5c2f": "#12408a", "#14532d": "#12408a", "#166534": "#1a56db", "#15803d": "#1d4ed8",
    "#064e3b": "#0b3d91", "#065f46": "#12408a", "#047857": "#1a56db", "#1b5e20": "#12408a",
    "#2e7d32": "#1a56db", "#388e3c": "#2563eb", "#43a047": "#2563eb", "#1e7e34": "#1a56db",
    # mid greens -> blue-600/700
    "#1f9c46": "#2563eb", "#1c7c3e": "#1a56db", "#2fae52": "#3b82f6", "#2f9e4f": "#2563eb",
    "#16a34a": "#2563eb", "#22c55e": "#3b82f6", "#10b981": "#3b82f6", "#059669": "#2563eb",
    "#28a745": "#2563eb", "#4ade80": "#60a5fa", "#7cc954": "#60a5fa", "#84cc16": "#2563eb",
    "#34d399": "#60a5fa", "#6ee7b7": "#93c5fd", "#66bb6a": "#60a5fa", "#81c784": "#93c5fd",
    # tints -> blue-50/100/200
    "#8fd6a6": "#93c5fd", "#86efac": "#93c5fd", "#bbf7d0": "#bfdbfe", "#bfe6cc": "#bfdbfe",
    "#bfe0c9": "#bfdbfe", "#cdeeda": "#bfdbfe", "#a7f3d0": "#bfdbfe", "#c8e6c9": "#bfdbfe",
    "#d1fae5": "#dbeafe", "#dcfce7": "#dbeafe", "#dff2e6": "#dbeafe", "#d8ecdd": "#dbeafe",
    "#d5eedc": "#dbeafe", "#e8f5e9": "#eff6ff", "#ecfdf5": "#eff6ff", "#eaf7ee": "#eff6ff",
    "#f0fdf4": "#eff6ff", "#f1f8e9": "#eff6ff",
    # green-tinted greys / borders / text -> slate
    "#d7e6dc": "#dbe7fb", "#c6dccd": "#cbd5e1", "#d9e4dd": "#e2e8f0", "#e2ece5": "#e2e8f0",
    "#cfe4d6": "#cbd5e1", "#a9bdb0": "#94a3b8", "#9fb3a7": "#94a3b8", "#5c6b62": "#64748b",
    "#16241c": "#1e293b", "#f5f8f6": "#f8fafc", "#101314": "#0f172a",
}
RGB = {  # rgb(a) triplets
    "20,80,40": "26,86,219", "242,249,244": "244,247,252", "217,240,224": "219,234,254",
    "230,244,234": "239,246,255", "10,15,12": "15,23,42", "20,24,21": "15,23,42",
    "22,101,52": "26,86,219", "34,197,94": "59,130,246", "16,185,129": "59,130,246",
}


# Admin pages: only the old cyan brand accent -> design blue. Their greens are semantic
# (success / "live" / teacher-category colours) and must stay green.
CYAN_ONLY_HEX = {"#05b0fc": "#1a56db", "#0490cf": "#12408a"}
CYAN_ONLY_RGB = {"5,176,252": "26,86,219"}


def recolor(text: str, cyan_only: bool = False):
    HEX_MAP = CYAN_ONLY_HEX if cyan_only else HEX
    RGB_MAP = CYAN_ONLY_RGB if cyan_only else RGB
    count = 0

    def hex_sub(m):
        nonlocal count
        new = HEX_MAP.get(m.group(0).lower())
        if new:
            count += 1
            return new
        return m.group(0)

    text = re.sub(r"#[0-9a-fA-F]{6}\b", hex_sub, text)

    def rgb_sub(m):
        nonlocal count
        key = re.sub(r"\s+", "", m.group(2))
        new = RGB_MAP.get(key)
        if new:
            count += 1
            return f"{m.group(1)}({new}"
        return m.group(0)

    text = re.sub(r"(rgba?)\((\s*\d+\s*,\s*\d+\s*,\s*\d+)", rgb_sub, text)
    return text, count


def main(argv):
    apply = "--apply" in argv
    cyan_only = "--cyan-only" in argv
    files = [a for a in argv if not a.startswith("--")]
    total = 0
    for f in files:
        p = Path(f)
        src = p.read_text(encoding="utf-8")
        out, n = recolor(src, cyan_only=cyan_only)
        total += n
        print(f"{n:4d}  {f}")
        if apply and n:
            p.write_text(out, encoding="utf-8")
    print(f"{total:4d}  total{' (applied)' if apply else ' (dry run)'}")


if __name__ == "__main__":
    main(sys.argv[1:])
