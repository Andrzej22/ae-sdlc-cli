# Review checklist

Contents: 0. Not findings · 1. Under-design smells · 2. Over-design smells · 3. Runtime &
concurrency smells · 4. AI-generated code & AI boundaries · 5. Implementation self-check ·
6. Writing a finding

Severity follows blast radius (rule 9) more than the name of the violated principle, and
findings resting only on [assumed] scenarios are capped at Minor (rule 21).

## 0. Not findings (don't flag these on their own)

- A single obvious conditional or a `match` inside a factory (rule 15).
- DTOs used as boundary objects (rule 14).
- A class without an interface when no reason from rule 5 applies.
- A local hardcode on an axis that hasn't moved yet — note a trigger instead (rule 7).
- Folder layout that differs from a textbook when responsibilities are well allocated (rule 3).

Default severity is a starting point — raise it on a confirmed axis of change or hotspot,
lower it when no realistic change scenario exists.

## 1. Under-design (coupling, cohesion, responsibility)

| Smell | How to spot | Principle | Fix direction | Default |
|---|---|---|---|---|
| God object | Many unrelated actors change it; frequent merge conflicts; hard to name | SRP, High Cohesion | Split by actor/axis of change | Major |
| Fat controller | Business rules, queries or branching in routes/handlers/cron entry files | Controller, SRP | Move to use case / domain; controller 1–3 lines | Major |
| Anemic model without invariants | Entities accept any state; validation scattered in services | Information Expert (Smart Data) | Move invariants into entity/value object | Minor–Major |
| Train wreck / Ask instead of Tell | `a.b().c().d()`, loops over another object's internals | Information Expert, Low Coupling | Delegate to the expert | Minor |
| Getter/setter for every field | Objects are mutable bags; outside code enforces rules | Encapsulation | Intent methods (`top_up`, `cancel`) | Minor |
| Type switch in business logic | `if type ==`, `match kind` repeated in several places | Polymorphism, OCP | Polymorphic types; deport choice to a factory | Major if repeated on a live axis |
| Downcasting / isinstance on subtypes | Caller checks concrete type to behave correctly | LSP | Fix the hierarchy or the contract | Major |
| Stub implementations | `NotImplementedError`, silent no-ops in implementers | LSP, ISP | Split interface by role | Major |
| Subtype breaks expectations | Values outside the base contract (negative costs, None where never returned) | LSP / Design by Contract | Model the concept separately | Blocker if money/data |
| Fat interface | Clients get methods they must not call | ISP | Role interfaces per client | Minor–Major (Major if dangerous op) |
| Domain imports infrastructure | ORM/SDK/HTTP types in domain or use cases | DIP | Port in domain language + adapter | Major |
| Vendor-shaped contract | `save_to_postgres`, `StripeService` in domain | DIP | Rename to business intent | Minor |
| Concrete construction in services | Service instantiates concrete clients/processors | Creator, DIP | Inject, or factory for newables | Minor–Major |
| Classic singleton / global state | `get_instance()`, mutable module globals used as service locator | DIP, testability | Container scope / composition root | Major |
| Utils/Helpers dumping ground | Static functions over another concept's data | Pure Fabrication misuse | Find missing domain object (e.g. `DateRange`) | Minor |
| Deep inheritance | >3 levels, or `extends` failing "A is a B" | Inheritance rules | Flatten; interfaces/composition | Minor–Major |
| "Wolf inside out" base class | Base provides utilities children must remember to call in order | Template Method | Base owns lifecycle, children fill steps | Major if resources leak |
| Combinatorial subclasses | Class names form a grid (PdfSales, ExcelSales, PdfTax…) | Bridge / Decorator | Split axes, compose | Major |
| Coupling with wide blast radius | A vendor/infra type (ORM model, SDK response, LLM client) appears in many modules | Low Coupling, DIP | Translate at the adapter; domain types inside | Major |
| Semantically incoherent class | Members change for different reasons / express different concepts, even if the class is small | High Cohesion (semantic) | Split by concept | Minor–Major |
| Inheritance for reuse | Subclass exists to borrow helpers, not to be substitutable | Inheritance ≠ reuse, LSP | Composition / functions | Minor–Major |
| DTO-only domain with scattered invariants | Rules on the same data checked in several services | Smart Data | One guarding place (entity or single policy) | Major |
| Not deletable | Removing a variant needs edits in many files | Optimize for deletion, OCP | Isolate variant + single registration | Minor–Major |
| Split by DB tables | Services mirror tables and call each other for every operation | DDD bounded contexts | Split by business capability | Major |

## 2. Over-design (flag with the same seriousness)

