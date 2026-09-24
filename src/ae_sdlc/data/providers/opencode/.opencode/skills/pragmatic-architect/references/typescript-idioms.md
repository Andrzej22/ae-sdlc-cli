# TypeScript idioms (TS 5.x strict, Node/NestJS backend, React frontend)

General design rules live in SKILL.md "Core stance" (referenced here as "rule N"); this file only
maps them onto TypeScript.

Contents: 1. Mapping table · 2. Project layout (backend, frontend) · 3. Snippets ·
4. TypeScript-specific pitfalls

## 1. Mapping table

| Concept | Idiomatic TypeScript |
|---|---|
| Interface / port | `interface` or `type` (structural). Interfaces are erased at runtime → in NestJS inject via an `InjectionToken`/`Symbol` or use an abstract class as the token |
| Smart Data / invariants | class with private constructor + `static create()` that validates; `readonly` fields; parse at the boundary with zod/valibot ("parse, don't validate") |
| Value object / typed ids | branded types (`type OrderId = string & { readonly __brand: 'OrderId' }`), small immutable classes (`Money`, `DateRange`) |
| Money | integer minor units (`bigint`/`number` cents) or a decimal library — never floating `number` for amounts |
| Static factory | `static of(...)`, `static fromDto(...)` |
| Simple factory / registry | `const handlers = {...} satisfies Record<Kind, Handler>` — the compiler forces a handler for every kind |
| Builder | usually unnecessary: options object with optional props and defaults; keep builders for test data (`aUser().withRole('admin').build()`) |
| Singleton | ES module instance (module cache); in NestJS default provider scope is singleton |
| DI / composition root | NestJS modules/providers (`useClass`, `useFactory`, tokens); outside Nest — a manual `container.ts`, or tsyringe/inversify if really needed |
| Strategy | plain functions / `Record<Kind, Fn>`; class only with state or several methods |
| State | discriminated unions + exhaustive `switch`; class-per-state for rich behaviour; XState for complex workflows |
| Template Method | abstract class with a concrete driver method; or a higher-order function taking the variable steps |
| Decorator (GoF) | wrapper class implementing the same interface; TS `@decorators` are metadata/AOP hooks, not GoF Decorator |
| Proxy / lazy | lazy getters, memoized loaders, ORM lazy relations; ES `Proxy` only when unavoidable (magic, hard to trace) |
| Middleware pipeline | Express/Koa/Fastify middleware; NestJS: middleware → guards → interceptors → pipes → handler → interceptors → exception filters |
| Request-scoped state | `AsyncLocalStorage` (nestjs-cls), never module-level variables |
| Observer / events | explicit event dispatcher, `EventEmitter` (synchronous!), RxJS; brokers across services |
| Command / CQRS | command objects + handlers (`@nestjs/cqrs`), BullMQ jobs |
| Iterator | generators, async generators, `for await…of`, streaming query results |
| Visitor | discriminated unions + exhaustive `switch` with a `never` check, or `ts-pattern` |
| Prototype | `structuredClone` (drops class prototypes and functions!), explicit `clone()` on classes; spread is shallow |
| Flyweight | shared frozen objects, `Map` caches keyed by intrinsic state |
| Unit of Work / Identity Map | MikroORM (real UoW + Identity Map), TypeORM `EntityManager`/`QueryRunner`, Prisma `$transaction` (no identity map) |
| Traits / mixins | mixin functions exist; prefer composition (rule 13) |
| LLM provider port | domain-named interface (`TicketClassifier`), adapter wraps the SDK (Anthropic SDK, Vercel AI SDK); zod schema validates structured output in the adapter |
| Architecture tests | `dependency-cruiser`, `eslint-plugin-boundaries`, Nx module boundaries in CI; `tsc --noEmit` strict; contract tests over all implementations; `expectTypeOf` for type-level contracts |

## 2. Project layout

**Backend (NestJS or plain Node)** — default suggestion, adapt to the project:
```
src/
  domain/          # entities, value objects, domain errors — no framework/ORM imports
  application/     # use cases, ports (interfaces + injection tokens)
  infrastructure/  # ORM repositories, HTTP/LLM clients, publishers (adapters)
  api/             # controllers, request/response DTOs (zod/class-validator)
  main.ts / app.module.ts  # composition root
```
Import rule: `api → application → domain`, `infrastructure → application/domain`, never
`domain → outside`. Enforce with dependency-cruiser or eslint-plugin-boundaries.

