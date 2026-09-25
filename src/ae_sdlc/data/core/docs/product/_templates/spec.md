---
artifact_type: spec
work_item_id: <SPEC-ID>
title: <title>
status: DRAFT
epic: <EPIC-ID-OR-EMPTY>
source_of_truth: repository
dependencies: []
---

# Specification: <title>

## Goal
<why this spec exists — problem or opportunity in one short paragraph>

## Outcome
<required observable outcome>

## Requirements
- **R-1:** <requirement>

## Acceptance Criteria
- **AC-1:** <observable criterion>

## Assumptions
- <default or constraint this spec relies on, or None>

## API Surface
| Method & path | Auth / role | Request | Response | Errors |
|---|---|---|---|---|
| <endpoint or "none: internal only"> | | | | |

Client needs checked: CRUD completeness, access management, `my_role`/`permissions`, list filtering/pagination, error codes. Deferred endpoints: <list or none>.

## Design & Boundaries
<!-- via pragmatic-architect -->
- <module/layer ownership, adapter boundaries, invariants — no FastAPI in domain, shared application layer, etc.>

## AI Design
<!-- via ai-engineering-mentor. Delete this whole section if the work is not AI-related. -->
- <LLM/agent/tooling/guardrails/eval concerns for this spec>

## Constraints
- <constraint or None>

## Non-Goals
- <non-goal or None>

## Open Questions
<!-- Non-blocking: does not prevent READY. -->
- <question needing product input later, or None>

## Blocking Questions
<!-- Blocking: the spec cannot be READY until answered. -->
- <question or None>

## Implementation Notes
Only include implementation constraints that are genuinely part of the requirement.
