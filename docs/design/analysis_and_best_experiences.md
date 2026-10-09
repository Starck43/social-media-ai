# Comparative Analysis (20+ analogs researched)

> **Historical research — not the active implementation backlog.** Reviewed against dev `f11acefba8fbc2f47e3a17f670a97587208f1dd3` on 2026-10-08. Several gaps below are now implemented, while performance/competitor claims remain unverified. See [proposal review](proposal_review.md), [current roadmap](../ROADMAP_INTEGRATED.md), [local UX plan](../LOCAL_EXPERIENCE_PLAN.md) and [production gates](../BUSINESS_PRODUCTION_READINESS.md). Original research is preserved below.

## What the project has that is strong/differentiated
| Feature | Status | Notes |
| --- | --- | --- |
| VK + Telegram + MAX tri-platform | ✅ | Rare combo; socmon marks MAX unsupported, twidgest-bot has VK+X only |
| Multi-tenant (shared bot, invite codes) | ✅ | claude-digest, twidgest-bot also multi-tenant; yours uses tenant_scoped mixin |
| Hybrid collection layers per source | ✅ | L1 Bot API / L2 MTProto (Telethon) / VK API; configurable via `Source.params["mode"]` |
| DB-backed task queue (no Redis/Celery) | ✅ | `SELECT ... FOR UPDATE SKIP LOCKED` on `jobs` table; simpler ops |
| LLM providers in DB (Fernet-encrypted) | ✅ | OpenAI-compat + Anthropic; admin UI with "Test connection" action |
| Confirmation flow for dangerous actions | ✅ | Staged in session, human "да/нет" confirmation |
| Learning from chat + reflection jobs | ✅ | `learn` (watermark-driven fact extraction) + `reflect` (dedup/repair from /bad notes) |
| Per-tenant daily LLM cost cap | ✅ | Tracked across agent chat, digests, learn/reflect |
| Web UI under `/app` (Jinja2/HTMX/Alpine) | ✅ | Not just Telegram; sqladmin optional |
| 80+ test files, pytest, isolated test schema | ✅ | Good coverage |

