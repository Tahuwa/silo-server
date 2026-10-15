#!/usr/bin/env python3
"""Apply the artwork-quality patch to a Silo Server source tree.

Usage: python3 patch/apply.py <path-to-silo-server-checkout>

Every edit matches the upstream code by structure, not by exact value, and
must land exactly once. If upstream renames or moves anything, this exits
non-zero instead of producing an image that is silently unpatched.

What changes (see README.md for the reasoning):
  - WebP quality 90 -> 95 for every cached artwork variant
  - cached "original" longest edge 1920 -> 3000 px
  - poster/still ladder gains a 1280 px rung ("large" for tvOS / Infuse)
  - poster/still "medium" (served when a client asks for no size) 500 -> 780
  - LadderVersion + 100 so Silo backfills the new rung for existing artwork
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

WEBP_QUALITY = 95
MAX_ORIGINAL_DIMENSION = 3000
POSTER_TOP_RUNG = 1280
POSTER_MEDIUM_WIDTH = 780
LADDER_VERSION_OFFSET = 100

MARK = "silo-server-hq"


class PatchError(Exception):
    pass


def sub_once(path: Path, pattern: str, repl, what: str, flags: int = 0) -> None:
    text = path.read_text(encoding="utf-8")
    new, count = re.subn(pattern, repl, text, flags=flags)
    if count != 1:
        raise PatchError(f"{what}: expected exactly 1 match in {path}, found {count}")
    path.write_text(new, encoding="utf-8")
    print(f"  ok  {what}")


def patch(root: Path) -> None:
    imageutil = root / "internal/imageutil/imageutil.go"
    artworkkey = root / "internal/artworkkey/artworkkey.go"
    imagesize = root / "internal/imagesize/imagesize.go"
    for f in (imageutil, artworkkey, imagesize):
        if not f.is_file():
            raise PatchError(f"missing {f.relative_to(root)} — upstream layout changed")
        if MARK in f.read_text(encoding="utf-8"):
            raise PatchError(f"{f.relative_to(root)} is already patched")

    sub_once(
        imageutil,
        r"(\bwebpQuality\s*=\s*)\d+",
        rf"\g<1>{WEBP_QUALITY} // {MARK}",
        f"WebP quality -> {WEBP_QUALITY}",
    )
    sub_once(
        imageutil,
        r"(\bconst MaxCachedOriginalDimension\s*=\s*)\d+",
        rf"\g<1>{MAX_ORIGINAL_DIMENSION} // {MARK}",
        f"original cap -> {MAX_ORIGINAL_DIMENSION}px",
    )

    def add_rung(m: re.Match) -> str:
        widths = [int(w) for w in re.findall(r"\d+", m.group(2))]
        if not widths:
            raise PatchError("poster/still ladder is empty")
        if POSTER_TOP_RUNG not in widths:
            widths.append(POSTER_TOP_RUNG)
        if POSTER_MEDIUM_WIDTH not in widths:
            # "medium" must name a rung that exists, or posters 404.
            raise PatchError(f"poster/still ladder {widths} has no {POSTER_MEDIUM_WIDTH} rung")
        widths.sort(reverse=True)
        return f"{m.group(1)}{', '.join(map(str, widths))}}} // {MARK}"

    sub_once(
        artworkkey,
        r"(default:\s*//\s*poster, still\s*\n\s*return \[\]int\{)([^}]*)\}",
        add_rung,
        f"poster/still ladder += {POSTER_TOP_RUNG}",
    )
    sub_once(
        artworkkey,
        r"(\bconst LadderVersion\s*=\s*)(\d+)",
        lambda m: f"{m.group(1)}{int(m.group(2)) + LADDER_VERSION_OFFSET} // {MARK}: upstream + {LADDER_VERSION_OFFSET}",
        f"LadderVersion + {LADDER_VERSION_OFFSET}",
    )

    # Only posters and stills: logo/profile/banner ladders have no 780 rung.
    sub_once(
        imagesize,
        r"(func mediumVariant\(imageType string\) string \{\n)",
        (
            r"\g<1>"
            f"\t// {MARK}: posters and stills default to the {POSTER_MEDIUM_WIDTH} px rung.\n"
            "\tswitch strings.ToLower(strings.TrimSpace(imageType)) {\n"
            "\tcase artworkkey.ImagePoster, artworkkey.ImageStill:\n"
            f"\t\treturn widthVariant({POSTER_MEDIUM_WIDTH})\n"
            "\t}\n"
        ),
        f"poster/still medium -> w{POSTER_MEDIUM_WIDTH}",
    )

    # A test that pins the patched behaviour, run by the workflow before the
    # image build — so a structural match that compiles but misbehaves still
    # fails the build.
    (root / "internal/imagesize/hq_patch_test.go").write_text(
        f"""package imagesize

// {MARK}: pins the artwork-quality patch.

import (
\t"testing"

\t"github.com/Silo-Server/silo-server/internal/artworkkey"
)

func TestHQPatch(t *testing.T) {{
\tcases := []struct {{
\t\timageType string
\t\tsize      Size
\t\twant      string
\t}}{{
\t\t{{"poster", Medium, "w{POSTER_MEDIUM_WIDTH}"}},
\t\t{{"still", Medium, "w{POSTER_MEDIUM_WIDTH}"}},
\t\t{{"poster", Large, "w{POSTER_TOP_RUNG}"}},
\t\t{{"still", Large, "w{POSTER_TOP_RUNG}"}},
\t\t{{"poster", Small, "w300"}},
\t\t{{"backdrop", Medium, "w1920"}},
\t\t// Types without a {POSTER_MEDIUM_WIDTH} rung keep their old default.
\t\t{{"logo", Medium, "w500"}},
\t\t{{"profile", Medium, "w500"}},
\t}}
\tfor _, c := range cases {{
\t\tif got := Variant(c.imageType, c.size); got != c.want {{
\t\t\tt.Errorf("Variant(%q, %q) = %q, want %q", c.imageType, c.size, got, c.want)
\t\t}}
\t}}
\t// Every medium variant must be a rung that actually gets generated.
\tfor _, typ := range []string{{"poster", "still", "logo", "profile", "backdrop", "thumb", "banner", "disc"}} {{
\t\tv := Variant(typ, Medium)
\t\tfound := false
\t\tfor _, name := range artworkkey.VariantNames(typ) {{
\t\t\tif name == v {{
\t\t\t\tfound = true
\t\t\t}}
\t\t}}
\t\tif !found {{
\t\t\tt.Errorf("medium variant %q for %q is not generated (ladder %v)", v, typ, artworkkey.VariantNames(typ))
\t\t}}
\t}}
}}
""",
        encoding="utf-8",
    )
    print("  ok  added internal/imagesize/hq_patch_test.go")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).resolve()
    print(f"Patching {root}")
    try:
        patch(root)
    except PatchError as exc:
        print(f"PATCH FAILED: {exc}", file=sys.stderr)
        return 1
    print("Patch applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
