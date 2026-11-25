## Baseline AppWorld solution

**Status**: active

### Objective

Select, implement, and evaluate a credible baseline agent for the requirements in `../constitution-spec.md`: high AppWorld task success, useful error awareness, consistency across repeated runs, and timely final responses. The investigation should identify the smallest well-supported design that can be implemented and measured, while preserving the constitution's reusable, benchmark-independent decision boundary.

### Current constraints

- Follow `../constitution-spec.md` for requirements, preferences, layered architecture, and acceptance through the authoritative `appworld` profile.
- Use Pydantic AI and Pydantic models where applicable; keep AppWorld protocol integration separate from the reusable decision engine.
- Do not use reference answers, private evaluator data, benchmark-specific solvers, fixed model seeds, or disabled continuous batching.
- Plan to use self-hosted `qwen/qwen3.5-9b`; an A10 server will be made available when the serving and evaluation plan is ready.
- Preserve public-task confidentiality boundaries and never commit credentials or private validation data.
- Use partial validation for iteration and full validation only for the authoritative acceptance result.

### Current state

The solution repository contains the constitution and terminology but no real agent implementation. A dedicated current validator is running locally, its complete configuration is discoverable, and its independently provisioned real runtime passed the no-op/isolation/evaluation smoke test. A temporary two-repeat no-op solution then exercised installation, protocol finalization, self-assessment, AppWorld setup/cleanup, and all four metric paths without infrastructure failures. Public research has identified interactive code ReAct as the provisional policy and the related reliable-agent repository as a source of generic infrastructure patterns. The A10 serving configuration and staged train/dev experiment gates are defined; GPU access is the next external requirement. No model-backed AppWorld episodes have run, so task success, calibration, consistency, latency, and resource use remain unknown.

### Contexts

| ID | Solution implementation | Validation setup | Runtime and environment | Baseline |
|----|-------------------------|------------------|-------------------------|----------|
| C1 | No implementation; constitution at the merged project baseline | `agent-validation` AppWorld support at commit `cc09c57` during initial contract review | Local CPU-only investigation host; A10 model server not yet provisioned | No solution measurements |
| C2 | No implementation; configuration discovery only | Current validator commit `cc09c57` serving `appworld` at `127.0.0.1:8681` | Local CPU-only investigation host; no AppWorld episodes or model provider | Successful discovery, no solution measurements |
| C3 | No solution implementation; validator's real runtime smoke only | Validator commit `cc09c57` with independently provisioned pinned AppWorld runtime/data | Local CPU-only investigation host; Landlock-enforced worker and separate evaluator; no model provider | Provisioning and real no-op/isolation/evaluation smoke passed |
| C4 | Temporary no-op contract probe at `1dd90cfeff38ea2e52dfeb8fc58347efeff46e7b` | One train task, two repeats, all four criteria; partial validation on current local validator/runtime | Local CPU-only host; no model provider; zero environment actions | Run `20261125T071128-376661Z-347b316f96f5`: all metrics computed; AC1 valid, other criteria accepted |
| C5 | Reactive baseline commit `553ad3efc09aa8507777b100ec17d9d21aadf487` | Train `82e2fac_1`, one repeat, AC1/AC2/AC7; current validator and pinned AppWorld runtime | Qwen3.5-9B pinned revision on vLLM 0.34.0/A10; solution/validator on local host; 12K context | Run `20261125T111854-666528Z-19438b25db85`: timely controlled final, zero actions, no task success |
| C6 | Hardened baseline commit `324649fb66267e54f18fea960680cd8902cb317e` | Local unit/integration/build/wheel checks; no provider in standard tests | Python 3.12; Pydantic AI 2.66 and 2.74; clean non-editable wheel environment | 148 tests on each version, Ruff/build/pip checks and clean wheel episode passed |

### Current understanding

#### Supported by evidence

