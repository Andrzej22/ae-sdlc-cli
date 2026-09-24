# Beyond GoF: macro-architecture, AI agents, teams

Contents: 1. Enterprise application patterns · 2. DDD · 3. Microservices · 4. AI agents ·
5. Team guardrails

This file is the single source for AI-era rules (SKILL.md keeps only a pointer). General
design rules live in SKILL.md "Core stance" and are referenced here by number.

GoF/GRASP/SOLID are the micro level (classes inside one process). Macro levels follow the
same logic: find the axes of change, isolate them, keep coupling low.

## 1. Enterprise application patterns (Fowler, PoEAA)

- **Layered architecture:** presentation (controllers) → business (use cases, domain) →
  infrastructure (DB, SMTP, queues, external APIs). Layers frame DIP; they don't guarantee it.
- **Transaction Script vs Domain Model:** simple CRUD-ish flows → transaction scripts are fine;
  rich interacting rules → domain model. Choose explicitly per module.
- **Unit of Work, Identity Map, Data Mapper:** separate business logic from persistence.
  Active Record trades SRP for speed — fine for simpler apps, migrate when complexity grows.
- Frameworks (Spring, NestJS, Django, .NET, FastAPI + SQLAlchemy) are built from these
  patterns; knowing them explains the framework instead of copying annotations.
- Applies to frontends too: complex UI without these ideas becomes a component-shaped Big Ball
  of Mud.

## 2. DDD

- Split systems by **bounded contexts** (business capability and language), not by DB tables
  and not by technical layers.
- **Ubiquitous language:** `Invoice`, `ship()` — not `BillingDataContainer`,
  `updateDeliveryStatusAndNotify()`.
- The same physical thing can have different models per context (a product is a box with
  weight for Warehouse, a priced line with VAT for Sales) — that's correct.
- Wrong context boundaries cost more than any bad class design.

## 3. Microservices

- Microservices without low coupling / high cohesion = distributed Big Ball of Mud: synchronous
  chains, cascading timeouts, no useful stack trace. Learn to modularise inside one deployable
  first.
- Design for failure: the network is unreliable.
  - **Saga** with compensating transactions instead of distributed transactions.
  - **Circuit breaker**, timeouts, retries with backoff and idempotency.
  - **Observability:** metrics (latency, errors), centralised logs, distributed tracing with a
    correlation id.
  - **Elasticity:** stateless services, state in DB/cache.
  - **Event-driven architecture:** publish facts ("OrderCreated"); consumers subscribe.
    The broker acts like a Mediator, services interact Observer-style — with the same cascade
    risks at larger scale.
- At macro level NFRs (scalability, availability, fault tolerance) drive decisions.

## 4. AI coding agents and AI in the system

**Agents as developers**
- An agent is a fast, diligent junior. Without contracts it produces a God object that works
  today and collapses on the next requirement, then hallucinates while extending it.
- The senior role shifts to designing boundaries and reviewing: contracts (interfaces,
  Protocols, type hints), the pattern to use, allowed/forbidden imports, files in scope,
  acceptance tests, verification commands (Mode 5 in SKILL.md).
- Name patterns and principles precisely in the prompt: "implement `DiscountPolicy` as a
  Strategy registry; new discounts must not modify `OrderCalculator` (OCP)".
- Low coupling and semantic cohesion reduce the context an agent needs for a correct change,
  and with it hallucinations and collateral breakage.
- Agents imitate the codebase. Keep one clean reference implementation per concept; fix a bad
  example before asking agents to extend it, or it will be multiplied.
- Context locality: a change should be understandable from a few files. Prefer explicit wiring
  in a composition root over magic (heavy metaprogramming, reflection-based auto-registration,
  implicit globals) that neither agents nor newcomers can trace.
- Code became cheap, review did not. Small scoped diffs, local reasoning and explicit wiring
  are what keep review feasible.
- Refactoring became cheaper *when tests exist*, which strengthens core rule 7: hardcode now,
  let the axis of change reveal itself, then extract (often mechanically, by an agent). Without
  tests that argument collapses, so tests at boundaries come first.
- Executable guardrails beat instructions: type checks, layer/import contracts, contract tests
  and architecture tests in CI. `CLAUDE.md` / `AGENTS.md` describe boundaries; CI enforces them.
- Typical agent failure modes to review for: generated God objects, fat routes, helper modules,
  speculative interfaces, near-duplicate clones, drive-by refactors, hallucinated APIs, weakened
  tests.

**LLMs as components of the system** (code-level boundary only; model choice, guardrail stack,
eval design and agent topology belong to ai-engineering-mentor if installed)
- The LLM provider is the most volatile dependency: models, pricing, limits and SDKs change
  monthly. Always behind a domain-named port (protected variation, case "external dependency").
- Model output is untrusted input: request structured output and validate it at the adapter;
  never let raw text drive writes or actions unvalidated.
- Treat calls as unreliable network I/O: timeouts, retries with backoff, idempotency, fallback
  model/provider where it matters, cost and latency metrics, tracing.
- Model ids, prompts and parameters are configuration (versioned), not code in use cases.
- Evals are contract tests for the LLM port: run them against every adapter/model change.
- Agentic workflows are still software: tools are ports, orchestration is a use case, state
  lives outside the process for elasticity — the same GRASP/SOLID reasoning applies.

## 5. Team guardrails

- Consistency beats individual brilliance: shared contracts, layer rules, patterns catalogue,
  review checklist — so the codebase stays coherent whoever (or whatever) edits it.
- Own the trade-offs: explain choices to the business in money, time and risk.
- Stack and structure are chosen for the team that maintains them: a junior-heavy team ships
  faster and safer with a simple modular monolith than with fragmented services.