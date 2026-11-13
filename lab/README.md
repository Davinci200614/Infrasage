# lab/ — target stack for the digital twin

The lab is the Docker Example Voting App, pinned in `target.lock` and vendored
here. It is the system under test; the twin injects faults into it and the
InfraSage collectors observe it.

## Vendor decision

Vendored, not cloned at run time. The upstream repo publishes no tags, so the
pin is a commit hash (`target.lock`). `compose.yaml` is a byte-identical copy of
the upstream `docker-compose.yml` at that commit; the service sources it builds
(`vote/`, `result/`, `worker/`, `seed-data/`) and `healthchecks/` are copied
alongside it so the relative build contexts keep working. Upstream is not
modified — all changes live in `docker-compose.infrasage.yml`.

## Run

```sh
# once, then Docker layer cache makes later ups fast
docker compose -p infrasage-lab -f compose.yaml -f docker-compose.infrasage.yml build

docker compose -p infrasage-lab -f compose.yaml -f docker-compose.infrasage.yml up -d
docker compose -p infrasage-lab -f compose.yaml -f docker-compose.infrasage.yml down -v
```

Always pass `-p infrasage-lab` (the fault injector and teardown checks rely on
that project label) and always tear down with `-v`.

## Host setup (done on this device)

- Docker Engine 29.9.3, Compose v5.7.1, service enabled at boot.
- The user must be in the `docker` group. Existing shells (including opencode)
  pick that up only after a restart; use `sg docker -c '<command>'` meanwhile.
- **Docker storage is capped at 40 GiB**: `/var/lib/docker.img` is an ext4
  loopback mounted at `/var/lib/docker` (`/etc/fstab`, `nofail`), so image
  builds, containers, volumes and build cache all share that ceiling. The loop
  file lives on the btrfs root, which Docker's overlayfs cannot quota directly.
  Images and build cache are stored by containerd, so its root is moved inside
  the cap: `root = "/var/lib/docker/containerd"` in `/etc/containerd/config.toml`.
  Check with `docker system df`; if it fills, `docker builder prune` first.

## Ports (loopback only)

| Service | Host |
|---|---|
| vote | 127.0.0.1:8080 |
| result | 127.0.0.1:8081 |
| redis | 127.0.0.1:6379 |
| db | 127.0.0.1:5432 |

## Constraints worth knowing before writing twin code

- `docker-compose.infrasage.yml` needs Compose >= 2.24 for `!override`; the base
  file publishes on `0.0.0.0`, so ports are replaced, not appended.
- `worker/Program.cs` hardcodes `Server=db` and `redis`. The
  `connectivity_or_endpoint` fault from the guide cannot be injected through
  environment variables — it needs a DNS/`/etc/hosts` or network-level approach.
- `worker` has no healthcheck: the image has no probe tools. Steady state is
  proven by the end-to-end vote -> result check (guide §5.3), not by per-service
  health.
- `worker` runs with `restart: unless-stopped` (override). Stopping `redis` or
  `db` makes the worker exit(1), so without this a fault would kill it
  permanently. A manual `docker stop worker` still stays stopped.
- `seed` is a one-shot profile (`--profile seed`); it is not part of the five
  services in the baseline.