- The constitution requires a layered design and permits either caller-controlled interaction or a complete task runner; validation uses caller-controlled interaction.
- The validator starts `python -m reliable_agent_appworld` once per episode and exchanges versioned JSON Lines messages over standard input/output. It owns AppWorld code execution and private scoring.
- AppWorld exposes one `execute_code` tool backed by a stateful Python shell. Public task data includes the instruction, supervisor information, and application descriptions; private evaluation data must not reach the solution.
- The registered AppWorld profile covers unperturbed repeated episodes and evaluates overall success, error awareness, consistency, and timely responses. Numeric thresholds and the full population are obtained from the validator rather than duplicated here.
- The existing local validation endpoints on ports 8678–8680 do not expose the `appworld` profile; a separate current validation instance is required for our runs ([local discovery](evidence/baseline-local-services.json)).
- The independently provisioned AppWorld runtime has the expected task counts, and the validator's real smoke test passed initialization, no-op execution, private-data read denial, separate evaluation, evidence retention, and cleanup without a model provider ([runtime smoke](evidence/appworld-runtime-smoke.json)).
- Success requires both the AppWorld `complete_task` call and a separate protocol final, followed by independent private evaluation. A controlled unsuccessful final with a strict boolean error flag is fully measurable; the real no-op probe exercised this path and all four criteria without infrastructure errors ([contract probe](evidence/appworld-contract-probe.json)).
- The solution is installed non-editably and launched in an empty workspace. It cannot rely on source-checkout paths, AppWorld imports, private feedback, or unbounded standard output/error ([contract findings](evidence/validator-contract-findings.md)).
- The related reliable-agent implementation provides reusable protocol/runner/model/logging patterns and regression tests, but its default PDDL planning, formal goal machinery, fixed sampling, and unbounded payload logging are not suitable for this baseline ([reuse findings](evidence/reliable-agent-reuse-findings.md)).
- The official Qwen checkpoint and stable vLLM release support the required reasoning/tool parsers. A BF16 12K-context launch is a measurable A10 starting point, while official sampling must be set explicitly and no seed fixed ([serving plan](evidence/qwen-a10-serving-plan.md)).

#### Tentative or unresolved

- Interactive code ReAct is the provisional policy because it supports public intermediate observations and recovery with the fewest components, but its advantage for Qwen3.5-9B is unmeasured. Structured decisions versus fenced-code extraction and the value of a fixed public demonstration still require direct comparison ([policy research](evidence/appworld-policy-research.md)).
- The amount of API discovery, observation retention, context management, and completion checking needed by the baseline is unknown.
- A reliable method for calibrated `likely_incorrect` flags is not yet established; conservative deterministic failure flags and model assessment need comparison.
- Model serving parameters, practical episode latency, prompt size, output-token limits, and A10 memory use require measurement. Prior short-context PDDL traffic is only compatibility evidence, not an AppWorld serving or quality baseline.
- The local validation base URL and runtime are established; operational A10 provider settings remain pending.

### Experiments and decisions

