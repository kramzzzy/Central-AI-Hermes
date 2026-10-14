# Production Build Recipes & Deployment Architecture

Central AI is architected into decoupled independent layers (Boxes):

| Box | Compose File | Primary Services | Dockerfiles / Images | Purpose |
|---|---|---|---|---|
| **Box 1: Intelligence Stack** | `/compose.hermes.yaml` | `hermes`, `voice`, `whatsapp-connector`, `whatsapp-customer-connector`, `hindsight`, `laya`, `scrapling` | `deploy/hermes/Dockerfile`, `deploy/whatsapp/Dockerfile.caller`, `deploy/laya/Dockerfile`, `ghcr.io/vectorize-io/hindsight`, `scrapling` | Core LLM reasoning (:8642), vector memory (:8888), intent classification (:8001), WhatsApp telephony (:8085/:8086), and unified Jarvis voice pipeline (:8081/:8082) |
| **Box 2: Database Layer** | *External / Managed* | PostgreSQL / Supabase | Managed Supabase or PostgreSQL 15+ container | Persistent store for contacts, chats, profiles, and authentication (`DATABASE_URL`) |
| **Box 3: App OS Cockpit** | `/compose.app-os.yaml` | `web`, `worker` | `/Dockerfile` | Next.js UI (:3000), API endpoints, executive cockpit, and background task worker (voice proxied from Box 1) |

### 3-Way Installation Sequence

Follow the strict 3-way deployment process detailed in [docs/INSTALLATION_GUIDE.md](../docs/INSTALLATION_GUIDE.md):
1. **STEP 1**: Deploy **Box 1** via `/compose.hermes.yaml`. Once healthy, **turn OFF "Auto Deploy"** on Box 1 in Coolify (Box 1 is an immutable heavy AI runtime; this prevents 4-minute rebuilds when pushing frontend code).
2. **STEP 2**: Ensure **Box 2** (Supabase / PostgreSQL) is provisioned, accessible via `DATABASE_URL`, and keys are obtained.
3. **STEP 3**: Deploy **Box 3** via `/compose.app-os.yaml` (or run locally with `npm run dev`). Set `HERMES_BASE_URL=http://hermes:8642`. Box 3 will auto-run migrations and display system health.

### Existing Box 1 installations: preserve data before the storage fix

**Do not redeploy this storage change until the existing data is backed up and migrated. Keep Auto Deploy off.** The old Hindsight mount covered `/var/lib/hindsight`, but the pinned `0.10.2-slim` image stores its embedded PostgreSQL database in `/home/hindsight/.pg0`. A replacement container cannot recover data left in the old container's writable layer.

Operator checklist (performed by the owner in Coolify; agents must not access the server):

1. Record the current Compose project, container identities, volume names, and selected Hermes profile. Keep the same Coolify resource/project so existing named volumes are reused.
2. Export a verified Hindsight database backup while the old container still exists. Stop all writers, then shut down the old Hindsight container cleanly before taking a physical copy of its entire `/home/hindsight/.pg0` directory. Do not copy running PostgreSQL files as your only backup. Keep the old container and an independent backup until restoration is verified.
3. Stop Hermes/voice writers and back up the existing `hermes-profiles` and `phone-state` volumes plus the Hermes container's `/opt/os-adapter/.runtime` directory. This directory contains the installation binding, memory retention policies, and conversation/job runtime state. Preserve file permissions and ownership; Hermes runs as UID/GID 10000.
4. Before starting replacement services, populate the existing project's `hindsight-data` volume with the backed-up `.pg0` contents (not an extra nested `.pg0` directory), preserving PostgreSQL ownership. Populate the new `hermes-state` volume with the backed-up `.runtime` contents. Do not overwrite a nonempty volume without first backing it up and checking which copy is authoritative. Restore logical database backups into a compatible database using the pinned image if physical migration is unsuitable.
5. Deploy Box 1, then Box 3. Verify the selected assistant, old chat history, Hindsight banks/counts and a known memory, custom skills, and a newly assigned skill in team chat. Repeat the checks after a normal redeploy, keeping the same volume identities. Do not delete old containers/backups until both checks pass.

Fresh installations need no migration. Hermes histories (`profiles/<profile>/state.db`) and custom skills (`profiles/<profile>/skills`) remain in `hermes-profiles`; runtime bindings now live in `hermes-state`; Hindsight now mounts its actual database directory. Updating application images must never remove these volumes (`down -v`, volume pruning, or changing the Compose project can still lose access to data).

Local storage-contract self-check (requires Python and PyYAML; validates configuration only, not a backup or live restore):

```sh
python -c "import pathlib,yaml; configs=[yaml.safe_load(pathlib.Path(p).read_text()) for p in ['compose.hermes.yaml','compose.yaml']]; assert all('hindsight-data:/home/hindsight/.pg0' in c['services']['hindsight']['volumes'] and 'hermes-state:/opt/os-adapter/.runtime' in c['services']['hermes']['volumes'] and 'hermes-profiles:/opt/data' in c['services']['hermes']['volumes'] for c in configs); print('Storage mounts OK')"
```

New profiles receive a separate `michael-os-<profile>` bank. Existing bank mappings are intentionally preserved. A legacy profile mapped to another profile's bank requires administrator review and a deliberate, verified migration; do not blindly rename banks or copy mixed-user memories. This fix cannot recover data already lost with an earlier removed container unless a backup exists.

### Networking & Resolution
- **Internal Docker Network**: Box 1 and Box 3 join the shared `coolify` Docker network. Hermes resolves on hostname `hermes`.
- **Automatic Localhost Rescue**: Box 3 uses `lib/hermes-url.ts` to automatically detect Docker container runtimes and rewrite any accidental `localhost` or `127.0.0.1` to `http://hermes:8642`.
