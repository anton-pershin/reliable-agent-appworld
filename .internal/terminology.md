## Terminology

### General

| Term | Definition |
|------|------------|
| Task | An objective supplied to the agent, together with instructions and available information. |
| Episode | One attempt by the agent to complete a task, from task receipt to its final response or termination. |
| Completion declaration | An explicit statement by the agent that it has completed the task. It does not establish that the task was successful. |
| Task success | Satisfaction of the task's correctness conditions, determined independently of the agent's claim of completion. |
| Task success rate | For one fixed task and one evaluation condition, `N_success / N`, where `N` is the number of planned repeated episodes for that task and condition, and `N_success` is the number of those episodes that succeed. Unsuccessful attempts, including crashes and timeouts, remain in the denominator; episodes from different tasks are not combined. |
| Overall success rate | For one evaluation condition, the arithmetic mean of the task success rates across the benchmark task set. Each task has equal weight, regardless of its episode count. |
| Unperturbed evaluation conditions | The original task inputs and prompts, with the configured model and execution settings and without deliberately injected failures. |
| Evaluation run | Execution of a specified set of task episodes under a specified evaluation configuration. |
| Consistency | Stability of overall success rates across repeated evaluation runs under unchanged conditions. |
| Error awareness | The agent's ability to identify its own unsuccessful results, assessed against independently determined task outcomes. |
| Confidence | The agent's reported degree of belief that its answer is correct. It is not an independently verified probability. |
| Likely incorrect flag | The agent's boolean assessment, returned as `likely_incorrect`, that its answer is likely incorrect. |
| Action attempt | One requested operation in an execution environment, including an action-catalog request, a failed or rejected action, or a repeated action. Each action in a batch is one action attempt. |
| End-to-end time | Elapsed time from task receipt to the final response, including self-assessment. |
