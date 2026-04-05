# HackNU Back

Backend and integration layer for our Hackathon solution to the **AI Brainstorm Canvas** case.

The core idea was simple: AI should not live in a separate tab. It should act inside the same canvas as the team, see the current board state, use meeting context, and propose spatial edits that users can approve, reject, or refine.

Current frontend deployment: <https://hacknu.167.99.251.164.nip.io>  
Frontend source: <https://github.com/bekzhanak/hacknu-front>

## What This Repo Contains

This repository is the backend side of the system plus the Google Meet transcript extension:

- `hacknu-back/`: FastAPI service, planner, storage integration, DB models, migrations
- `meet-extension/`: Chrome extension that captures Google Meet captions and sends them to the backend
- `infra/`: deployment compose file and deploy script
- `docker-compose.yml`: local backend + Postgres stack

The browser canvas itself lives in the separate frontend repo. The full product is the combination of:

1. React + tldraw + Liveblocks frontend
2. This FastAPI backend
3. PostgreSQL
4. Google Meet caption ingestion
5. Optional Higgsfield media generation

## Hackathon Framing

The brief asked for an **AI brainstorming agent that lives inside a canvas**, not a chatbot with a whiteboard attached in the background.

Our answer was to make AI behave like a **spatial collaborator**:

- it reads the current canvas from Liveblocks
- it reads meeting context from captured transcripts
- it proposes actual canvas operations, not just text
- it appears on the board through pending changes
- humans stay in control through approve, reject, and edit actions

This keeps the agent present in the session without requiring fake real-time cursor movement.

## High-Level Architecture

```text
Users in browser
  |
  v
React + tldraw frontend
  |- writes user canvas changes directly to Liveblocks
  |- calls backend for autocomplete, chat-agent runs, approvals, transcript-backed queries
  |- renders pending AI changes as ghost/spatial suggestions
  |
  +-----------------------> Liveblocks Cloud
  |                           |- canonical shared canvas state
  |                           |- pendingChanges
  |                           |- agent registry / room metadata
  |                           |- presence
  |
  +-----------------------> FastAPI backend (this repo)
                              |- reads room storage through Liveblocks REST API
                              |- calls LLM planner
                              |- normalizes safe tldraw operations
                              |- stores agents / messages / transcript records in Postgres
                              |- writes pending changes back into Liveblocks
                              |- optionally generates media via Higgsfield

Google Meet
  |
  v
Chrome extension
  |
  v
POST /rooms/{room_id}/transcript
```

## Why The Architecture Looks Like This

Two decisions drive the whole system:

### 1. Liveblocks is the shared canvas boundary

The frontend writes user edits straight to Liveblocks, and the backend reads and patches that same room through the Liveblocks REST API. That means the agent always reasons over the same canvas state the users are seeing.

### 2. The LLM does not emit raw tldraw records directly

The planner asks the model for **high-level draft operations** such as:

- add a note
- add a geo shape
- connect two shapes
- update a label

Then `hacknu-back/app/operations.py` compiles those into concrete tldraw-compatible objects, applies backend styling defaults, resolves references, avoids bad arrow duplication, and keeps suggestions sane relative to the viewport and existing layout. This makes the agent more reliable and keeps prompt complexity manageable.

## Main User Flows

### 1. Autocomplete / proactive suggestions

1. Frontend detects user activity on the canvas, waits for idle, and calls `POST /complete`.
2. Backend fetches room storage from Liveblocks.
3. Backend loads rejected history and recent meeting context.
4. Planner generates a small, contextual suggestion.
5. Backend compiles and normalizes operations.
6. Backend stores the suggestion in Postgres and writes it to Liveblocks `pendingChanges`.
7. Frontend renders the suggestion on the board as a pending spatial change.
8. Users approve, reject, or edit it.

This is how the AI feels present without constantly interrupting.

### 2. Chat agent -> canvas action

