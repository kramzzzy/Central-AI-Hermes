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

### Networking & Resolution
- **Internal Docker Network**: Box 1 and Box 3 join the shared `coolify` Docker network. Hermes resolves on hostname `hermes`.
- **Automatic Localhost Rescue**: Box 3 uses `lib/hermes-url.ts` to automatically detect Docker container runtimes and rewrite any accidental `localhost` or `127.0.0.1` to `http://hermes:8642`.
