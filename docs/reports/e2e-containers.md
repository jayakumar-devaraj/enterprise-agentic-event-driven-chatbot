# Functional verification — full stack in containers (Task 12, rung 3)

Date: 2026-10-17 18:28 CDT, `main` at `2a68f7f` (Task 12 images, PR #20; resource caps, PR #19).

```
export MSYS_NO_PATHCONV=1   # Windows Git Bash only: keeps the scrypt hash intact
export AUTH_USERS="$(uv run python -m gateway.auth demo demo-pw)"
docker compose -f deploy/compose.yaml up -d --wait
```

Observed:
- `seed` exited 0 and `kafka-init` exited 0; `gateway` healthy, `worker` running; both run as
  user `app` (`id -un`).
- Gateway published on `127.0.0.1:8001` (`docker compose port gateway 8000`; Compose assigns from the
  8000–8003 range); `GET /healthz` → `{"status":"ok"} -> 200`.
- Through the frontend container's proxy (`http://127.0.0.1:5173/api`):
```
POST http://127.0.0.1:5173/api -> 202
data: {"type":"token","seq":0,"token":"[mock]"} data: {"type":"token","seq":1,"token":"intent=billing"} data: {"type":"token","seq":2,"token":"turns=1"} data: {"type":"token","seq":3,"token":"last=-"} data: {"type":"done","seq":4}
```
- First attempt (17:2x) returned 401: Git Bash rewrote the `/` segments of the exported hash into
  Windows paths before `docker.exe` saw them (container value length 145 vs 125 on the host).
  With `MSYS_NO_PATHCONV=1` the container value is 125 characters and auth succeeds.