1. User sends a prompt to an agent via `POST /agent/{agent_id}/run`.
2. Backend loads:
   - current room storage
   - that agent's chat history
   - recent transcript context
   - prior rejected changes
3. In `generate` mode, the backend produces pending canvas changes.
4. In `query` mode, the backend answers questions about the canvas and returns referenced shape IDs.

This makes the agent usable both as a collaborator and as a contextual explainer.

### 3. Human control loop

Pending changes can be:

- `approve`: commit into `shapes`
- `reject`: remove from Liveblocks and store as rejected history
- `edit`: treat human feedback as a revision request and generate a better replacement

This was important for the brief because the AI needed to participate without taking over the board.

### 4. Meeting transcript grounding

The Chrome extension polls Google Meet captions, batches them, and posts them to the backend. The backend stores transcript chunks in `meeting_transcripts`, deduplicates progressive caption fragments, and builds a cached summary plus recent raw lines for the planner.

That gives the agent access to the conversation, not only the visible canvas.

### 5. Media generation

The backend exposes Higgsfield-backed endpoints for:

- text-to-image: `POST /media/generate`
- image-to-video: `POST /media/generate/video`
- status polling: `GET /media/status/{request_id}`

For image generation, the backend first uses an LLM to decide whether the selected canvas content is visually suitable, crafts a stronger image prompt, then inserts the generated result into Liveblocks as canvas media data.

## Backend Components

### FastAPI app

- `hacknu-back/app/main.py`: app setup, CORS, router mounting, health endpoint
- `hacknu-back/app/routes.py`: agent, autocomplete, transcript, and approval flows
- `hacknu-back/app/generate_routes.py`: media generation endpoints

### Planner and operation compiler

- `hacknu-back/app/planner.py`: prompt construction, LLM calls, query answering
- `hacknu-back/app/operations.py`: converts semantic draft ops into concrete tldraw-safe operations
- `hacknu-back/app/shapes.py`: backend shape schemas and defaults

### Integrations

- `hacknu-back/app/liveblocks.py`: Liveblocks REST client for storage and presence
- `hacknu-back/app/transcript.py`: transcript ingestion, deduplication, summarization cache
- `hacknu-back/app/higgsfield.py`: Higgsfield client with polling helpers

### Persistence

- `hacknu-back/app/models.py`: `Agent`, `AgentChange`, `ChatMessage`, `MeetingTranscript`
- `hacknu-back/app/database.py`: async SQLAlchemy engine/session
- `hacknu-back/alembic/`: schema migrations

## Data Model

### Liveblocks room state

The frontend and backend coordinate through shared room storage. The main keys used by the product are:

- `shapes`: committed canvas objects
- `pendingChanges`: AI suggestions awaiting approval
- `agents`: agent registry mirrored into room storage
- `agentChats`: frontend-managed collaborative chat UI state
- `meta`: room metadata and autocomplete lease

Generated media may also create Liveblocks asset records so image shapes can render on the board.

### PostgreSQL tables

- `agents`: agent registry per room
- `agent_changes`: pending / approved / rejected AI changes
- `chat_messages`: persisted agent conversations and change timeline items
- `meeting_transcripts`: captured meeting caption chunks

Postgres is used for memory, auditability, and replayable chat history. Liveblocks is used for shared real-time canvas state.

## API Surface

### Canvas participation

- `POST /complete`
- `POST /complete/action`
- `GET /agents/{room_id}`
- `POST /agents/{room_id}`
- `POST /agent/{agent_id}/run`
- `POST /agent/{agent_id}/action`
- `GET /agent/{agent_id}/messages`

### Meeting context

- `POST /rooms/{room_id}/transcript`
- `GET /rooms/{room_id}/transcript`
- `DELETE /rooms/{room_id}/transcript`

### Media

- `POST /media/generate`
- `POST /media/generate/video`
- `GET /media/status/{request_id}`

### Ops / health

