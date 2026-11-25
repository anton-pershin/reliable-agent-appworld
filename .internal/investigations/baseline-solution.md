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

### Next candidates

- [x] Reconstruct the validator's exact AppWorld solution lifecycle and configuration-discovery workflow from the current source and tests.
- [x] Study the related reliable-agent implementations for reusable protocol, state-management, and self-assessment patterns without importing benchmark-specific policy.
- [x] Study official AppWorld examples and public task/API mechanics to identify the smallest general-purpose interaction loop.
- [x] Select interactive code ReAct as the provisional baseline and prove the real validator lifecycle with a no-op contract probe.
- [x] Define the exact A10 launch configuration and staged train/dev decision gates.
- [ ] Obtain A10 access; qualify model identity, sampling, structured output, context, and concurrency.
- [ ] Compare structured decisions versus fenced-code extraction and concise prompting versus one fixed public demonstration on prespecified public partial-validation tasks.
- [ ] Select prompt/history/self-assessment settings from completed repeat populations, then create the implementation spec with explicit decision gates.

### Linked specs

- None yet.
