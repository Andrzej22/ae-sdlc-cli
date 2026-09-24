# GoF patterns in modern form

Contents: 0. Ground rules · 1. Pain → pattern table · 2. Creational · 3. Structural ·
4. Behavioral · 5. Commonly confused pairs

## 0. Ground rules

Selection is governed by SKILL.md core rules 1, 2, 5, 7, 8 and 16 (problem first, axis of
change, reasons for abstraction, Rule of Three, patterns as functions, newables vs injectables).
Specific to GoF:
- GoF without GRASP/SOLID is cargo cult; the 1994 book shows the idea, production uses the
  modern, usually simpler, form.
- A DI container is effectively a large Abstract Factory + Singleton for injectables.
- Categories (creational/structural/behavioral) are for navigation only; decide by pain.

## 1. Pain → pattern

| Pain | Consider | First check the cheaper option |
|---|---|---|
| Concrete class instantiated inside business logic, several implementations | Simple factory / static factory / DI | One implementation, no roadmap → plain constructor |
| Constructor with many optional/positional params | Builder (fluent) | Keyword arguments / dataclass defaults |
| Expensive or setter-heavy setup of similar objects | Prototype | Factory with presets |
| Costly-to-create, frequently reused resources | Object pool | Infra-level pooling (bouncer, driver pool) |
| Third-party/legacy API doesn't match your contract | Adapter | — (wrap external deps always) |
| Need caching / auth / lazy load without touching the class | Proxy | Framework feature (ORM lazy load, cache decorator) |
| Independent add-on behaviours that combine | Decorator | Single fixed behaviour → just add it |
| Clients orchestrate many subsystem classes | Facade | — |
| Class count = A × B (e.g. reports × formats) | Bridge | Only one axis → Strategy |
| Uniform tree, clients write recursion | Composite | — |
| Millions of similar objects, OOM | Flyweight | Measure first |
| Long `if/else` choosing an algorithm | Strategy | Two branches that never change → keep the if |
| `switch(status)` in many methods | State | 2–3 trivial states → a flag/enum |
| Same algorithm skeleton, different steps, lifecycle must be enforced | Template Method | Composition (Strategy) if steps combine freely |
| UI/components poking each other directly | Mediator | — |
| Many independent reactions to an event | Observer / events | Direct call if exactly one reaction |
| Wall of pre-checks (CORS, auth, rate limit) before handler | Middleware pipeline | — |
| Undo/redo, queued/retried actions, macros | Command | Few changes, small object → Memento |
| Rollback of a small/medium object | Memento | — |
| Business rules edited without redeploy | Interpreter (small DSL) | Config tables / feature flags |
| Exposing internal collections | Iterator / generator | Return an immutable copy for small data |
| New operations over a closed, stable hierarchy | Pattern matching / singledispatch | Classic Visitor only if language lacks both |

## 2. Creational

**Singleton.** Wanting one instance (pool, cache) is fine; `get_instance()` + private
constructor is an anti-pattern: global state kills isolated tests and hides dependencies (DIP).
Modern form: singleton scope in the container, module-level instance, or Pure DI in the
composition root. Lazy init under concurrency races; double-checked locking is fragile — prefer
eager init. Relatives: Monostate (many instances, shared state), Multiton (keyed instances).

**Factory Method / Simple Factory / static factory.** The problem is not `new` but `new` inside
business logic. Classic GoF creator hierarchy is rarely written; use a simple factory
(accepting a local `match` = local OCP violation for a clean rest), a static/`classmethod`
constructor, a lambda/registry dict, or the DI container.

**Abstract Factory.** Consistent families that must not be mixed (all components for one
platform/vendor). Rare in application code; adding a new product type touches every factory.

**Prototype.** Clone a configured template instead of kilometres of setters. The object clones
itself (Information Expert). Shallow copy aliases nested objects — mutations leak into the
template; deep copy costs more and must handle cycles.

**Object Pool.** Rent/return expensive objects (connections, threads). Reset state on return.
In serverless/microservices this role often moves to infrastructure (connection bouncers).

**Builder.** Cures telescoping constructors. GoF form with Director is obsolete; use a fluent
builder ending in `.build()` that validates and returns an immutable object. Named arguments
reduce the need; still great for Test Data Builders and complex configs. Pairs with a factory
(factory picks the preset, builder assembles).

## 3. Structural

**Adapter.** Translate your ideal contract to a third-party/legacy API. Lives in
infrastructure; model the contract on your needs, not the first vendor's shape.