| Smell | How to spot | Fix direction | Default |
|---|---|---|---|
| Speculative generality | Interfaces/factories/strategies with one impl and no roadmap, outside system boundaries | Inline; keep a trigger note | Minor |
| Abstraction bureaucracy | Several layers that only forward one call | Collapse layers | Minor |
| Ceremonial abstraction | Interface/port with no reason to change and no real second consumer | Remove or inline; keep boundary ports | Minor |
| DI mistaken for design | Container autowires everything; huge constructors; concretes injected into domain | Fix dependency direction; split God services | Major |
| Empty hierarchies | Many classes, real code is one line | Replace with function/data | Minor |
| Pattern for a constant rule | Strategy/State for things that never change | Plain method | Nit–Minor |
| Classic GoF where idiom exists | One-method strategy classes, Visitor in a language with `match`, Director-builder | Use language idiom | Nit |
| Premature microservices | Synchronous chains between services, no clear bounded contexts | Modular monolith first | Major |
| ISP taken to absurdity | Cohesive pairs split (`open`/`close`) | Merge | Nit |

## 3. Runtime and concurrency

| Smell | Principle | Default |
|---|---|---|
| Request state in fields of shared middleware/filters/singletons | Stateless pipeline | Blocker |
| Observer cascade: setters/events trigger DB/HTTP chains or loops | Observer pitfalls | Major |
| Eager loading of huge graphs to answer a small question | Cohesion vs memory (lazy loading) | Major |
| Hidden N+1 through lazy proxies | Proxy pitfalls | Minor–Major |
| Shallow copies aliasing nested state | Prototype pitfalls | Major |
| Pooled objects returned without reset | Object pool | Blocker if data leaks |
| Money as float | Encapsulation of value | Blocker |
| Distributed transaction without compensation | Saga | Major |

## 4. AI-generated code & AI boundaries

| Smell | How to spot | Fix direction | Default |
|---|---|---|---|
| Generated God object | One class/module mixing HTTP, parsing, validation, SQL, formatting | Split by responsibility; give agent contracts next time | Major |
| Near-duplicate clones | Several almost identical functions/classes instead of reuse | Consolidate (Rule of Three is already met) | Minor–Major |
| Style drift | New code ignores the project's reference implementation | Align with the reference, or fix the reference first | Minor |
| Drive-by refactors | Diff touches unrelated files "while at it" | Revert out-of-scope changes | Minor |
| Hallucinated API / invented config flags | Calls or options that don't exist in the dependency | Verify against docs/types; add type check to CI | Blocker |
| Weakened tests | Assertions removed/loosened, tests skipped to go green | Restore; treat as Blocker | Blocker |
| LLM SDK in domain | Provider client or response types imported in domain/use cases | Domain-named port (e.g. `TextSummarizer`) + adapter | Major |
| Unvalidated LLM output | Model output used without schema validation | Structured output + validation at the adapter; treat as untrusted input | Blocker if it drives writes/actions |
| Hardcoded model/prompt | Model ids, prompts, params inside business code | Configuration + versioned prompts; evals as contract tests | Minor–Major |
| Missing resilience on LLM calls | No timeout/retry/idempotency/fallback | Same treatment as any unreliable network dependency | Major |
| Untraceable magic | Reflection-based auto-registration, implicit globals, metaprogramming that hides wiring | Explicit composition root | Minor–Major |
| Guardrails only in prose | Layer rules exist only in docs/CLAUDE.md | Import/layer checks and contract tests in CI | Minor |

## 5. Implementation self-check (run before presenting code)

- [ ] Controllers/handlers only map input → call use case → map output.
- [ ] Domain and use cases import no infrastructure; ports are in domain language.
- [ ] Newables built by owner/factory/builder; injectables arrive via constructor/DI.
- [ ] Entities enforce their invariants (Smart Data).
- [ ] No repeated type switches; any unavoidable choice lives in one factory.
- [ ] Every implementation honours the full contract (no stubs, no surprising values).
- [ ] Every new abstraction is justified by a rule-5 reason tagged [confirmed].
- [ ] Nothing added "for the future"; deferred abstractions noted as triggers instead.
- [ ] No Utils/Helpers; no global mutable state; pipeline components stateless.
- [ ] Inheritance only for IS-A, ≤2–3 levels.
- [ ] Tests use fakes for ports; contract tests if several implementations exist.
- [ ] No commented-out old code left behind.
- [ ] The most likely change touches few modules (small blast radius).
- [ ] Deleting this variant/feature = deleting its implementation + one registration line.
- [ ] I can say where the complexity went and why it's cheaper there.
- [ ] New code follows the project's existing reference implementation (or I flagged why not).
- [ ] Diff stays in scope; no existing test was weakened.
- [ ] LLM/external calls: behind a port, output validated, timeouts/retries set, config not hardcoded.

## 6. Writing a finding

Good: "**Major** · `billing/service.py:40-180` · `InvoiceService` computes VAT, applies
promotions and parses the ERP response. Three actors (accounting, marketing, ERP vendor). When
the ERP format changes, VAT logic is redeployed and retested (blast radius: billing module + 40 tests + invoice export). → Extract `ErpInvoiceParser`
(adapter) and `PromotionPolicy`; keep VAT in `Invoice`. Cost: ~2 new classes, 1 day."

Bad: "Violates SRP, should be refactored." (no scenario, no fix, no cost)