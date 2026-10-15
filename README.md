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

| | Upstream | This image |
|---|---|---|
| WebP quality of every cached variant | 90 | **95** |
| Longest edge of the cached "original" | 1920 px | **3000 px** |
| Poster / episode-still sizes | 780 · 500 · 300 | **1280** · 780 · 500 · 300 |
| Poster / still served when an app asks for no size ("medium") | 500 px | **780 px** |
| `artworkkey.LadderVersion` | N | N + 100 |

Effects:

- **Apple TV** asks for "large" and now gets 1280 px posters and stills instead of 780.
- **iPhone/iPad/Mac/Android/web** use the default size and now get 780 px instead of 500.
- **Jellyfin-mode apps (Infuse, …)** map their pixel requests onto the same sizes.
  Requests of 780 px or wider now get 1280 instead of 780, and size-less or mid-size
  requests get 780 instead of 500.
- Backdrops, logos, headshots and banners keep their upstream sizes. Only the quality
  and original-size changes apply to them.
- Raising `LadderVersion` makes Silo's one-shot ladder backfill generate the new
  1280 px rung for artwork it has already cached. Until that finishes, apps fall back
  to the 780 px rung.

Artwork that is already cached keeps its old 1920 px-capped original. Re-apply
custom posters from their source to get the full benefit.

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
  5. Runs a Go test that pins the patched behaviour.
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
