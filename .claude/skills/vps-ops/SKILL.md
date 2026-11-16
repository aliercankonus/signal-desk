---
name: vps-ops
description: How to check and deploy signal-desk on the Hostinger VPS through the Hostinger API tools (status, metrics, Docker projects, logs, deploy, firewall). Use when asked to check the VPS, deploy, restart, or investigate the collector or API.
---

# VPS operations (Hostinger API)

There is no SSH from Claude sessions. Everything goes through the Hostinger tools: `search` to find an operation, `execute` / `multi-execute` to run it.

## Find the machine
`vps_virtual-machines_list` → take the `id` of the VPS. Don't hard-code the IP or ID in the repo.

## Health check (read-only, do this first)
1. `vps_virtual-machines_get` — state, plan.
2. `vps_virtual-machines_metrics` with a 24 h window — summarise CPU, RAM, disk, uptime as single numbers; don't paste the series.
3. `vps_docker_list` — projects, container state, health, published ports. Flag any port bound to `0.0.0.0` that isn't 22/80/443.
4. `vps_docker_logs` for `signal-desk` only when something looks wrong; grep for `HALT|RESYNCING|ERROR|Traceback`.

## Deploy
- CI publishes `ghcr.io/aliercankonus/signal-desk:main` after every merge to `main`. Confirm the publish job passed for the commit before deploying.
- Deploy or update with `vps_docker_create`, `project_name: signal-desk`, `content`: the raw URL of `docker-compose.yml` on `main` (`https://raw.githubusercontent.com/aliercankonus/signal-desk/main/docker-compose.yml`). Same name replaces the project; the `data` volume is kept.
- `vps_docker_create` does not pull a newer `:main` image; it reuses the cached one. After it, run `vps_docker_update` to pull and recreate, then check the new code is live (e.g. a new route or API field).
- `vps_docker_create` replaces the project's environment too. Read the current one first with `vps_docker_get` and pass it back as `environment`, or the overview page's HTTPS route (`UI_PUBLIC`, `UI_HOST`, `UI_BASICAUTH`) turns off. Never print or commit `UI_BASICAUTH`.
- `vps_docker_update` only pulls newer images into the compose file already on the VPS (`/docker/signal-desk/docker-compose.yml`). It does not apply changes to `docker-compose.yml` (healthchecks, env, volumes, services). If the merge touched the compose file, use `vps_docker_create` as above.
- After deploy: `vps_docker_list` until `api` and `collector` are both healthy (the collector's healthcheck has a 120 s start period), then check logs once.

## Collector rules
- The collector exits with code 3 on HALT (Binance ban/rate limit, raw quota, disk threshold). Do not restart it in a loop. Read the logs, report the reason, and ask the owner.
- Stop with `vps_docker_stop`. Never `vps_docker_delete` the project or volumes without explicit owner approval: it deletes collected data.

## Firewall
Intended state: inbound TCP 22, 80, 443 only. Use `vps_firewall_*` operations; after rule changes, sync the firewall. Confirm with the owner before activating a firewall on the VM (it could cut off access they rely on).

## Never
- Recreate/reinstall the VM, delete snapshots/backups, or change root passwords.
- Expose the API port publicly; it binds to 127.0.0.1 by design. The only public path is the overview page through Traefik with basic auth (owner-approved 2026-11-16), and only the pages (`/`, `/sessions`) and their `/api/` data are routed.
