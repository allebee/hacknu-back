# AI Brainstorm Canvas — Architecture

## Project Overview
**Hackathon**: HackNU 2026
**Goal**: AI brainstorming agent that lives inside a collaborative canvas as a spatial participant.
**Stack**: FastAPI + PostgreSQL + Liveblocks + tldraw 3.10

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Frontend (React + tldraw)                        │
│                                                                         │
│   Canvas UI ──► useStorage / useMutation ──► Liveblocks Cloud (WebSocket)│
│   Chatbot Sidebar ──► Backend API                                       │
└────────────────────┬───────────────────────────────────────┬────────────┘
                     │                                       │
                     ▼                                       ▼
┌─────────────────────────────┐        ┌──────────────────────────────────┐
│     Liveblocks Cloud        │        │      Backend (FastAPI)            │
│                             │◄──────►│                                  │
│  Storage:                   │ REST   │  GET  storage (read canvas)      │
│    shapes (LiveMap)         │ API    │  PATCH storage (write changes)   │
│    pendingChanges (LiveMap) │        │  POST presence (agent status)    │
│    agents (LiveMap)         │        │                                  │
│    meta (LiveObject)        │        │  PostgreSQL:                     │
│                             │        │    agents, agent_changes,        │
│  Presence:                  │        │    chat_messages                 │
│    agent status (ephemeral) │        │                                  │
└─────────────────────────────┘        └──────────────────────────────────┘
```

**Key principle**: Frontend writes user changes directly to Liveblocks. Backend reads/writes via Liveblocks REST API. Liveblocks Storage is the single source of truth for canvas state.

---

## Tech Stack

| Layer | Tech |
|-------|------|
| Frontend | Vite + React + TypeScript + tldraw 3.10 |
| Realtime sync | Liveblocks (storage + presence) |
| Backend | FastAPI (Python 3.11) |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Database | PostgreSQL 16 |
| LLM | OpenAI / Gemini (provider-swappable) |
| Package manager | uv |
| Containerization | Docker Compose |

---

## Features

### 1. Autocomplete (Ghost Shapes)
- After user is idle for N seconds, frontend calls `POST /complete`
- Backend reads canvas state, generates shape suggestions via LLM
- Writes suggestions to `pendingChanges` in Liveblocks
- Frontend renders them as semi-transparent ghost overlays
- User can **Approve**, **Reject**, or **Edit** (opens chatbot sidebar)

### 2. Chatbot Sidebar
- User creates chatbot agents per room via `POST /agents/{room_id}`
- Each chatbot maintains its own conversation history (stored in PostgreSQL)
- User sends prompts → `POST /agent/{agent_id}/run`
- Agent can **generate shapes** (written to `pendingChanges`) or **answer questions** about the canvas
- Multiple chatbots can have pending changes simultaneously — each independently approvable

### 3. Approve / Reject / Edit Flow
- `POST /complete/action` with `action: "approve" | "reject" | "edit"`
- **Approve**: moves shapes from `pendingChanges` to `shapes` via JSON Patch
- **Reject**: removes from `pendingChanges`, saves to DB so agent won't re-suggest
- **Edit**: removes old suggestion, re-runs agent with user's edit prompt

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/complete` | Autocomplete — generates ghost shapes after idle |
| `POST` | `/complete/action` | Approve / reject / edit a pending change |
| `GET` | `/agents/{room_id}` | List agents in a room |
| `POST` | `/agents/{room_id}` | Create a new chatbot agent |
| `POST` | `/agent/{agent_id}/run` | Run chatbot (generate or query) |
| `GET` | `/agent/{agent_id}/messages` | Chat history timeline |
| `GET` | `/health` | Health check |

Swagger docs: `http://localhost:8000/docs`

---

## Liveblocks Storage Schema

```
Storage Root:
├── shapes         (LiveMap)  — committed shapes on canvas
├── pendingChanges (LiveMap)  — agent suggestions awaiting approval
├── agents         (LiveMap)  — registered agents in this room
└── meta           (LiveObject) — room metadata
```

All shape values follow strict tldraw v3.10 schemas. See `FRONTEND_STORAGE_GUIDE.md` for full TypeScript contracts.

