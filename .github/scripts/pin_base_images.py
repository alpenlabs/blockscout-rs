#!/usr/bin/env python3
"""Rewrite every external base image in a Dockerfile to its pinned digest.

Usage: pin_base_images.py <Dockerfile> <pins-file> [named-build-context ...]

The Dockerfiles in this repo use mutable tags (services-base:latest,
ubuntu:24.04). This replaces each `FROM image:tag` with
`FROM image:tag@sha256:...` from the pins file, so the same commit always
builds on the same images. It fails, and nothing is built, if the Dockerfile
uses an image that has no pin, so a base image can never slip in unpinned.

The Dockerfile is edited in place: run it on the throwaway build checkout only.
"""

import re
import sys
from pathlib import Path

FROM_RE = re.compile(r"^(\s*FROM\s+)((?:--\S+\s+)*)(\S+)(.*)$", re.IGNORECASE)
AS_RE = re.compile(r"\s+AS\s+(\S+)", re.IGNORECASE)
COPY_FROM_RE = re.compile(r"--from=(\S+)", re.IGNORECASE)


def load_pins(path):
    pins = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        image, digest = line.split()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            sys.exit(f"::error::{path}: bad digest for {image}")
        pins[image] = digest
    return pins


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    dockerfile, pins_file, contexts = Path(sys.argv[1]), sys.argv[2], set(sys.argv[3:])
    pins = load_pins(pins_file)

    stages = set()
    errors = []
    out = []
    for number, line in enumerate(dockerfile.read_text().splitlines(), start=1):
        for source in COPY_FROM_RE.findall(line):
            if source not in stages and source not in contexts and not source.isdigit():
                errors.append(f"line {number}: --from={source} is not a build stage or a known build context")

        match = FROM_RE.match(line)
        if not match:
            out.append(line)
            continue

        prefix, flags, image, rest = match.groups()
        alias = AS_RE.search(rest)
        if image in stages or image.lower() == "scratch":
            out.append(line)
        elif "$" in image:
            errors.append(f"line {number}: base image {image} uses a build argument and cannot be pinned")
            out.append(line)
        elif "@sha256:" in image:
            out.append(line)
        elif image in pins:
            out.append(f"{prefix}{flags}{image}@{pins[image]}{rest}")
            print(f"{image} -> {pins[image]}")
        else:
            errors.append(f"line {number}: base image {image} has no pinned digest in {pins_file}")
            out.append(line)
        if alias:
            stages.add(alias.group(1))

    if errors:
        for error in errors:
            print(f"::error::{dockerfile}: {error}", file=sys.stderr)
        sys.exit(1)

    dockerfile.write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