## What analogs have that this project could adopt
| Capability | Best Example(s) | Gap in current project |
| --- | --- | --- |
| Structured LLM outputs (Pydantic models) | SmIA, Ratatoskr, IntelFlow | Strict write boundaries done (PR #17); legacy `extract_json()` retained for compat only |
| Semantic dedup (embeddings + pgvector) | Telo-watch-tower, Ratatoskr, Syne | Only content-hash dedup; no near-duplicate detection |
| Two-model pipeline (cheap select + quality write) | ai-news-digest, rss-bot | Single model for everything |
| LLM observability (tracing, cost per call) | SmIA (Langfuse), OpenCrow | Cost tracked but no trace visibility |
| Memory decay/eviction + auto-promotion | Syne, Lethe, Mem0 | Flat `agent_memory` KV; no TTL, no semantic search |
| Knowledge graph for memory | Syne, Cognee | No entity-relation extraction |
| Error learning (post-hook on failure) | Kit, Agent Life Space | No auto-correction capture |
| Proactive notifications | BuzzAgent, Nuggets | Reactive only (digest + chat) |
| Prompt injection sanitization | Iva, Agent Life Space | None visible |
| Multi-model fallback with health probing | rss-bot (AUTOMODEL), Telo-watch-tower | Fallback on error only; no proactive probe |
| Plugin/skill system | Glean (4-layer), Hermes Agent | agent_scenario exists but no skill framework |
| Reproducibility/audit trail | socmon (config/model/prompt snapshots) | No per-run config snapshot |
| Raw payload retention for re-parsing | socmon | collected_items exists but limited |
| Bilingual output (RU/EN) | IntelFlow, BuzzAgent, akyn-bot | Russian only |
| Web search tool integration | Kit, IntelFlow | No web search in agent tools |
| Tiered subscriptions + Telegram Stars | claude-digest, twidgest-bot | Plan tiers exist; no payment integration |
| Template + AI hybrid onboarding | twidgest-bot (15 templates + AI suggest) | Manual source/task creation |

## Implementation Plan (Prioritized)
### Phase 1: Foundation (High Impact, Low Risk)
- [x] Structured outputs (partial, PR #17): strict local Pydantic contracts added (`DigestSummary`, `LearnedFacts`/`ExtractedFact`, `ReflectionResult` in `app/services/ai/output_contracts.py`, enforced in digest builder and learn/reflect before writes); legacy `extract_json()`/`_clamp_confidence` retained only for compatibility (covered by `tests/test_learning.py`, not used by writes). Provider-native `response_format={"type": "json_schema"}` still open
- [x] Prompt injection guard: Add input sanitizer in `app/agent/runtime.py` before passing user text to LLM (block `ignore previous instructions`, etc.)
- [ ] Two-model pipeline: Add `LLMModel` field `role: selector/writer/general`; update `LLMClientFactory` to pick by role; use cheap model for collection analysis, quality model for digests/agent chat
- [ ] LLM observability: Integrate Langfuse (optional, via env) for trace logging in `chat_with_fallback()`

### Phase 2: Memory & Intelligence (High Value)
- [ ] Semantic memory: Add `pgvector` extension; add `embedding` column to `agent_memory`; implement `semantic_search(query, top_k)` tool for agent
- [ ] Memory decay: Add `access_count`, `last_accessed`, `ttl_days` to `agent_memory`; add nightly decay job (promote frequent, expire stale)
- [ ] Error learning hook: Add post-hook in dispatcher that captures failed tool calls + user message → saves `Correction` to vector store (like Kit)
- [ ] Knowledge graph (optional): Extract entities from permanent memories → store relations; enable 1-hop traversal in recall

### Phase 3: Collection & Cost Optimization
- [ ] Algorithmic pre-filter: Before LLM analysis, run regex/keyword/engagement filters (like BuzzAgent's 70% token savings)
- [ ] Semantic dedup: On collect, embed new items; cosine similarity against recent embeddings in `pgvector`; skip near-duplicates
- [ ] Proactive model health: Add background probe job (like rss-bot) that tests all configured models every N hours; auto-disable degraded

### Phase 4: Product Polish
- [ ] Web search tool: Add DuckDuckGo/Tavily tool to agent registry
- [ ] Bilingual digests: Add `language` field to `Tenant`; render digests in RU/EN per workspace
- [ ] Telegram Stars billing: Integrate payment webhook for Pro tiers (like claude-digest)
- [ ] Template onboarding: Pre-seed 10-15 source/task templates; `/createchannel ai <topic>` suggests sources via LLM

## Open Questions / Decisions Needed
- Memory scope: Keep flat KV + add pgvector column, or migrate to Mem0-style fact extraction? (Recommend: additive — keep KV, add vector column)
- Observability vendor: Langfuse (open source, self-hostable) vs custom? (Recommend: Langfuse optional, behind `LANGFUSE_SECRET_KEY` env)
- Skill system vs scenarios: Extend `agent_scenario` to support procedural skills, or add separate skill registry? (Recommend: extend scenario with `skill_steps` JSON)
- Payment integration: Telegram Stars only, or also Stripe/YooKassa for web UI? (Recommend: Stars first, web later)

## Validation Checklist
- [x] Write-path LLM outputs validated by strict Pydantic contracts (PR #17; digest/learn/reflect); provider-native schema protocols still open
- [ ] `pytest` passes with new structured outputs
- [ ] Daily cost cap still enforced with two-model pipeline
- [ ] Memory recall tool returns relevant facts in <200ms (pgvector HNSW index)
- [ ] Prompt injection attempts blocked and logged
- [ ] Digest generation cost reduced by ≥30% via pre-filter + selector model
- [ ] Rebranding applied: repo name, package name, README, Docker image, CLI help text

## Out of Scope (for now)
- MCP server exposure
- WhatsApp/Discord/Slack channels
- Mobile app
- Hosted SaaS offering
