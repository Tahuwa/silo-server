#!/usr/bin/env python3
"""Tiny GHCR client for the build workflow (stdlib only).

  registry.py labels ghcr.io/owner/name:tag   -> prints the linux/amd64 image labels as JSON
  registry.py exists ghcr.io/owner/name:tag   -> exit 0 if the tag exists, 1 if not

Credentials come from GHCR_USER / GHCR_TOKEN when set (needed for private
packages); otherwise requests are anonymous, which works for public images.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


def parse(ref: str) -> tuple[str, str]:
    host, _, rest = ref.partition("/")
    if host != "ghcr.io" or ":" not in rest:
        raise SystemExit(f"expected ghcr.io/<owner>/<name>:<tag>, got {ref!r}")
    repo, _, tag = rest.rpartition(":")
    return repo.lower(), tag


def token(repo: str) -> str:
    req = urllib.request.Request(f"https://ghcr.io/token?scope=repository:{repo}:pull")
    user, secret = os.environ.get("GHCR_USER"), os.environ.get("GHCR_TOKEN")
    if user and secret:
        basic = base64.b64encode(f"{user}:{secret}".encode()).decode()
        req.add_header("Authorization", f"Basic {basic}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["token"]


def get(repo: str, path: str, tok: str, *, head: bool = False):
    req = urllib.request.Request(
        f"https://ghcr.io/v2/{repo}/{path}",
        method="HEAD" if head else "GET",
        headers={"Authorization": f"Bearer {tok}", "Accept": ACCEPT},
    )
    return urllib.request.urlopen(req, timeout=60)


def labels(ref: str) -> dict:
    repo, tag = parse(ref)
    tok = token(repo)
    with get(repo, f"manifests/{tag}", tok) as resp:
        manifest = json.load(resp)
    if "manifests" in manifest:  # multi-platform index
        amd64 = [m for m in manifest["manifests"]
                 if m.get("platform", {}).get("os") == "linux"
                 and m.get("platform", {}).get("architecture") == "amd64"]
        if not amd64:
            raise SystemExit(f"{ref} has no linux/amd64 image")
        with get(repo, f"manifests/{amd64[0]['digest']}", tok) as resp:
            manifest = json.load(resp)
    with get(repo, f"blobs/{manifest['config']['digest']}", tok) as resp:
        config = json.load(resp)
    return (config.get("config") or {}).get("Labels") or {}


def exists(ref: str) -> bool:
    repo, tag = parse(ref)
    try:
        tok = token(repo)
        with get(repo, f"manifests/{tag}", tok, head=True):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):  # no package yet / no such tag
            return False
        raise


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("labels", "exists"):
        print(__doc__)
        return 2
    cmd, ref = sys.argv[1], sys.argv[2]
    if cmd == "labels":
        print(json.dumps(labels(ref)))
        return 0
    return 0 if exists(ref) else 1


if __name__ == "__main__":
    sys.exit(main())
