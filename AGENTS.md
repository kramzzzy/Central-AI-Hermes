## Mandatory Agent Communication Policy: Minimal, Concise & Precise

- **Zero Fluff & No Walls of Text**: Never output lengthy prose, redundant greetings, boilerplate recaps, or verbose walk-throughs.
- **Minimal & Essential Only**: Deliver ONLY the minimal and precise facts the user needs to know.
- **Punchy Bullet Format**: Summarize changes, status, and verification in short, high-density bullet points.
- **Direct Next Action**: Conclude with a clear 1-2 sentence status and immediate next step.

## graphify Knowledge Graph & Architecture Navigation

This project has an AST-level knowledge graph at `graphify-out/` with god nodes, community clusters, and cross-file call hierarchies.

**MANDATORY: Always use `graphify` before searching or reading raw code files.**

- **Codebase Questions & Discovery**: Run `graphify query "<question>"` to return a focused, scoped subgraph of relevant functions, routes, and call chains.
- **Trace Relationships**: Run `graphify path "<Caller>" "<Callee>"` to trace execution paths across files.
- **Inspect Specific Symbols**: Run `graphify explain "<concept/function>"` for focused architecture deep-dives.
- **Keep Graph Current**: After modifying any code files in the repository, immediately run:
  ```bash
  graphify update .
  ```
  _(AST-only extraction; runs locally in seconds with zero LLM API cost)._

## Hindsight Persistent Agent Memory

Central AI integrates with Hindsight (`:8888`) as Box 1's long-term memory engine.

- Use Hindsight MCP tools (`hindsight_sync_status`, `hindsight_search_knowledge_pages`, `hindsight_reflect`, `hindsight_capture_initiative`) to store and retrieve architectural decisions, user preferences, and operational initiatives.
- Do not repeat redundant codebase deep-dives when memory pages and graphify subgraphs already exist.

## Assistant Identity & Voice Call Architecture

1. **Zero Hardcoded Assistant Names**:
   - Never hardcode "Leo", "Sarah", or any static name in UI, voice hooks, or tools.
   - Assistant identity is loaded dynamically from Box 2 database (`agent_profiles WHERE is_main = true`).
   - Voice intent matchers (e.g. `isGoodbyeIntent`) dynamically adapt regex patterns to the assistant's runtime name.
   - When the user says _"Goodbye [AssistantName]"_, _"Bye [AssistantName]"_, or _"End call"_, the voice engine terminates the active call session gracefully.

2. **Screen Widget Full Orchestration**:
   - The assistant fully controls App OS screen widgets (`weather`, `website`, `report`, `contacts`, `tasks`, etc.) via `central_ai_control_widget`.
   - Supported actions: `open`, `close`, `close_all`, and `navigate` (for web URLs).
   - **Live Weather Ground Truth**: The assistant shares the exact meteorological and solar yield data with the weather widget via `lib/weather-data.ts` and `central_ai_get_live_weather`. It answers weather questions in < 5ms without external web scraping.

3. **Multi-Channel WhatsApp Texting & Outbound Calling**:
   - **Voice-Initiated WhatsApp Texting**: Users can tell the assistant via voice (_"Text Mark that the report is ready"_ or _"Send a WhatsApp to Michael..."_). The assistant resolves the contact via directory and executes `send_whatsapp_message` / `central_ai_send_whatsapp_message` via Meowcaller (`:8080/send`).
   - **Voice-Initiated Outbound Calling**: The assistant places WhatsApp calls on command via `call_whatsapp_contact` / `central_ai_call_whatsapp_contact`.
   - **Dynamic Linked Line Detection**: The assistant detects its own linked WhatsApp number dynamically via `socket.Store.ID.User` -> Meowcaller `/status` (`linked_number`). This displays the real connected number in App OS and prevents self-calling errors.

## Mandatory Box 1 Dual-Push Synchronization (`Central-AI-Hermes`)

- **Dedicated Box 1 Repository**:
  - The standalone Box 1 deployment repository is located at `C:\AI Development\Central-AI-Hermes` (Remote: `https://github.com/kramzzzy/Central-AI-Hermes.git`).
- **Mandatory Dual-Push on Box 1 Changes**:
  - Whenever modifying, fixing, or adding code/config files that belong to Box 1:
    - `scripts/whatsapp-caller/` (Meowcaller Go binary files)
    - `scripts/local-bridge.py` and other backend scripts (`scripts/*.py`)
    - `deploy/hermes/` and `deploy/whatsapp/` (Dockerfiles, patches)
    - `compose.hermes.yaml` / `.env.hermes.example`
    - `hermes_plugins/` and Box 1 skills (`skills/personal-assistant/`)
  - The agent **MUST ALWAYS**:
    1. Update the files in `Central-AI-OS` (`Your-AI-Agent-Clean`).
    2. Synchronize the modified files directly to `C:\AI Development\Central-AI-Hermes`.
    3. Commit and push the updates to `origin main` in `Central-AI-Hermes`:
       ```bash
       git -C "c:\AI Development\Central-AI-Hermes" add <files>
       git -C "c:\AI Development\Central-AI-Hermes" commit -m "<message>"
       git -C "c:\AI Development\Central-AI-Hermes" push origin main
       ```
    4. Ensure both repositories are pushed and kept completely in sync.