**Frontend (React)** — feature-oriented:
```
src/
  features/<feature>/
    ui/            # components: presentation only
    model/         # pure domain logic, types, state machines — no React imports
    api/           # server calls, query hooks (TanStack Query), DTO mapping
  shared/          # design system, generic utilities with a clear concept
```
Mapping to GRASP: components are the view; custom hooks act as controllers (thin: wire UI to
model/api); business rules live in `model/` as pure functions or small classes. A component full
of business rules is a fat controller. Server state (TanStack Query) and client state are
different concerns — don't mirror server data into a global store.

## 3. Snippets

Port + token + adapter + thin NestJS controller:
```ts
// application/ports.ts
export interface OrderRepository {
  get(id: OrderId): Promise<Order | null>;
  add(order: Order): Promise<void>;
}
export const ORDER_REPOSITORY = Symbol('OrderRepository');

// application/place-order.ts
@Injectable()
export class PlaceOrder {
  constructor(
    @Inject(ORDER_REPOSITORY) private readonly orders: OrderRepository,
    @Inject(PAYMENT_GATEWAY) private readonly payments: PaymentGateway,
  ) {}
  async execute(cmd: PlaceOrderCommand): Promise<OrderId> {
    const order = Order.create(cmd.customerId, cmd.items); // Creator + invariants
    await this.payments.charge(order.total, cmd.paymentToken);
    await this.orders.add(order);
    return order.id;
  }
}

// api/orders.controller.ts
@Post()
place(@Body() body: PlaceOrderDto) {
  return this.placeOrder.execute(toCommand(body));
}
```

Exhaustive union instead of Visitor / type switches:
```ts
type Shape =
  | { kind: 'circle'; r: number }
  | { kind: 'rect'; w: number; h: number };

function area(s: Shape): number {
  switch (s.kind) {
    case 'circle': return Math.PI * s.r ** 2;
    case 'rect':   return s.w * s.h;
    default: { const _exhaustive: never = s; return _exhaustive; }
  }
}
```
Adding a variant becomes a compile error at every place that must handle it — a controlled blast
radius instead of a silent miss.

Strategy registry checked by the compiler:
```ts
const discounts = {
  vip: (total, customer) => total.percent(15),
  blackFriday: (total) => total.percent(10),
} satisfies Record<DiscountKind, DiscountPolicy>;
```

LLM output validated at the adapter:
```ts
const ClassificationOut = z.object({ category: z.enum(['billing', 'bug', 'other']) });

export class AnthropicTicketClassifier implements TicketClassifier {
  async classify(text: string): Promise<TicketCategory> {
    const raw = await this.callWithTimeoutAndRetry(text);
    return ClassificationOut.parse(JSON.parse(raw)).category; // untrusted input
  }
}
```

## 4. TypeScript-specific pitfalls

- **Structural typing makes substitutability too easy.** Any object of the right shape "fits",
  so LSP is checked only by shape. Use branded types for ids and value objects, and contract
  tests for behaviour.
- **`as` casts and `any` are downcasting in disguise** (LSP smell). Prefer `unknown` + narrowing
  or schema parsing.
- **Interfaces don't exist at runtime:** DI by interface needs tokens; `instanceof` checks against
  interfaces are impossible and against classes break across bundles/realms.
- **`EventEmitter` is synchronous** — listeners run inside `emit()`, so a setter that emits can
  trigger an Observer cascade on the caller's stack. Likewise chained `useEffect`s in React.
- **Floating promises** hide failures; enable `@typescript-eslint/no-floating-promises`.
- **Module-level mutable state** is shared across requests and, in serverless, across warm
  invocations — a data-leak risk. Use `AsyncLocalStorage` for request state.
- **Spread is a shallow copy;** `structuredClone` loses class prototypes. Clone domain objects via
  their own `clone()`.
- **Barrel files (`index.ts` re-exports)** hide real dependencies, create import cycles and widen
  blast radius. Keep them at package/feature boundaries only.
- **ORM types leaking into the domain** (Prisma-generated types, TypeORM entities with
  decorators) are vendor coupling with a wide blast radius; map at the repository.
- **Duplicate validation layers** (class-validator DTO + zod + entity checks) drift apart; decide
  which layer owns which invariant (rule 14).
- **Legacy vs standard decorators:** `experimentalDecorators` (NestJS, TypeORM) and TC39 decorators
  differ; don't mix them in one codebase.
- **Enums:** prefer string-literal unions or `as const` objects; they work with exhaustive checks
  and don't generate runtime code surprises.
- **Global stores as God objects:** a single Redux/Zustand store coordinating everything becomes a
  bloated Mediator; split by feature.