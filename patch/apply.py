#!/usr/bin/env python3
"""Apply the artwork-quality patch to a Silo Server source tree.

Usage: python3 patch/apply.py <path-to-silo-server-checkout>

Every edit matches the upstream code by structure, not by exact value, and
must land exactly once. If upstream renames or moves anything, this exits
non-zero instead of producing an image that is silently unpatched.

What changes (see README.md for the reasoning):
  - WebP quality 90 -> 95 for every cached artwork variant
  - cached "original" longest edge 1920 -> 3000 px
  - poster/still ladder gains a 1280 px rung ("large": tvOS, Infuse)
  - LadderVersion + 100 so Silo backfills the new rung for existing artwork
  - poster/still sizes served to apps, in every path Silo picks them:
      * explicit image_size=small / medium     300 / 500 -> 780
      * no image_size (web UI, most endpoints) 500       -> 780
      * hard-coded card / featured paths       300 / 500 -> 780
    and backdrop cards (small / hard-coded card path) 300 -> 1280.
    780 and 1280 are rungs every cached poster / backdrop already has, so
    none of this waits on the ladder backfill.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

WEBP_QUALITY = 95
MAX_ORIGINAL_DIMENSION = 3000
POSTER_TOP_RUNG = 1280
POSTER_SERVED_WIDTH = 780      # small, medium and size-less poster/still requests
BACKDROP_CARD_WIDTH = 1280     # small backdrop requests (landscape cards)
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


def add_file(root: Path, rel: str, content: str) -> None:
    path = root / rel
    if path.exists():
        raise PatchError(f"{rel} already exists — upstream added a file with our name?")
    path.write_text(content, encoding="utf-8")
    print(f"  ok  added {rel}")


def patch(root: Path) -> None:
    imageutil = root / "internal/imageutil/imageutil.go"
    artworkkey = root / "internal/artworkkey/artworkkey.go"
    imagesize = root / "internal/imagesize/imagesize.go"
    catalog = root / "internal/catalog/detail.go"
    handlers = root / "internal/api/handlers/items.go"
    for f in (imageutil, artworkkey, imagesize, catalog, handlers):
        if not f.is_file():
            raise PatchError(f"missing {f.relative_to(root)} — upstream layout changed")
        if MARK in f.read_text(encoding="utf-8"):
            raise PatchError(f"{f.relative_to(root)} is already patched")

    # ── Encoding ─────────────────────────────────────────────────────────────
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

    # ── Ladder ───────────────────────────────────────────────────────────────
    def add_rung(m: re.Match) -> str:
        widths = [int(w) for w in re.findall(r"\d+", m.group(2))]
        if not widths:
            raise PatchError("poster/still ladder is empty")
        if POSTER_TOP_RUNG not in widths:
            widths.append(POSTER_TOP_RUNG)
        if POSTER_SERVED_WIDTH not in widths:
            # Every size we serve must name a rung that exists, or posters 404.
            raise PatchError(f"poster/still ladder {widths} has no {POSTER_SERVED_WIDTH} rung")
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
    # The backdrop card size must exist in the backdrop ladder.
    backdrop = re.search(r"case ImageBackdrop:\s*\n\s*return \[\]int\{([^}]*)\}",
                         artworkkey.read_text(encoding="utf-8"))
    if not backdrop or str(BACKDROP_CARD_WIDTH) not in re.findall(r"\d+", backdrop.group(1)):
        raise PatchError(f"backdrop ladder has no {BACKDROP_CARD_WIDTH} rung")

    # ── Served sizes: explicit image_size (apps, Jellyfin-mode cards) ────────
    # Only posters/stills/backdrops: logo/profile/banner ladders differ.
    sub_once(
        imagesize,
        r"(func Variant\(imageType string, size Size\) string \{\n)",
        (
            r"\g<1>"
            f"\tif v, ok := hqVariant(imageType, size); ok {{ // {MARK}\n"
            "\t\treturn v\n"
            "\t}\n"
        ),
        f"explicit small/medium -> w{POSTER_SERVED_WIDTH} (posters/stills)",
    )
    add_file(root, "internal/imagesize/hq_patch.go", f"""package imagesize

// {MARK}: sharper artwork for card- and default-size requests.

import (
\t"strings"

\t"github.com/Silo-Server/silo-server/internal/artworkkey"
)