## Strict Prohibition: No Remote / Live Server Access

- **NEVER connect to, inspect, ping, curl, SSH into, or check the live server / remote VPS under any circumstances.**
- All development, debugging, testing, and fixing must be 100% local.
- The user exclusively handles all deployments via Coolify.

## Supabase & Database Layer Best Practices (RLS & Schema Security)

- **Zero Unrestricted Tables**: Every table created in the `public` schema **MUST** have Row Level Security enabled (`ALTER TABLE public.<table_name> ENABLE ROW LEVEL SECURITY;`).
- Supabase flags tables without RLS with a red `UNRESTRICTED` badge because PostgREST exposes all `public` tables to the public internet via the REST API.
- Do NOT use `FORCE ROW LEVEL SECURITY`. Table owners and pooler connections (`DATABASE_URL` via postgres user) have `BYPASSRLS` privileges by default.
- Wrap auth helper functions in subqueries: `(SELECT auth.uid())` instead of `auth.uid()`.
## Strict Global Design Pattern & UI/UX Policy

All agents modifying or creating UI components, pages, widgets, settings, modals, or onboarding flows **MUST ALWAYS** follow our existing design patterns and enforce the global design system without exception:

1. **Mandatory Global Token Usage (`app/globals.css`)**:
   - **Never hardcode arbitrary hex colors** (e.g., generic Tailwind blue `#3b82f6`, violet `#8b5cf6`, or random greys).
   - **Surfaces & Canvas**: Always use `var(--bg)` (`#111314`), `var(--surface)` (`#191c1d`), `var(--surface-raised)` (`#202425`), and `var(--surface-overlay)`.
   - **Borders**: Always use `var(--border)` (`#2b3031`), `var(--border-subtle)` (`#242829`), and `var(--border-hover)` (`#3a4242`).
   - **Brand Accent**: Always use Central AI's signature pale sage/olive accent `var(--accent)` (`#c6dab9`), `var(--accent-hover)` (`#d4e5ca`), `var(--accent-text)` (`#273023`), and `var(--accent-subtle)` (`rgba(198, 218, 185, 0.12)`).
   - **Primary Action Buttons**: Must use `background: var(--accent); color: var(--accent-text); font-weight: 600;`.
   - **Secondary Action Buttons**: Must use `background: var(--btn-secondary-bg); border: 1px solid var(--btn-secondary-border); color: var(--text);`.
   - **Semantic Indicators**: Use `var(--success)` / `var(--success-bg)` for active/healthy, `var(--warning)` / `var(--warning-bg)` for alerts, `var(--danger)` / `var(--danger-bg)` for errors/destructive actions.

2. **Typography & Hierarchy**:
   - **Body & Headings**: Always use `var(--font-sans)` (`"IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`).
   - **Metadata, Counters, Status, and Code**: Always use `var(--font-mono)` (`"IBM Plex Mono", ui-monospace, monospace`).
   - **Labels & Tags**: Use uppercase tracking (`letter-spacing: 1px` to `1.5px`) with `var(--text-muted)` / `var(--text-subtle)`.

3. **Component Architecture & Consistency**:
   - Re-use standard structural elements: `.panel`, `.section-heading`, `.eyebrow`, `.status-dot`, and `.orbit-mark`.
   - Maintain the refined, calm, high-precision dark business cockpit aesthetic.
   - Use `@phosphor-icons/react` for iconography with consistent weight (`duotone` or `regular`).
   - Any new screen (e.g. `/init`, settings, wizard) must visually integrate seamlessly with the rest of the OS. Never build isolated pages with disconnected aesthetic styles.

4. **Mandatory Container Action Button Bottom Alignment**:
   - In all multi-column card grids, forms, settings panels, or side-by-side modules:
     - **Always anchor action buttons to the bottom of the container** (`margin-top: auto;`).
     - **Equal-Height Layout Symmetry**: Adjacent cards in the same grid/flex row (e.g. Profile vs Account & Access Settings, forms, panels) MUST have their primary action buttons aligned symmetrically along the exact same horizontal bottom baseline, never floating awkwardly midway up the container.
     - **Standard Implementation Pattern**:
       - Card/panel container uses `display: flex; flex-direction: column; height: 100%;`
       - Form or card body uses `display: flex; flex-direction: column; flex: 1;`
       - Action buttons footer/bar uses `margin-top: auto; display: flex; align-items: center; justify-content: flex-end; gap: 12px; padding-top: 14px; border-top: 1px solid var(--border-subtle);`

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
