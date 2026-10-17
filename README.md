# silo-server (artwork quality patch)

An always-current build of [Silo Server](https://github.com/Silo-Server/silo-server)
with sharper posters, title cards and backdrops.

Every 4 hours a GitHub Actions workflow checks the official
`ghcr.io/silo-server/silo-server:latest` image. When upstream has shipped a new
build, it rebuilds **that exact source commit** with a small patch and publishes
it here:

```
ghcr.io/tahuwa/silo-server:latest
```

Apart from the five changes below, the image is upstream Silo.

## Why

Silo re-encodes every piece of artwork when it caches it, and apps are served
renditions smaller than many screens need:

| | Upstream (ladder v3) | This image |
|---|---|---|
| WebP quality of every cached variant | 90 | **95** |
| Longest edge of the cached "original" | 3840 px | 3840 px (patch only raises it below 3000) |
| Poster sizes generated | 1000 · 500 · 300 | **1280** · 1000 · **780** · 500 · 300 |
| Episode-still sizes generated | 1280 · 500 · 300 | 1280 · **780** · 500 · 300 |
| Poster / still served for cards (`small`) | 300 px | **780 px** |
| Poster / still served for default and `medium` | 500 px | **780 px** |
| Poster served for `large` | 1000 px | **1280 px** |
| Backdrop served for landscape cards (`small`) | 300 px | **1280 px** |
| `artworkkey.LadderVersion` | N | N + 100 |

The patch only ever adds rungs to upstream's ladder. Upstream's backfill and
database fence check for upstream's own rungs, so dropping one would make the
backfill reopen on every newly cached image.

Silo picks a served size in three places, and all three are patched:

- the `image_size` contract (`internal/imagesize`), used by apps and Jellyfin-mode
  list cards that ask for a size
- the historical defaults for requests that name no size (`internal/catalog`),
  which the web UI and most endpoints use
- hard-coded card and featured paths (`internal/api/handlers`)

Effects:

- **Library grids and shelves** (web, apps, Infuse) show 780 px posters and title cards
  instead of 300 px, and 1280 px landscape cards instead of 300 px.
- **Detail pages and default-size requests** get 780 px instead of 500 px.
- **Apple TV** asks for "large" and gets 1280 px posters instead of 1000 px.
- Logos, headshots and banners keep their upstream sizes. Only the quality
  change applies to them.
- Upstream dropped the 780 px poster / still rung in ladder v3. The patch keeps
  it, so artwork cached before v3 already has the card size and it isn't
  garbage-collected.
- Raising `LadderVersion` makes Silo's one-shot ladder backfill generate the
  rungs that cached artwork is missing. Until then, requests for them fall back
  to the widest rung that artwork has.
- Clients that send explicit widths (`/api/v2` `poster_width` and the other
  slot widths, Jellyfin `MaxWidth`) are left to upstream. They pick the
  narrowest rung that covers the width, and the extra rungs make that pick
  tighter.

Grids download more data: a 780 px WebP poster is roughly 3–5× the bytes of a
300 px one. That's fine on a home network, but it's noticeable on slow remote
connections.

Artwork that is already cached keeps its quality-90 files.
Re-apply custom posters from their source to get the higher-quality encode.

## Use it

In the `.env` next to your Silo `docker-compose.yml`:

```
SILO_IMAGE=ghcr.io/tahuwa/silo-server:latest
```

Then update as usual:

```
docker compose pull silo
docker compose up -d --no-deps silo
```

Tags:

- `latest`: the newest patched upstream build
- `build-N`: the same upstream build number as the official image
- `<commit sha>`: the upstream source commit

Pin `build-N` or a commit to stay on a version. Switch `SILO_IMAGE` back to
`ghcr.io/silo-server/silo-server:latest` to return to the official image.

The image is `linux/amd64` only.

## How it works

- `.github/workflows/build.yml`
  1. Reads `org.opencontainers.image.revision` from upstream's `latest`.
  2. Stops if that commit already has a patched image here.
  3. Checks out the commit.
  4. Applies `patch/apply.py`.
  5. Runs Go tests that pin the patched behaviour in all three places.
  6. Builds the upstream `Dockerfile` and pushes the image.
- `patch/apply.py` matches each change by code structure and requires exactly one
  match. **If upstream moves or renames something, the build fails** rather than
  publishing an unpatched image. `latest` stays on the last good build, and GitHub
  emails you about the failed run.
- `scripts/registry.py` is a minimal GHCR client the workflow uses.

To rebuild by hand, go to **Actions → Build patched Silo image → Run workflow**
and tick *force* to rebuild a build that is already published. Any push that
changes the patch also rebuilds.

To try the patch locally:

```
git clone https://github.com/Silo-Server/silo-server src
python3 patch/apply.py src
```

## License

Silo Server is licensed under the GNU Affero General Public License v3.0, and
so is this modified build and the scripts in this repository (see `LICENSE`).
The complete corresponding source of any published image is the upstream commit
named in its `org.opencontainers.image.revision` label, plus this repository's
`patch/apply.py` at the commit in its `io.github.tahuwa.patch-commit` label.

This is an unofficial build, not affiliated with or supported by the Silo
project. Please report problems that also happen on the official image upstream.
