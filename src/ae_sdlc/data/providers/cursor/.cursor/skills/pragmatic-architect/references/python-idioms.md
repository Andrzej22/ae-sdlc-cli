# Python idioms (Python 3.11+, FastAPI, async SQLAlchemy 2.0)

Contents: 1. Mapping table · 2. Project layout · 3. Snippets · 4. Python-specific pitfalls

## 1. Mapping table

| Concept | Idiomatic Python |
|---|---|
| Interface / port | `typing.Protocol` (structural, no inheritance needed); `abc.ABC` when you need enforced abstract methods or shared template code |
| Smart Data / invariants | `@dataclass(frozen=True, slots=True)` + `__post_init__`; Pydantic models with validators at the boundary |
| Value object | frozen dataclass with behaviour (`DateRange.contains`, `Money.__add__`) |
| Static factory | `@classmethod` constructors (`Money.of(...)`, `Order.create(...)`) |
| Simple factory / registry | `dict[str, Callable[..., T]]` or a function with `match` |
| Builder | usually unnecessary: keyword-only dataclass fields with defaults; keep builders for test data and complex configs |
| Singleton | module-level instance, `functools.lru_cache` on a provider, or app-scoped dependency |
| DI / composition root | FastAPI `Depends` + one module that wires adapters; `app.dependency_overrides` in tests |
| Strategy | plain callables / `Protocol` with `__call__`; class only if it has state or several methods |
| State | state objects, or `Enum` members mapped to handler objects; avoid `if self.status == ...` spread across methods |
| Template Method | `abc.ABC` base with a concrete driver method and abstract steps; resources via context managers |
| Decorator (GoF) | wrapper class implementing the same Protocol; function decorators `@` only for function-level concerns |
| Proxy / lazy loading | ORM relationship loading strategies; `functools.cached_property` |
| Middleware pipeline | ASGI/Starlette middleware, FastAPI dependencies; request state in `request.state` or `contextvars` |
| Observer / events | explicit in-process event dispatcher; broker (Kafka, RabbitMQ, Redis streams) across services |
| Command | dataclass command + handler (CQRS); queue tasks (Celery, arq, Dramatiq) |
| Iterator | generators, async generators (`AsyncIterator`), `yield_per`/streaming results |
| Visitor | `match`/`case` on types, `functools.singledispatch` / `singledispatchmethod` |
| Prototype | `copy.copy` / `copy.deepcopy`, custom `__deepcopy__` |
| Flyweight | `__slots__`, shared immutable objects, interning, caches keyed by intrinsic state |
| Unit of Work / Identity Map | `AsyncSession` (it is both); commit at the use-case boundary, not inside repositories |
| Traits / mixins | small mixins without state; keep MRO shallow |
| LLM provider port | domain-named `Protocol` (`TextSummarizer`, `IntentClassifier`), adapters per provider; Pydantic schema for structured output validated in the adapter; model id/prompt/params from settings |
| Architecture tests | `import-linter` contracts (layers, forbidden imports) in CI; contract tests parametrized over all implementations of a port |

## 2. Hexagonal layout (default suggestion, adapt to the project)

```
app/
  domain/          # entities, value objects, domain services, domain errors — no framework imports
  application/     # use cases (one per action or per aggregate), ports (Protocols)
  infrastructure/  # SQLAlchemy repositories, HTTP/LLM clients, message publishers (adapters)
  api/             # FastAPI routers (controllers), request/response schemas
  bootstrap.py     # composition root: wiring ports → adapters
```
Import rule: `api → application → domain`; `infrastructure → application/domain`;
never `domain → anything outside domain`. Enforce with import-linter if the project allows.

## 3. Snippets

Port + adapter + thin route:
```python
# application/ports.py
class OrderRepository(Protocol):
    async def get(self, order_id: OrderId) -> Order | None: ...
    async def add(self, order: Order) -> None: ...

# application/place_order.py
@dataclass
class PlaceOrder:
    orders: OrderRepository
    payments: PaymentGateway

    async def __call__(self, cmd: PlaceOrderCommand) -> OrderId:
        order = Order.create(cmd.customer_id, cmd.items)   # Creator + invariants
        await self.payments.charge(order.total, cmd.payment_token)
        await self.orders.add(order)
        return order.id

# api/orders.py
@router.post("/orders")
async def place_order(body: PlaceOrderIn, uc: PlaceOrder = Depends(get_place_order)):
    return {"id": await uc(body.to_command())}
```

Strategy as callables with a DI-style registry:
```python
DiscountPolicy = Callable[[Money, Customer], Money]

DISCOUNTS: dict[str, DiscountPolicy] = {
    "vip": vip_discount,
    "black_friday": black_friday_discount,
}
```

LLM behind a domain port, output validated as untrusted input:
```python
class TicketClassifier(Protocol):
    async def classify(self, text: str) -> TicketCategory: ...

class AnthropicTicketClassifier:            # infrastructure adapter
    def __init__(self, client: AsyncAnthropic, settings: LLMSettings): ...
    async def classify(self, text: str) -> TicketCategory:
        raw = await self._call_with_timeout_and_retry(text)   # resilience lives here
        return ClassificationOut.model_validate_json(raw).category  # validate at the edge
```
Domain and use cases see `TicketCategory`, never SDK types; swapping providers or models is a
new adapter or a config change, and an eval suite runs against every adapter.

Stateless middleware (request data in contextvars, never on `self`):
```python
request_id: ContextVar[str] = ContextVar("request_id")
```

## 4. Python-specific pitfalls

- Lazy loading in async SQLAlchemy raises `MissingGreenlet`; choose loading explicitly
  (`selectinload`, `joinedload`) and consider `lazy="raise"` on relationships to surface hidden
  I/O.
- Committing inside repositories breaks Unit of Work; the use case (or a UoW wrapper) owns the
  transaction boundary.
- `Protocol` is not checked at runtime unless `@runtime_checkable`, and even then only method
  presence — LSP is enforced by tests, not the type system.
- Mutable default arguments and class-level mutable attributes create accidental shared state
  (Monostate by accident).
- Module-level singletons are created at import time — keep them free of I/O, or build them in
  the composition root / lifespan handler.
- Multiple inheritance works (C3 MRO), which makes deep mixin stacks tempting; keep them shallow.
- `@dataclass` without `frozen=True` + public fields = getters/setters for everything; decide
  consciously which objects are data and which guard invariants.
- Pydantic models are great boundary DTOs; don't let them become the domain model by default in
  complex domains.