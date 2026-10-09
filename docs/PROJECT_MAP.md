# Project map: current runtime and planned expansion

Baseline: dev `234a23dd7d1d2ce8dd3813f1ed63c7c7ef748669`, inspected 2026-10-09. Diagrams are explanatory views, not deployed-state evidence or complete dependency/FK graphs. Current views and separately labeled planned views prevent a roadmap being mistaken for shipped features. See [documentation index](DOCS_INDEX.md) and [status rules](DOCUMENTATION_GUIDE.md).

## 1. Current system context

```mermaid
flowchart LR
    U[User / operator] --> CHAT[Telegram / MAX chat]
    U --> WEB[Web UI / API / admin]
    CHAT --> A[AI Assistant application]
    WEB --> A
    VK[Authorized VK API] --> A
    TG[Telegram updates / authorized MTProto] --> A
    A <--> DB[(PostgreSQL)]
    A <--> LLM[Configured LLM providers]
    A --> REC[Owned digest / notification recipients]
    A --> OPS[Fixed operator alerts]
    classDef core fill:#dbeafe,stroke:#2563eb,color:#172554
    classDef store fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef external fill:#fef3c7,stroke:#d97706,color:#78350f
    class A,CHAT,WEB core
    class DB store
    class VK,TG,LLM,REC,OPS external
```

MAX transport is not MAX collection. Recipients must be owned/authorized; operator alerts cannot carry arbitrary customer reports. Tenant isolation, complete cost admission and production readiness still have open gates.

## 2. Repository map and responsibility

| Path | Responsibility | Read next |
| --- | --- | --- |
| `app/runtime.py`, `app/worker.py` | Combined runtime and worker entrypoints | [Tasks](AGENT_TASKS.md) |
| `app/main.py`, `app/web/`, `app/api/`, `app/admin/` | Optional HTTP surfaces and UI | [API](API.md), [Admin](ADMIN.md) |
| `app/agent/` | Interactive tools, sessions, prompts and memory | [Agent](AGENT.md) |
| `app/tasks/`, `app/jobs/` | Scheduling, queue claiming and job handlers | [Tasks](AGENT_TASKS.md) |
| `app/channels/` | Messenger listener and outbound transports | [Channels](CHANNELS.md) |
| `app/services/social/`, `app/services/monitoring/` | Credentials, platform clients, pull collection and push ingest | [Collection](COLLECTION.md) |
| `app/services/ai/` | Analysis, prompt/schema assembly, reporting and model orchestration | [AI pipeline](AI_PIPELINE_AND_PROMPTS.md) |
| `app/services/digest/` | Digest build/render and delivery checkpoint components | [Digest](DIGEST.md) |
| `app/services/tenancy/`, `app/core/` | Workspace context, configuration, DB/security foundations | [Tenancy](TENANCY.md) |
| `app/models/`, `app/schemas/` | ORM, managers and typed contracts | [Models](MODELS.md) |
| `app/services/notifications/` | Workspace notifications and operator alert separation | [Notifications](NOTIFICATIONS.md) |
| `app/templates/`, `app/static/` | Web assets and templates | [UI design](design/ui.md) |
| `cli/`, `scripts/` | Operator commands and setup/test utilities | [CLI](CLI.md) |
| `migrations/`, `tests/`, `docker/` | Schema history, regression checks and deployment assets | [Deployment](DEPLOYMENT.md) |
| `app/celery/` | Frozen legacy; not the active runtime architecture | Root guidance |

No nonexistent `app/services/llm/` directory is introduced in this map: the inspected services directory contains `ai`, not a dedicated `llm` directory. Recheck older guidance against actual source paths before navigating or editing.

```mermaid
flowchart TB
    Entry[Entrypoints] --> Agent[Agent / HTTP interaction]
    Entry --> Runtime[Scheduler + worker + channel listener]
    Agent --> Services[AI / collection / digest / notification services]
    Runtime --> Services
    Services --> Models[ORM models and managers]
    Models --> DB[(PostgreSQL)]
    Services --> Ext[Platform and LLM APIs]
    Core[Configuration / tenancy / permissions] -.-> Agent
    Core -.-> Runtime
    Core -.-> Services
```

This is a responsibility view; arrows do not certify every import or authorization path.

## 3. Current collection and report flow

