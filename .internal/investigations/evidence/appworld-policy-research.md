# AppWorld baseline policy research

## Durable sources

- Official repository at the validator's pinned release: [`StonyBrookNLP/appworld@66ad809`](https://github.com/StonyBrookNLP/appworld/tree/66ad8099e12188ece0d3fe45e661dbc01880813b).
- Pinned [minimal agent notebook](https://github.com/StonyBrookNLP/appworld/blob/66ad8099e12188ece0d3fe45e661dbc01880813b/notebooks/minimal_agent.ipynb), [ReAct prompt](https://github.com/StonyBrookNLP/appworld/blob/66ad8099e12188ece0d3fe45e661dbc01880813b/experiments/prompts/react.txt), and [ReAct controller](https://github.com/StonyBrookNLP/appworld/blob/66ad8099e12188ece0d3fe45e661dbc01880813b/experiments/code/recoma/react_controller.py).
- [AppWorld paper](https://arxiv.org/html/2407.18901v1), including baseline designs and failure analysis.
- Current official simplified agents were reviewed for ideas, but their `0.2.0.dev0` APIs are not drop-in compatible with `0.1.3.post1`.

## Candidate comparison

| Candidate | Evidence and strengths | Costs/risks | Investigation decision |
|-------------|------------------------|-------------|------------------------|
| Interactive code ReAct | Official strongest overall GPT-4o baseline; observes intermediate state; natural error recovery; needs no solution demonstrations or API predictor | Many model calls, long contexts, formatting/repetition risk | **Provisional baseline.** Smallest general policy with direct observation-driven recovery. |
| FullCode + Reflexion | Best open-model policy in the original paper; few model calls | Upfront retrieval omissions, whole-program retries repeat persistent writes, strongest released version uses training-solution demonstrations | First later ablation, not the initial implementation. |
| Plan & Execute | Explicit decomposition and reusable subtask state | More calls/parsers; stale plans; weak open-model results | Reject for baseline complexity. |
| Iterative function calling | Structured independent calls and efficient batching | Poor fit for dependent code/loops; model-specific tool parser risk | Reject initially; AppWorld already exposes stateful code rather than individual APIs. |

The paper's historical scores are context, not predictions: ReAct GPT-4o reached 48.8 TGC on Test-Normal, while tested open models were much lower and FullCodeReflexion sometimes surpassed ReAct. This supports a measurable baseline increment rather than assuming immediate project acceptance.

## Baseline mechanisms supported by public evidence

- Use just-in-time public API Docs discovery rather than injecting the complete documentation.
- Preserve one stateful execution session per episode and explicitly print values needed for subsequent decisions.
- Use small validated changes, inspect results, paginate completely, use environment time, and avoid unrelated writes.
- Start with one fixed public onboarding demonstration if model experiments show it helps; correct errors in the released example rather than copying it blindly.
- Bound context by trimming old observations first while retaining the actual instruction, actions, and recent interactions. The official 20,000-character threshold is a candidate to measure, not a chosen constant.
- Start model experiments around 50 interactions, but enforce a solution deadline and final-response reserve before either interaction or time exhaustion.
- Track formatting failures, repeated code, missing `complete_task`, incomplete answers, observation truncation, and timeouts as diagnostic events.

## Required adaptations to this project

- The solution cannot call `world.execute` or `world.task_completed()` directly. It requests `execute_code` through the validator and decides from public observations.
- AppWorld `complete_task` is not sufficient termination; the agent must also emit the protocol final with self-assessment.
- The official minimal notebook's deterministic decoding and fixed seed are **not adopted**. Our constitution requires serving/model-card default sampling, explicitly forbids fixing the seed, and requires continuous batching to remain enabled.
- Native model tool parsing and fenced-code extraction are both candidate output mechanisms, not decisions settled by the official implementation. Compare schema-guided structured decisions against simple code-block parsing on the actual Qwen server.
- Do not copy current-main AppWorld APIs, oracle retrieval, dynamic test-specific demonstrations, private evaluator feedback, rollback, or benchmark-specific solvers.

## Model-backed decision experiments

1. **Output contract:** compare structured action/final decisions with fenced-code extraction on the same public tasks; measure malformed outputs, repair calls, latency, and completion behavior.
2. **Prompt/context:** compare a concise generic ReAct prompt with one fixed public demonstration; measure success, interactions, context size, and repetition.
3. **History control:** test observation-first trimming against unbounded history on tasks that require API discovery and pagination.
4. **Self-assessment:** compare model-only assessment with deterministic conservative overrides for explicit failures, exhaustion, and unverified completion; evaluate precision and recall without private feedback in the loop.
5. **Repeat behavior:** repeat every compared configuration without fixing a model seed and retain all runs before selecting a baseline.