| ID | Idea or question | Context | Result and evidence links | Decision and rationale |
|----|------------------|---------|---------------------------|------------------------|
| E1 | What exact process, message, tool, timing, and evidence contract must the baseline satisfy? | C1 | Static source/test review established installation, protocol, completion, timing, budgets, and failure semantics: [contract findings](evidence/validator-contract-findings.md). | Require an installed-package subprocess harness, strict typed messages, one action per turn initially, an absolute deadline/final reserve, bounded logs, and controlled failure finals. |
| E2 | Can an existing local validation service run the AppWorld profile? | C1 | All three discovered endpoints returned HTTP 404 for AppWorld configuration discovery: [local discovery](evidence/baseline-local-services.json). | Leave those services unchanged. Start a separate current AppWorld-capable instance when the implementation is ready for contract tests. |
| E3 | Can the current validator expose its authoritative AppWorld configuration through the documented HTTP boundary? | C2 | Discovery succeeded and returned a complete editable configuration plus matching schema: [discovery summary](evidence/appworld-discovery-summary.json). | Use this dedicated service for investigation-stage discovery. Select partial configurations by modifying the complete returned copy; do not duplicate its thresholds in solution documentation. |
| E4 | Can the pinned AppWorld runtime be provisioned from the detached current source without an explicit source-path override? | C2 | No; the first attempt failed before creating a runtime because Python imported the older installed PDDL-only validator: [attempt 1](evidence/appworld-provisioning-attempt1.json). | Treat this as an invocation error, not a validator defect. Repeat with `PYTHONPATH` pinned to the detached current source and verify imported module paths. |
| E5 | Does the corrected provisioning produce a usable, isolated real AppWorld runtime? | C3 | Provisioning succeeded with the exact split counts, and `tests/test_appworld_real.py` passed: [runtime smoke](evidence/appworld-runtime-smoke.json). | Use this runtime for local protocol and partial-validation experiments. No GPU is needed for the contract harness; keep the public/private execution boundary unchanged. |
| E6 | Does a minimal installed solution traverse the real two-repeat validation lifecycle with computable criteria? | C4 | Yes. Run `20261125T071128-376661Z-347b316f96f5` completed with no infrastructure failures; AC1 was valid and AC2/AC3/AC7 accepted: [contract probe](evidence/appworld-contract-probe.json). Run directory: `/tmp/opencode/appworld-validation-artifacts/20261125T071128-376661Z-347b316f96f5`. | Keep a controlled `completed=false`, `likely_incorrect=true` fallback. Treat this only as plumbing evidence and proceed to model-backed policy experiments rather than tuning against the no-op score. |
| E7 | Which official AppWorld policy is the smallest credible general-purpose baseline? | C1 | Public pinned-release and paper research favored interactive code ReAct; FullCodeReflexion is a later ablation, while PlanExec and native iterative function calls add premature complexity: [policy research](evidence/appworld-policy-research.md). | Provisionally choose reactive one-code-step ReAct. Do not adopt official deterministic decoding/fixed seeds, direct world access, or automatic termination; compare output mechanisms and prompts on Qwen before finalizing the spec. |
| E8 | What can be reused from the related reliable-agent without importing PDDL policy or known failure modes? | C1 | Source, tests, review history, and validation records identified reusable contracts/runner/protocol/model/logging patterns and specific race/deadline/log-size cautions: [reuse findings](evidence/reliable-agent-reuse-findings.md). | Port minimal generic infrastructure with the final-write fix and absolute deadlines. Build a fresh reactive engine and AppWorld prompt; omit planning/voting/formal-goal/transition-learning components. |
| E9 | What exact A10 model-serving configuration should be qualified? | C1 | Official model/vLLM documentation and prior short-context PDDL evidence support a pinned BF16 Qwen checkpoint, vLLM 0.34.0, 12K initial context, explicit Qwen sampling, and staged concurrency qualification: [serving plan](evidence/qwen-a10-serving-plan.md). | Request an exclusive 24 GiB A10 with Docker/NVIDIA access and Hugging Face connectivity. Keep the endpoint behind an SSH tunnel; record actual serving identities and treat every capacity flag as provisional until S0 passes. |
| E10 | How should model-backed candidates be compared without test-set tuning or incomplete-screen bias? | C1 | A staged S0–S6 matrix defines synthetic serving checks, train smoke, dev-only candidate screens, two-repeat confirmation, and eventual full validation: [experiment plan](evidence/baseline-experiment-plan.md). | Use completed prespecified populations and all four criteria when repeats permit. Start with structured decisions and concise ReAct; add fallback/output/prompt complexity only when its gate is triggered. |
| E11 | Is `a10-oct-22` ready for the planned serving qualification? | C1 | The A10 is idle, CUDA 13.2 is reported, passwordless sudo works, and the exact Qwen revision is cached. Docker is absent and only 4.3 GiB is free. Existing vLLM 0.32.0 was left unchanged: [preflight](evidence/a10-preflight.json). | Reuse the cached model and existing assets where appropriate. Complete the disk audit and request approval before any cleanup; do not require the originally assumed 100 GB because the model is already present. |
| E12 | Which server cleanup can provide sufficient space without deleting any current model or serving environment? | C1 | `/home` holds 122G, including an 89G Hugging Face cache. Twelve shared blobs totaling 30.84 GiB reference only removed 27B/35B model paths; pip cache holds 6.76 GiB and an old CUDA installer 3.85 GiB: [disk audit](evidence/a10-disk-audit.json). | Request approval for these three low-risk scopes totaling 41.45 GiB. Preserve all current model caches and existing environments; revalidate blob references immediately before deletion. |
| E13 | Did the approved cleanup reclaim space without damaging cached models or the existing environment? | C1 | All three approved scopes were revalidated and deleted. The server now has 47G available, every current model snapshot has zero broken symlinks, `pip check` passes, and the driver remains healthy: [cleanup result](evidence/a10-cleanup-result.json). | Proceed with a dedicated AppWorld serving environment. Do not remove any additional assets; the reclaimed space is sufficient. |
| E14 | Does the pinned A10 server support the baseline's generation and structured-output contracts? | C1 | vLLM 0.34.0 launched the exact cached revision. Ten OpenAI probes completed and all semantic assertions passed, including tool continuation, multiline Unicode code, streaming, and JSON Schema. Pydantic AI 2.66 returned a valid structured action without a seed: [S0 results](evidence/a10-s0-basic-results.json). | Keep structured output as primary and JSON text as fallback. Suppress Pydantic AI's stdout banner and test that regression. Continue near-context/concurrency qualification; basic probe success is not AppWorld quality evidence. |
| E15 | Can the server handle large prompts and short concurrency 1/2/4 batches? | C1 | All requests passed with valid outputs, no OOM/crash/5xx, and no length truncation. Concurrency 4 completed eight short requests in 6.734 seconds; 9,538- and 11,020-token JSON Schema prompts both succeeded: [load results](evidence/a10-load-results.json). | Retain 12K and allow concurrency qualification through 4. A longer soak and real AppWorld behavior remain separate gates; do not infer task quality from synthetic load. |
| E16 | Does the first real baseline commit execute its public AppWorld task successfully? | C5 | Run `20261125T111854-666528Z-19438b25db85` completed with computable AC1/AC2/AC7 but zero actions and no success. Public reproduction showed Qwen encoding the nested action object as a JSON string: [S2 result](evidence/appworld-s2-first-result.json). | Keep the negative run. Parse internal action/arguments JSON object strings, clarify preloaded objects/API Docs, and rerun the exact new commit. The controlled error flag worked but is not task-solving evidence. |
| E17 | Does the hardened implementation satisfy local packaging and provider-independent quality gates? | C6 | Both Pydantic AI 2.66 and 2.74 passed 148 tests. Ruff, pip checks, sdist/wheel builds, packaged resources, and a clean non-editable wheel episode without `PYTHONPATH` passed: [quality results](evidence/local-quality-results.json). | Local AC1–AC4/AC7 are satisfied pending CI. Use the clean wheel path as a regression gate for every later review change. |