// hqVariant overrides the rung an explicit size resolves to. Posters and
// stills get the {POSTER_SERVED_WIDTH} px rung for small and medium; backdrop cards (small)
// get {BACKDROP_CARD_WIDTH} px. Large and original keep the ladder's answer.
func hqVariant(imageType string, size Size) (string, bool) {{
\tswitch strings.ToLower(strings.TrimSpace(imageType)) {{
\tcase artworkkey.ImagePoster, artworkkey.ImageStill:
\t\tif size == Small || size == Medium || size == Unset {{
\t\t\treturn widthVariant({POSTER_SERVED_WIDTH}), true
\t\t}}
\tcase artworkkey.ImageBackdrop:
\t\tif size == Small {{
\t\t\treturn widthVariant({BACKDROP_CARD_WIDTH}), true
\t\t}}
\t}}
\treturn "", false
}}
""")

    # ── Served sizes: no image_size (web UI and most endpoints) ──────────────
    sub_once(
        catalog,
        r"(func cachedImageVariantKey\(imageType, size string\) string \{\n)",
        (
            r"\g<1>"
            f"\tif v, ok := hqLegacyVariant(imageType, size); ok {{ // {MARK}\n"
            "\t\treturn v\n"
            "\t}\n"
        ),
        f"size-less poster/still -> w{POSTER_SERVED_WIDTH}",
    )
    add_file(root, "internal/catalog/hq_patch.go", f"""package catalog

// {MARK}: sharper artwork for requests that name no image_size.

import "github.com/Silo-Server/silo-server/internal/imagesize"

// hqLegacyVariant overrides cachedImageVariantKey's historical defaults for
// size-less requests: posters and stills get the {POSTER_SERVED_WIDTH} px rung instead of
// 500. Explicit sizes are left to imagesize.Variant.
func hqLegacyVariant(imageType, size string) (string, bool) {{
\tif parsed, err := imagesize.Parse(size); err == nil && parsed != imagesize.Unset {{
\t\treturn "", false
\t}}
\tswitch imageType {{
\tcase "poster", "still":
\t\treturn "w{POSTER_SERVED_WIDTH}", true
\t}}
\treturn "", false
}}
""")

    # ── Served sizes: hard-coded card / featured paths ───────────────────────
    sub_once(
        handlers,
        r'return strings\.Replace\(path, "/original\.", "/w300\.", 1\)',
        f'return strings.Replace(path, "/original.", "/"+hqCardVariant(path)+".", 1) // {MARK}',
        f"card thumbnails -> w{POSTER_SERVED_WIDTH} (posters/stills), w{BACKDROP_CARD_WIDTH} (backdrops)",
    )
    sub_once(
        handlers,
        r'return strings\.Replace\(path, "/original\.", "/w500\.", 1\)',
        f'return strings.Replace(path, "/original.", "/"+hqFeaturedVariant(path)+".", 1) // {MARK}',
        f"featured posters -> w{POSTER_SERVED_WIDTH}",
    )
    add_file(root, "internal/api/handlers/hq_patch.go", f"""package handlers

// {MARK}: sharper artwork on the card and featured paths.

import "github.com/Silo-Server/silo-server/internal/catalog"

// hqCardVariant replaces cardThumbnailPath's fixed w300. Other image types
// keep w300 — their ladders may not carry the wider rungs.
func hqCardVariant(path string) string {{
\tswitch catalog.ImageTypeFromCachedPath(path) {{
\tcase "poster", "still":
\t\treturn "w{POSTER_SERVED_WIDTH}"
\tcase "backdrop":
\t\treturn "w{BACKDROP_CARD_WIDTH}"
\t}}
\treturn "w300"
}}

// hqFeaturedVariant replaces featuredPosterPath's fixed w500.
func hqFeaturedVariant(path string) string {{
\tswitch catalog.ImageTypeFromCachedPath(path) {{
\tcase "poster", "still":
\t\treturn "w{POSTER_SERVED_WIDTH}"
\t}}
\treturn "w500"
}}
""")

    # ── Tests the workflow runs before building ──────────────────────────────
    add_file(root, "internal/imagesize/hq_patch_test.go", f"""package imagesize

// {MARK}: pins the artwork-quality patch.

import (
\t"slices"
\t"testing"

\t"github.com/Silo-Server/silo-server/internal/artworkkey"
)

