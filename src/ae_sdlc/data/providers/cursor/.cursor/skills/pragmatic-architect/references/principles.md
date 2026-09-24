# Principles: OOP, GRASP, SOLID

Contents: 1. OOP (paradigm, encapsulation, inheritance, polymorphism, abstraction) ·
2. GRASP (9 patterns) · 3. SOLID (5 principles) · 4. How they relate

Format per item: **Essence** · **Smells** · **Apply / don't** · **Nuance**

Cross-cutting rules (axis of change, blast radius, semantic cohesion, false equivalences,
evidence tagging) are defined once in SKILL.md "Core stance" and referenced here as "rule N".

---

## 1. OOP

OOP is a way of thinking, not the `class` keyword. Classes used as folders for procedural
code are the most common failure.

### Paradigm: object as a cell, messaging
- **Essence:** an autonomous unit that owns its state and decides how to answer a message.
  State + behaviour are inseparable. You ask (`order.cancel()`), you don't operate on its data.
- **Smells:** DTO + service that reads fields, decides and writes back = Anemic Domain Model
  (procedural code in an OOP costume).
- **Apply / don't:** OOP for structure, FP for pure data transformation inside methods, AOP-style
  mechanisms (decorators, middleware) for cross-cutting concerns. Mixing paradigms is normal.
- **Nuance:** messaging is the source of decoupling — the sender doesn't know *how*.

### Encapsulation
- **Essence:** two dimensions — bundling (data + behaviour together) and information hiding.
  Scales from fields to methods, packages, modules and whole layers.
- **Smells:** private fields + generated getters/setters for all of them; outside code relying on
  internals ("unscrew a nut here, something falls off over there"); shared mutable state touched
  from many threads.
- **Apply / don't:** always for objects with invariants; boundary DTOs are just transport.
- **Nuance:** Tell, Don't Ask. Money in `Decimal`, never `float`. In languages without `private`,
  encapsulation lives in conventions and module boundaries.

### Inheritance
- **Essence:** mirrors real hierarchies; exists to enable polymorphism, not to save typing.
- **Smells:** hierarchy deeper than 2–3 levels; `extends` that fails the spoken "A is a B" test;
  or the opposite dogma — no common base where one clearly exists, forcing loops over 50
  unrelated classes (the "medical check / anniversary merch for all employees" case).
- **Apply / don't:** IS-A only. For shared contracts use interfaces; for shared implementation
  without hierarchy use traits/mixins/composition.
- **Nuance:** diamond problem is why most languages forbid multiple inheritance of classes.
  See rule 13 (inheritance ≠ reuse).

### Polymorphism
- **Essence:** the killer of `if type == ...`. Three kinds: ad-hoc (overloading, sugar), subtype
  (different behaviour behind one contract), parametric (generics: same behaviour over types).
- **Smells:** type switches repeated across business logic; casting roulette without generics;
  anemic objects that make polymorphism impossible.
- **Apply / don't:** for existing variation points or test seams; not "for the future".
- **Nuance:** conservation of complexity — conditions are *deported* into a factory, where they
  run once at creation. Easy-deletion rule: removing a feature = deleting one file.
  See rule 15 (`if` ≠ evil).

### Abstraction
- **Essence:** a property of thinking, not a fourth mechanism: deliberately ignore 99% of details
  relevant to other tasks. The three mechanisms are tools; abstraction is the result.
- **Smells:** five layers above one `db.save(o)`; empty hierarchies where the real code is one
  line; "precision design" fitted so exactly to today's requirements that it breaks on the first
  change.
- **Apply / don't:** a class with real logic goes behind an interface (testability, DI,
  Proxy/Decorator later). One implementation behind an interface is NOT the anti-pattern;
  meaningless hierarchies are.

---

## 2. GRASP (Larman) — who owns this responsibility?

GRASP diagnoses (*what* is wrong), SOLID prescribes (*how* to cut the dependency). On conflict,
SOLID usually wins; violate GRASP consciously.

### Information Expert
- **Essence:** responsibility goes to the class holding the needed data; experts delegate to each
  other (`Sale.total()` → `LineItem.subtotal()` → `Product.price`).
- **Smells:** train wrecks `a.getB().getC().getPrice()`; services looping over another object's
  internals.
- **Apply / don't:** computations and validation over own data — yes; pulling DB/HTTP into an
  entity to be "expert" — no.
- **Nuance:** rich model vs simple domain model. Enterprise often keeps logic in services
  ("modern proceduralism": entities + services + DI) to avoid 100k-line entity files (DirecTV
  case). See rule 14 (DTO ≠ evil). Even then keep **Smart Data**: entities reject invalid state (age −500, order without a
  date). Active Record is a perfect Expert that violates SRP — a conscious trade.

### Creator
- **Essence:** whoever owns/aggregates an object creates it (`order.add_item(product, qty)`).
  `new`/constructor call = marriage to a concrete class.
- **Smells:** constructors of concrete classes scattered through services and controllers.
- **Apply / don't:** newables — yes; injectables — DI container; creation needing DB/API/heavy
  computation — factory (Creator yields to SRP).
- **Nuance:** frameworks hide creation behind reflection and proxies, but the rule "the aggregator
  creates" still holds.

### Controller
- **Essence:** first object behind the UI/transport that receives a system event (HTTP, CLI,
  cron tick, message) and delegates to the domain. A receptionist.
- **Smells:** business logic in controllers (untestable, not reusable from CLI/queue); logic in
  a cron entry script.
- **Apply / don't:** facade controller first; split into use-case handlers (CQRS command
  handlers) when it needs ~10 dependencies.
