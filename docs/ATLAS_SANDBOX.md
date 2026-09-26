# MongoDB Atlas Hackathon Sandbox

LEVY is connected to the MongoDB Atlas sandbox required for the hackathon.
No credentials are stored in this repository.

## Verified sandbox

- Atlas project ID: `6ab805db7d3d0c27d96990fb`
- Cluster host: `cluster0.ezqhvk.mongodb.net`
- Database: `levy`
- MongoDB server: 8.0.32 at initial verification
- Collections: 20/20 present
- Standard indexes: 48, including 3 TTL indexes
- Atlas Vector Search indexes: `evidence_summary_idx`, `lessons_text_idx`
- Seed data: 20 questions, 22 reference events, 4 exposure records, 3 market ticks

The seed command is idempotent:

```bash
source .venv/bin/activate
levy-seed
```

## Local configuration

Copy `.env.example` to `.env`, set `LEVY_OFFLINE=false`, and provide the Atlas
SRV URI through `LEVY_MONGO_URI`. Keep `.env` permissioned to the current user:

```bash
chmod 600 .env
```

Never commit `.env`, database passwords, API keys, or full connection URIs.

## Validation

Run local tests without contacting Atlas:

```bash
LEVY_OFFLINE=true .venv/bin/python -m pytest
```

Run the API against Atlas:

```bash
source .venv/bin/activate
levy-api
```

Then check `GET /health`, `GET /api/board`, and `GET /api/stats`.

## Before deployment or judging

1. Set the Atlas URI and provider keys in the hosting platform's encrypted
   environment variables, never in source control.
2. Restrict the Atlas IP access list to the deployment provider's egress
   addresses; remove temporary developer addresses when no longer needed.
3. Use separate least-privilege database users for API and workers where time
   permits. The API should be read-only except for confirmed resolutions and
   explicitly enabled demo controls.
4. Rotate any credential that was ever pasted into chat, logs, shell history, or
   screenshots before the final demo.
5. Enable backups if the sandbox tier supports them.
6. Deploy the API and worker processes, then test persistence and SSE from the
   deployed UI origin. Update `LEVY_CORS_ORIGINS` accordingly.
7. Merge the reviewed `kiro/levy-backend` branch into the public repository's
   default branch before submission, or ensure judges receive the branch URL.

## Known production follow-ups

The current API and database paths are Atlas-backed. Live scheduled source
collectors and cross-process Atlas change-stream fan-out remain separate
implementation tasks; the existing scout scheduler currently provides roster
and cadence wiring while demo injection and replay exercise the full pipeline.
