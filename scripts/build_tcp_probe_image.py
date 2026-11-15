#!/usr/bin/env python3
"""Build the controlled local helper from a two-file context, never the whole repo."""
import io
import json
from pathlib import Path
import sys
import tarfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import docker
from backend.collectors.tcp_probe import helper_source_sha


def main():
    root = Path(__file__).resolve().parents[1]
    context = io.BytesIO()
    with tarfile.open(fileobj=context, mode='w') as archive:
        for source, name in [(root/'lab/tcp-probe/Dockerfile', 'Dockerfile'),
                             (root/'backend/collectors/tcp_probe_helper.py', 'backend/collectors/tcp_probe_helper.py')]:
            data = source.read_bytes()
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mode = 0o644
            archive.addfile(entry, io.BytesIO(data))
    context.seek(0)
    client = docker.from_env(timeout=120)
    try:
        image, _ = client.images.build(fileobj=context, custom_context=True,
                                      tag='infrasage-tcp-probe:phase2b1',
                                      buildargs={'PROBE_SHA': helper_source_sha()}, rm=True)
        print(json.dumps({'helper_image': image.id, 'helper_source_sha256': helper_source_sha()}))
    finally:
        client.close()


if __name__ == '__main__':
    main()