- `GET /health`
- `GET /docs`

## Repository Structure

```text
.
├── README.md
├── docker-compose.yml
├── FRONTEND_STORAGE_GUIDE.md
├── agent.md
├── hacknu-back/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic/
│   ├── app/
│   └── tests/
├── infra/
│   ├── docker-compose.yml
│   ├── deploy.sh
│   └── README.md
└── meet-extension/
    ├── manifest.json
    ├── background.js
    ├── content.js
    ├── popup.html
    └── popup.js
```

## Local Development

### Prerequisites

- Docker / Docker Compose
- Node.js for the separate frontend repo
- A Liveblocks project
- At least one LLM provider key:
  - OpenAI via `OPENAI_API_KEY`
  - or Gemini via `GEMINI_API`
- Optional Higgsfield keys for media generation

### 1. Configure the backend

```bash
cp .env.example .env
```

Fill in the application values you need:

```env
OPENAI_API_KEY=
GEMINI_API=
LIVEBLOCKS_SECRET_KEY=
HIGGSFIELD_API_KEY_ID=
HIGGSFIELD_API_KEY_SECRET=
AGENT_PROVIDER=openai
AGENT_MODEL=gpt-5.4
AI_DEBUG_PRINTS=true
```

### 2. Start backend + Postgres

```bash
docker compose up -d --build
```

Useful URLs:

- backend: `http://localhost:8000`
- health: `http://localhost:8000/health`
- docs: `http://localhost:8000/docs`

Migrations run automatically when the backend container starts.

### 3. Run the frontend

In the separate frontend repo:

```bash
git clone https://github.com/bekzhanak/hacknu-front
cd hacknu-front
npm install
cp .env.example .env
npm run dev
```

At minimum, point the frontend at:

- `VITE_LIVEBLOCKS_PUBLIC_KEY`
- `VITE_BRAINSTORM_API_BASE_URL=http://localhost:8000`

### 4. Optional: run the Google Meet extension

Load `meet-extension/` as an unpacked Chrome extension, then in the popup set:

- `roomId`: the Liveblocks room used by the frontend
- `backendUrl`: your backend base URL, for example `http://localhost:8000`

Turn on Google Meet captions, then start capture. The extension will batch transcript chunks into the backend.

## Deployment

For server deployment, this repo includes:

- `infra/docker-compose.yml`
- `infra/deploy.sh`

The deploy flow syncs the backend source to a remote machine, copies a filtered `.env`, then starts the production compose stack remotely.

```bash
bash infra/deploy.sh
```

Deploy-specific variables live in the same repo-root `.env`, for example:

```env
DO_HOST=
DO_USER=root
SSH_KEY=~/.ssh/id_rsa
SSH_PUBLIC_KEY=
REMOTE_DIR=/root/hacknu-back
APP_PORT=9000
```

More detail is in `infra/README.md`.

## Testing

Backend tests live under `hacknu-back/tests/`.

```bash
cd hacknu-back
python -m unittest discover -s tests
```

The current test suite focuses on:

- shape / schema normalization
- planner and route helper behavior
- approval and pending-change flows

## Why This Fits The Brief

The brief was not asking for a prettier whiteboard. It was asking for a new interaction model where AI feels like it is already in the room.

This system pushes in that direction by grounding the agent in three sources at once:

- the shared canvas state
- the live conversation transcript
- the human approval loop

That combination is what turns the agent from a disconnected chatbot into a canvas participant.

## Scope And Tradeoffs

This was built as a hackathon demo for a single collaborative session, not as a production platform.

Intentional tradeoffs:

- strong reliance on Liveblocks as the room-state boundary
- best-effort transcript capture through DOM polling of Google Meet captions
- approval-gated AI edits instead of fully autonomous canvas mutation
- simple Docker-based deployment

Those tradeoffs were acceptable for the brief because the goal was to make AI participation feel real in a demo-length session.