```mermaid
flowchart LR
    Schedule[Owned schedule] --> Queue[(Jobs)] --> Worker[Worker]
    Worker --> Pull[Authorized collection]
    Pull --> Stage[(Staged items)] --> Analyze[Structured analysis]
    Push[Telegram updates] --> Ingest[Push ingest] --> Analyze
    Analyze --> Analytics[(Stored analytics)]
    Analytics --> Brief[Deterministic report brief]
    Brief --> Narrative[Optional LLM narrative]
    Narrative --> Render[Render / split]
    Render --> Targets[Current owned recipients]
```

Pull staging and Telegram push ingest have different paths; do not infer every pushed item uses the identical job staging lifecycle. Scenario defines analysis methodology; task selects sources/schedule/reaction; grouping is a report projection. Evidence and metric coverage depend on actual source capabilities.

## 4. Agent interaction sequence

```mermaid
sequenceDiagram
    actor User
    participant Surface as Messenger / Web
    participant Agent
    participant Policy as Identity / permission checks
    participant Model as LLM
    participant Tools
    User->>Surface: Request
    Surface->>Agent: Resolve workspace and session
    Agent->>Model: Bounded context + tool definitions
    Model-->>Agent: Answer or proposed tool call
    Agent->>Policy: Check operation and actor
    alt Allowed read operation
        Agent->>Tools: Execute scoped read
        Tools-->>Agent: Structured result
    else Allowed confirmation-gated write
        Agent-->>User: Preview / confirmation request
        User->>Agent: Approve or cancel
        Agent->>Policy: Recheck permission
        Agent->>Tools: Execute only if approved and allowed
    end
    Agent-->>Surface: Response
    Surface-->>User: Result
```

Conceptual happy path, not proof of complete fail-closed identity or actor/TTL-bound approval. Those remain readiness work; rejected operations must not execute.

## 5. Delivery: merged components versus inactive integration

```mermaid
flowchart TB
    Builder[Current builder / broadcast path] --> Existing[Existing delivery behavior]
    subgraph Foundation[MERGED foundation - not end-to-end activation]
        Schema[0087 checkpoint storage]
        Contract[Versioned state contract]
        Parts[Deterministic HTML parts]
        Transport[Opt-in one-part transport]
        Store[Locked checkpoint store]
        Schema --> Contract --> Store
        Parts --> Store
        Transport --> Store
    end
    Snapshot[PR 12: atomic fresh snapshot - IN REVIEW] -.-> Foundation
    Binding[Original job / run / window binding - OPEN] -.-> Snapshot
    Activation[Coordinated builder activation - OPEN] -.-> Store
    Recovery[Truthful outcomes / uncertainty recovery - OPEN] -.-> Activation
```

At the inspected baseline PR #12 is draft/open (`91a4ab1`), not merged. Its reported 250 focused passes are the other task's evidence, not checks rerun by this documentation task. Merged foundation does not mean deployed migration or active retries. Latest work-in-review checklist is on that PR branch; merged status is [IMPLEMENTATION_STATUS](IMPLEMENTATION_STATUS.md). Do not duplicate receipt fields into a future general-purpose outbox without a separate design.

## 6. Proposed cloud/hybrid topology - NOT IMPLEMENTED

```mermaid
flowchart TB
    subgraph Cloud[Cloud trust boundary]
        Web[Web / Desktop cloud access] --> Core[Core / policy / orchestration]
        Core --> CloudData[(Only cloud-approved content)]
        Core --> External[Policy-allowed external models]
    end
    subgraph Company[Company trust boundary]
        Gateway[Server connector] --> Internal[Approved internal sources]
        Gateway --> LocalModel[Local inference]
        LocalUI[Local-only input / result view] --> Gateway
    end
    subgraph Personal[Personal device trust boundary]
        Connector[Personal connector] --> Files[Selected folders]
    end
    Gateway -->|Outbound authenticated connection / permitted metadata| Core
    Connector -->|Outbound authenticated connection / allowed results| Core
```

One connector engine, personal/server profiles; no customer server required for direct cloud use. Local-only content, including prompts/results, must not pass through cloud chat. There is no automatic external model fallback. Autonomous core placement is a later stage. Detailed policies and acceptance: [DEPLOYMENT_ARCHITECTURE](DEPLOYMENT_ARCHITECTURE.md).

## Maintenance

Keep this map high-level and link to authoritative contracts rather than copy their field lists. Change diagrams with the corresponding behavior change; label baseline, active versus opt-in versus proposed status and verification limits. Mermaid source is versioned and rendered by GitHub; diagram parsing/rendering must be checked during PR review. This task does not implement these future components or certify the current deployment.