- **Nuance:** the controller is a breakwater: absorbs concurrency and transport formats so the
  domain stays stateless and transport-agnostic. Ideal size: 1–3 lines.

### Low Coupling + High Cohesion
- **Essence:** minimise what classes know about each other; keep related logic together.
  Inseparable — moving one moves the other.
- **Smells:** "monolithicity" — cannot extract one class for a test without dragging the world
  (not the same as a monolith deployment, which is often fine). Classes that are grab-bags.
- **Apply / don't:** always as evaluation metrics; 100% cohesion = one-line classes and file
  explosion, find the balance.
- **How to measure:** coupling by blast radius (rule 9), cohesion semantically (rule 10).
- **Nuance:** under load, break cohesion deliberately — `YouTubeUser` must not load all videos
  to check a login → lazy loading (ORM proxy). Low coupling also shrinks the context an AI agent
  needs to change code safely.

### Polymorphism (GRASP)
- **Essence:** when behaviour varies by type, assign it to the types.
- **Smells:** the same type conditions in many places; deleting a feature means hunting ifs.
- **Apply / don't:** real multi-dimensional variation — yes; "pattern-itis" and soft-coding — no.

### Pure Fabrication
- **Essence:** an invented, non-domain class created to keep cohesion/coupling (Repository, DAO).
- **Smells:** `Utils`, `Helpers`, `Manager` dumping grounds.
- **Apply / don't:** technical responsibilities — yes. Before creating a helper, look for the
  missing domain object: `DateUtils.is_between` → `DateRange.contains`.

### Indirection
- **Essence:** put an intermediary (interface, adapter, proxy) between two classes to cut the
  direct dependency.
- **Nuance (interview classic):** Indirection = mechanism (what) → DIP = principle (which way
  arrows point) → IoC = style (who controls flow) → DI container = tool (who builds objects).
  Superpower: interception — caching, logging, auth inserted via Proxy/Decorator without edits.
- **Apply / don't:** when one of the reasons in rule 5 holds, add the interface now — it costs
  two clicks; otherwise it is ceremonial. Layered "lasagna" in mature systems is fine when each
  layer protects something. Container ≠ design: rule 12.

### Protected Variations
- **Essence:** identify the unstable point, encapsulate it behind a stable interface.
- **Apply / don't:** only for the reasons in rule 5; otherwise it's Speculative Generality
  ("don't be a fortune teller", rule 7).
- **Nuance:** axes of change reveal themselves after 6–12 months; PV is the strategy, OCP is the
  test of the result ("father and son").

---

## 3. SOLID

### SRP — Single Responsibility
- **Essence:** one reason to change = one axis of change from one actor (source of
  requirements). Not "does one thing".
- **Smells:** a class mixing tax rules, discount rules and external API parsing; 2000-line God
  object with constant merge conflicts; the opposite — atomised one-action classes.
- **Nuance:** well-decomposed classes are easy to name. Active Record (Django, Laravel) violates
  SRP consciously for speed; move to Data Mapper when the system grows.

### OCP — Open/Closed
- **Essence:** add behaviour with new code, don't modify old code. Modern mechanism: polymorphism
  via interfaces (Martin), not inheritance (Meyer).
- **Smells:** hardcoded type branches edited for every new variant.
- **Apply / don't:** strictness by maturity stage (rule 7). Never for things that don't change
  (circle area).
- **Nuance:** a simple factory with a local `match` is an acceptable localised violation.
  OCP guarantees you don't break old code, not that new code is correct — that's LSP.

### LSP — Liskov Substitution
- **Essence:** subtypes are substitutable without breaking the program. Logical contract >
  signatures. Design by Contract: don't strengthen preconditions, don't weaken postconditions,
  keep invariants.
- **Smells:** `isinstance`/downcasting; `NotImplementedError` stubs; subtype returning values
  the caller never expected (negative shipping cost used as a discount → negative order total —
  "Mykola syndrome"). Square extends Rectangle.
- **Nuance:** LSP makes OCP safe. In duck-typed languages only tests enforce it — use contract
  tests run against every implementation.

### ISP — Interface Segregation
- **Essence:** the interface belongs to the client; don't depend on what you don't use.
- **Smells:** one fat interface where an operator console could call factory-reset; "Extract
  interface" on a whole class under deadline; universal REST payloads for all clients.
- **Apply / don't:** split by client role (`SensorReadout`, `SensorCalibration`,
  `SensorFactoryReset`); don't split cohesive pairs (`open`/`close`).
- **Nuance:** also applies to APIs (DTO per client, BFF, GraphQL). SRP cleans the inside, ISP
  cleans the storefront.

### DIP — Dependency Inversion
- **Essence:** business policy doesn't depend on technical details; both depend on abstractions
  owned by and phrased in the language of the business.
- **Smells:** infra imports in services; vendor/tech names in contracts (`save_to_postgres`,
  `send_via_smtp`); interfaces duplicating one implementation 1:1 inside a stable flow.
- **Apply / don't:** interfaces at system edges immediately; inside stable flows only per rule 5.
- **Nuance:** DI ≠ DIP (rule 12). Layers only frame DIP; direction of imports must be guarded on
  every change — ideally by an import/layer check in CI.

---

## 4. How they relate

- GRASP names the responsibility problem; SOLID gives the cut; GoF gives ready-made shapes.
- PV ≈ OCP; Indirection → DIP; Pure Fabrication serves SRP; Polymorphism → OCP; LSP makes OCP
  safe; ISP + SRP are the inside/outside of the same concern.
- Nearly everything reduces to: find the real axis of change, isolate it, don't isolate what
  doesn't move.