func TestHQPatch(t *testing.T) {{
\tcases := []struct {{
\t\timageType string
\t\tsize      Size
\t\twant      string
\t}}{{
\t\t{{"poster", Small, "w{POSTER_SERVED_WIDTH}"}},
\t\t{{"poster", Medium, "w{POSTER_SERVED_WIDTH}"}},
\t\t{{"poster", Large, "w{POSTER_TOP_RUNG}"}},
\t\t{{"poster", Original, "original"}},
\t\t{{"still", Small, "w{POSTER_SERVED_WIDTH}"}},
\t\t{{"still", Medium, "w{POSTER_SERVED_WIDTH}"}},
\t\t{{"still", Large, "w{POSTER_TOP_RUNG}"}},
\t\t{{"backdrop", Small, "w{BACKDROP_CARD_WIDTH}"}},
\t\t{{"backdrop", Medium, "w1920"}},
\t\t// Types the patch leaves alone.
\t\t{{"logo", Small, "w500"}},
\t\t{{"logo", Medium, "w500"}},
\t\t{{"profile", Small, "w300"}},
\t\t{{"profile", Medium, "w500"}},
\t}}
\tfor _, c := range cases {{
\t\tif got := Variant(c.imageType, c.size); got != c.want {{
\t\t\tt.Errorf("Variant(%q, %q) = %q, want %q", c.imageType, c.size, got, c.want)
\t\t}}
\t}}
\t// Every size must resolve to a rung that actually gets generated.
\tfor _, typ := range []string{{"poster", "still", "logo", "profile", "backdrop", "thumb", "banner", "disc"}} {{
\t\tfor _, size := range All {{
\t\t\tif v := Variant(typ, size); !slices.Contains(artworkkey.VariantNames(typ), v) {{
\t\t\t\tt.Errorf("Variant(%q, %q) = %q is not generated (ladder %v)", typ, size, v, artworkkey.VariantNames(typ))
\t\t\t}}
\t\t}}
\t}}
}}
""")
    add_file(root, "internal/catalog/hq_patch_test.go", f"""package catalog

// {MARK}: pins the size-less defaults.

import "testing"

func TestHQPatchLegacyDefaults(t *testing.T) {{
\tcases := []struct{{ imageType, size, want string }}{{
\t\t{{"poster", "", "w{POSTER_SERVED_WIDTH}"}},
\t\t{{"still", "", "w{POSTER_SERVED_WIDTH}"}},
\t\t{{"poster", "small", "w{POSTER_SERVED_WIDTH}"}},
\t\t{{"poster", "large", "w{POSTER_TOP_RUNG}"}},
\t\t{{"poster", "original", "original"}},
\t\t{{"backdrop", "", "w1920"}},
\t\t{{"backdrop", "small", "w{BACKDROP_CARD_WIDTH}"}},
\t\t{{"logo", "", "w500"}},
\t}}
\tfor _, c := range cases {{
\t\tif got := cachedImageVariantKey(c.imageType, c.size); got != c.want {{
\t\t\tt.Errorf("cachedImageVariantKey(%q, %q) = %q, want %q", c.imageType, c.size, got, c.want)
\t\t}}
\t}}
}}
""")
    add_file(root, "internal/api/handlers/hq_patch_test.go", f"""package handlers

// {MARK}: pins the card / featured paths.

import "testing"

func TestHQPatchCardPaths(t *testing.T) {{
\tcases := []struct{{ in, card, featured string }}{{
\t\t{{"tmdb/movies/550/poster/original.abc.webp", "tmdb/movies/550/poster/w{POSTER_SERVED_WIDTH}.abc.webp", "tmdb/movies/550/poster/w{POSTER_SERVED_WIDTH}.abc.webp"}},
\t\t{{"tmdb/tv/1/season/1/episode/2/still/original.abc.webp", "tmdb/tv/1/season/1/episode/2/still/w{POSTER_SERVED_WIDTH}.abc.webp", "tmdb/tv/1/season/1/episode/2/still/w{POSTER_SERVED_WIDTH}.abc.webp"}},
\t\t{{"tmdb/movies/550/backdrop/original.abc.webp", "tmdb/movies/550/backdrop/w{BACKDROP_CARD_WIDTH}.abc.webp", "tmdb/movies/550/backdrop/w500.abc.webp"}},
\t\t{{"tmdb/people/9/profile/original.abc.webp", "tmdb/people/9/profile/w300.abc.webp", "tmdb/people/9/profile/w500.abc.webp"}},
\t\t{{"https://image.tmdb.org/t/p/original/x.jpg", "https://image.tmdb.org/t/p/original/x.jpg", "https://image.tmdb.org/t/p/original/x.jpg"}},
\t}}
\tfor _, c := range cases {{
\t\tif got := cardThumbnailPath(c.in); got != c.card {{
\t\t\tt.Errorf("cardThumbnailPath(%q) = %q, want %q", c.in, got, c.card)
\t\t}}
\t\tif got := featuredPosterPath(c.in); got != c.featured {{
\t\t\tt.Errorf("featuredPosterPath(%q) = %q, want %q", c.in, got, c.featured)
\t\t}}
\t}}
}}
""")


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