**Supported shape types**: `geo`, `arrow`, `note`, `text`, `frame`, `line`, `draw`, `group`

---

## Database Schema

Three tables in PostgreSQL:

**`agents`** — agent registry
| Column | Type | Description |
|--------|------|-------------|
| id | VARCHAR(255) PK | e.g. `agent_0_room1` |
| room_id | VARCHAR(255) | Liveblocks room ID |
| name | VARCHAR(255) | Display name |
| type | VARCHAR(50) | `autocomplete` or `chatbot` |
| is_default | BOOLEAN | True for the autocomplete agent |

**`agent_changes`** — all agent-generated changes (pending + resolved)
| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | |
| room_id | VARCHAR(255) | |
| agent_id | VARCHAR(255) | Which agent created |
| status | VARCHAR(20) | `pending`, `approved`, `rejected` |
| operations | JSONB | List of shape operations |
| reasoning | TEXT | LLM's explanation |
| user_feedback | TEXT | User's edit/rejection reason |

**`chat_messages`** — unified chat timeline per agent
| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | |
| agent_id | VARCHAR(255) | Globally unique — no room_id needed |
| type | VARCHAR(20) | `text` (message) or `change` (inline change card) |
| role | VARCHAR(20) | `user` or `assistant` (for type=text) |
| content | TEXT | Message text (for type=text) |
| change_id | VARCHAR(255) | Links to agent_changes (for type=change) |
| change_status | VARCHAR(20) | `pending`/`approved`/`rejected` |
| operations_summary | TEXT | e.g. "Added 3 shapes: rectangle, arrow, note" |

---

## Backend File Structure

```
hacknu-back/
├── Dockerfile
├── requirements.txt
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py
└── app/
    ├── __init__.py
    ├── main.py          # FastAPI app, CORS, lifespan, router mounting
    ├── config.py         # Env var config (DB, Liveblocks, LLM keys)
    ├── database.py       # Async SQLAlchemy engine + session dependency
    ├── models.py         # ORM models (Agent, AgentChange, ChatMessage)
    ├── schemas.py        # API request/response Pydantic models
    ├── shapes.py         # Strict tldraw shape schemas (8 types + enums)
    ├── liveblocks.py     # Liveblocks REST client (storage, presence, rooms)
    ├── planner.py        # LLM planner (generate ops, answer queries)
    └── routes.py         # All 6 endpoint handlers
```

---

## Running

```bash
# Start everything
docker compose up -d --build

# Backend at http://localhost:8000
# Swagger at http://localhost:8000/docs

# Logs
docker compose logs backend -f

# Stop
docker compose down
```

---

## Env Variables

```env
# LLM
OPENAI_API_KEY=sk-...
GEMINI_API=AIza...
AGENT_PROVIDER=openai        # "openai" or "gemini"
AGENT_MODEL=gpt-4o

# Liveblocks
LIVEBLOCKS_SECRET_KEY=sk_prod_...

# Database (set automatically by Docker Compose)
DATABASE_URL=postgresql+asyncpg://hacknu:hacknu@db:5432/hacknu
```

---

## Multi-Agent Pending Changes

Multiple chatbot agents can generate suggestions simultaneously. Each `PendingChange` in Liveblocks storage is tagged with `agentId`. Frontend filters by agent to show per-agent approve/reject buttons. Approving one agent's changes does not affect any other agent's pending changes.

```
pendingChanges LiveMap:
  chg_A01 → { agentId: "agent_0_room1", operations: [...] }    ← autocomplete
  chg_B01 → { agentId: "agent_abc_room1", operations: [...] }  ← chatbot
  // Each independently approvable/rejectable
```

---

## Chat Timeline

Chat history is a unified ordered timeline with two entry types:

```
[user]   "Add a decision tree for onboarding"         → type=text
[agent]  "Creating a decision tree with 5 nodes"      → type=text
[change] ✨ Added 5 shapes (pending)  [✓] [✗]         → type=change
[user]   "Make the colors more vibrant"                → type=text
[agent]  "Updating colors to blue and green"           → type=text
[change] 🎨 Updated 5 shapes (pending) [✓] [✗]        → type=change
```

Retrieved via `GET /agent/{agent_id}/messages?limit=50&offset=0`
