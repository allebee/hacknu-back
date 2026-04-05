# Backend Deploy

This repo uses:

- root `docker-compose.yml` for local development
- `infra/docker-compose.yml` for server deployment

The deploy script reads the repo-root `.env`, keeps SSH settings local, syncs the backend source to the server, and starts the backend stack there.

## First run

Create the repo-root `.env`:

```bash
cp .env.example .env
```

Fill in:

- app secrets: `OPENAI_API_KEY`, `GEMINI_API`, `LIVEBLOCKS_SECRET_KEY`, `HIGGSFIELD_API_KEY_ID`, `HIGGSFIELD_API_KEY_SECRET`
- optional app settings: `AGENT_PROVIDER`, `AGENT_MODEL`, `AI_DEBUG_PRINTS`
- deploy settings: `DO_HOST`, `DO_USER`, `SSH_KEY`, `SSH_PUBLIC_KEY`, `REMOTE_DIR`, `APP_PORT`

Do not commit `.env`.

`SSH_KEY` must point to the private key file used by `ssh`.
`SSH_PUBLIC_KEY` is only a reference to the public key that should be installed on the server.

## Deploy

From the repo root:

```bash
bash infra/deploy.sh
```

Default deploy target:

- host port: `9000`
- remote directory: `/root/hacknu-back`

Useful server commands:

```bash
docker compose -f /root/hacknu-back/infra/docker-compose.yml logs -n 200 -f backend
docker compose -f /root/hacknu-back/infra/docker-compose.yml restart backend
docker ps
```

After a successful deploy:

```text
http://<DO_HOST>:<APP_PORT>/health
http://<DO_HOST>:<APP_PORT>/docs
```