**Proxy.** Same interface, controls access: lazy loading, caching, security, remote. Watch for
hidden I/O (N+1 via lazy proxies).

**Decorator.** Stackable runtime add-ons behind the same interface (logging → cache → retry)
instead of a subclass per combination. Order of wrapping matters; always runs through the whole
chain and back.

**Facade.** One simple entry over a complex subsystem. Risk: grows into a God object.

**Bridge.** Two independent axes (abstraction × implementation) joined by composition to stop
the A×B class explosion. Fragile: collapses as soon as the abstraction checks the concrete
implementation type or builds direct dependencies.

**Composite.** Leaf and node share one interface; node delegates to children; recursion hidden.
Weak for heterogeneous trees; leaf `add()` is either meaningless or throws (LSP tension).

**Flyweight.** Split intrinsic (shared, immutable, heavy) from extrinsic (per-element, light)
state. Game dev, editors, huge in-memory graphs. Only after measuring memory.

## 4. Behavioral

**Strategy ("king of patterns").** Encapsulate interchangeable algorithms. Who selects:
(1) the client — clean context, selection logic scattered; (2) heuristic in context — client
ignorant, but tight coupling and OCP/DIP violations; (3) DI registry/map — most flexible in
enterprise. Several discounts at once = one composite strategy, never a list juggled by the
context. Stateless strategies → plain functions. Delete the old if/else, don't comment it out.

**State.** Each state is a class/object handling behaviour and transitions; kills status
switches. In JVM languages enum-with-behaviour is the easy form. Not for 2–3 trivial states.
Frontend "state management" is Observer, not this pattern.

**Template Method.** Base class owns the lifecycle (open → abstract step → close in `finally`),
subclasses fill steps, hooks are optional extension points. Anti-pattern it replaces: "wolf
inside out" — base class of utilities that children must remember to call. Risks: LSP breaks,
too many abstract steps. Without inheritance (Go) it becomes composition ≈ Strategy.

**Mediator.** Centralised coordination ("dictatorship"): components talk only to the mediator.
Risk: mediator becomes a God object. An event bus is a dumb postman; a mediator is an air
traffic controller.

**Observer.** Decentralised subscription ("anarchy"): source knows only the observer contract.
Danger: hidden cascades — a setter triggers DB calls, HTTP calls, new events, infinite loops,
connection exhaustion. Keep notifications explicit, avoid heavy synchronous work in handlers,
guard against re-entrancy. Scales up into Rx streams and message brokers.

**Chain of Responsibility → Pipes & Filters / Middleware.** Classic CoR (each link knows the
next) is legacy. Use isolated filters run by a pipeline coordinator. Filters must be stateless —
request state in a shared filter instance is a race condition and a data leak. CoR can
short-circuit; Decorator always completes.

**Command.** An action as an object: undo/redo, queues, retries, macros, audit. Best when
actions are many and the object is huge (200 GB video timeline). Descendants: job queue tasks,
CQRS commands.

**Memento.** Opaque snapshot created and restored by the object itself; no public getters and
setters for every field. Best when the object is small/medium and changes are few.

**Interpreter.** Small grammar for business rules (discounts, filters, formulas) so changes
don't need a deploy. Stop when you need loops, variables or user functions — then use the
project's own language in a sandbox instead of a homemade language.

**Iterator.** Safe traversal without exposing internals; lazy traversal of huge data with
filtering/pagination. Overkill for local arrays and private methods.

**Visitor.** Double dispatch to add operations to a closed, stable hierarchy (AST, linters).
Breaks Information Expert (elements become anemic), catastrophic if element types change often.
In modern languages prefer discriminated unions, sealed + `when`, `match`, `singledispatch`.

## 5. Commonly confused pairs

- **Strategy vs State:** Strategy swaps one operation, chosen from outside; State changes the
  whole object's behaviour and switches itself.
- **Strategy vs Template Method:** composition vs inheritance; Template enforces lifecycle.
- **Strategy vs Bridge:** one axis vs two independent axes.
- **Proxy vs Decorator:** controls access vs adds behaviour; single stand-in vs stackable layers.
- **Decorator vs Chain/Middleware:** always completes vs may short-circuit.
- **Mediator vs Event Bus vs Observer:** coordinator with logic vs dumb transport vs direct
  subscription.
- **Memento vs Command:** snapshot state vs record operations — choose by object size and
  number of actions.
- **Facade vs Mediator:** simplifies access for outside clients vs coordinates peers inside.