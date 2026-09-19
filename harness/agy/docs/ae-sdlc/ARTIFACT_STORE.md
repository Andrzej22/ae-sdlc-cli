# Artifact Store

Conversation memory is temporary. Durable accepted product state lives under `docs/product/`.

## Broad feature

```text
docs/product/epics/EPIC-001-feature/
├── epic.md
└── spec.md
```

One epic with one spec is a valid and preferred shape when the work is cohesive.

## Multiple implementation units

Only when the work is genuinely separable:

```text
docs/product/epics/EPIC-001-feature/
├── epic.md
└── specs/
    ├── SPEC-001-a.md
    └── SPEC-002-b.md
```

## Standalone work

```text
docs/product/standalone/SPEC-010-short-title.md
```

## States

```text
DRAFT
READY
IN_PROGRESS
DONE
BLOCKED
```

`spec` is the executable artifact type. Dependencies reference other spec IDs.