### Next candidates

- [x] Reconstruct the validator's exact AppWorld solution lifecycle and configuration-discovery workflow from the current source and tests.
- [x] Study the related reliable-agent implementations for reusable protocol, state-management, and self-assessment patterns without importing benchmark-specific policy.
- [x] Study official AppWorld examples and public task/API mechanics to identify the smallest general-purpose interaction loop.
- [x] Select interactive code ReAct as the provisional baseline and prove the real validator lifecycle with a no-op contract probe.
- [x] Define the exact A10 launch configuration and staged train/dev decision gates.
- [x] Obtain A10 access and complete initial GPU/host inventory.
- [ ] Qualify model identity, sampling, structured output, context, and concurrency.
- [x] Complete the A10 disk audit and identify specific cleanup candidates.
- [x] Obtain cleanup approval before deleting anything; then verify reclaimed space and preserved model integrity.
- [ ] Compare structured decisions versus fenced-code extraction and concise prompting versus one fixed public demonstration on prespecified public partial-validation tasks.
- [ ] Select prompt/history/self-assessment settings from completed repeat populations, then create the implementation spec with explicit decision gates.

### Linked specs

- `../specs/01-baseline-agent-general-spec.md` defines the initial reactive implementation, local acceptance checks, and model-backed smoke/dev diagnostics.
