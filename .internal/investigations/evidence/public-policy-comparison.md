# Public AppWorld policy comparison — 2026-11-26

## Sources and boundary

Compared the merged agent with public AppWorld source at commit `66ad8099e12188ece0d3fe45e661dbc01880813b`, public runtime API Docs/Supervisor source, and public validation protocol plumbing. No ground truth, private evaluator report, hidden task metadata, or credentials were used.

## Material differences and decisions

| Finding | Public evidence | Decision |
|---------|-----------------|----------|
| Official controllers stop after actual world completion; the merged engine accepted `completed=true` after any attempt. | `experiments/code/recoma/react_controller.py:114-123`; official ReAct prompt lines 305-337 | Inject an AppWorld completion-evidence predicate into the generic engine and defer a successful final until it is observed. Prototype A/B. |
| The actual instruction was an escaped JSON string nested beside generic instructions. | Official ReAct prompt lines 339-343 renders `Task:` directly | Render `objective` as a first-class field and decode structured public input once for prompting. Prototype B. |
| Action exhaustion could suppress the separate protocol final after a successful last action. | Official loop terminates after completion; baseline runner checked budget before another decision | Permit final-only decisions at action exhaustion and release a deferred final without another provider call. Prototype B. |
| The official ReAct demonstration teaches login, token storage, paginated accumulation, and concise completion answers. | Official ReAct prompt lines 99-177, 280-337 | Add compact general instructions and `search_api_docs`; encourage cohesive read-only batches, complete pagination, and direct answers. Prototype A. Add a worked example only if abstract instructions remain insufficient. |
| Routine official code generation used about 400 output tokens; the baseline allowed 4096 thinking tokens and had no remaining-time prompt signal. | `react_llama3_test_normal.jsonnet:16-27`; serving metrics averaged 276 generated tokens and 10.6 seconds per request | Expose configurable thinking and remaining time. Compare thinking/nonthinking under identical task populations; do not copy official deterministic seeds or disabled batching. |
| Large results were truncated as serialized JSON prefixes, potentially losing trailing errors. | Public baseline `model.py:_attempt_payload`; execution tracebacks put the useful exception at the end | Preserve structured status/error and use head/tail string truncation in the next engineering revision. |
| Total prompt size is not explicitly token-bounded, but observed average prompt size was about 4,002 tokens in a 12K context. | Public serving metrics after the full run | Do not treat context overflow as the main cause without direct evidence. Add deterministic completion visibility and bounded feedback before more complex context machinery. |

## Semantics to preserve in the accuracy spec

- Use high-level `apis.<app>.<api>` calls, not raw `requester.post`.
- Obtain permitted account data through Supervisor APIs; store per-app token strings and pass them according to endpoint docs.
- Paginate from `page_index=0`, retain filters/sorting, accumulate in Python, and print compact relevant evidence.
- Pass a concise scalar `answer` to `complete_task` only for answer-seeking instructions; omit it for action-only instructions.
- Keep AppWorld completion and the protocol final distinct.
- Treat file-system tasks as app API tasks, use environment time rather than model time, and resolve relations through permitted public apps.
- Preserve the layered boundary: completion recognition belongs to integration; the engine depends only on a generic predicate.
