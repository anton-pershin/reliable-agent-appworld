# Qwen3.5-9B A10 serving plan

## Verified model and serving facts

- Official checkpoint: [`Qwen/Qwen3.5-9B`](https://huggingface.co/Qwen/Qwen3.5-9B), not the Base variant.
- Pin revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a` and record the actually downloaded commit on the server.
- The checkpoint uses BF16 weights, has approximately 17.98 GiB of weight shards, and natively supports a context far beyond what one 24 GiB A10 can serve efficiently.
- Stable serving candidate: [`vLLM 0.34.0`](https://github.com/vllm-project/vllm/releases/tag/v0.34.0), with `qwen3` reasoning parsing and `qwen3_coder` tool parsing.
- A prior local endpoint advertised the alias `qwen/qwen3.5-9b` and `max_model_len=12288`; Pydantic AI tool output worked for short PDDL requests. Those runs did not qualify AppWorld context length, concurrency, quality, or the currently pinned model revision.

## Proposed exclusive-A10 launch

```bash
docker run --rm --name qwen35-vllm \
  --gpus all \
  --ipc=host \
  -p 127.0.0.1:8000:8000 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  vllm/vllm-openai:v0.34.0 \
  --model Qwen/Qwen3.5-9B \
  --revision c202236235762e1c871ad0ccb60c8ee5ba337b9a \
  --host 0.0.0.0 \
  --port 8000 \
  --tensor-parallel-size 1 \
  --dtype bfloat16 \
  --kv-cache-dtype auto \
  --language-model-only \
  --max-model-len 12288 \
  --max-num-seqs 4 \
  --max-num-batched-tokens 4096 \
  --max-cudagraph-capture-size 4 \
  --disable-prefix-caching \
  --generation-config vllm \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder
```

This is a starting candidate, not a qualified configuration. Continuous batching remains enabled. Start without an explicit GPU-memory-utilization override on an exclusive GPU. If memory/graph failures occur, reduce sequences and graph capture to 1–2, then context to 8192; use eager execution only for diagnosis. Do not apply FP8 or AWQ flags to the BF16 checkpoint.

Keep the endpoint bound to loopback and access it from the validation host through an SSH tunnel. Record the container digest, vLLM version, model SHA, driver, all effective engine flags, and GPU memory evidence.

## Sampling and output

The checkpoint has no generation config that can be assumed to supply Qwen's recommendations. Set them explicitly and omit every random seed.

Initial coding/structured-output experiment:

- thinking enabled;
- temperature `0.6`, top_p `0.95`, top_k `20`, min_p `0`;
- presence penalty `0`, repetition penalty `1.0`;
- maximum output tokens `4096` as an initial experiment, not an official default.

Also qualify Qwen's general thinking settings (temperature `1.0`, top_p `0.95`, top_k `20`, presence penalty `1.5`) if coding settings underperform. Send non-OpenAI-standard fields through Pydantic AI `extra_body`. Do not manually replay private reasoning; use the supplied chat template and retain only public responses.

The 12,288 limit includes prompt and generated tokens. Track `finish_reason="length"` explicitly; earlier 2,000-token caps caused failures in the related PDDL solution.

## Server qualification gates

1. Record GPU/driver/other users, startup memory, model/vLLM identities, `/health`, and `/v1/models`.
2. Test thinking/non-thinking, streaming/non-streaming, multi-turn history, and public reasoning/tool markup separation.
3. Test Pydantic AI tool output and JSON-schema text output with nested arguments, multiline Unicode code, invalid-output repair, and truncation detection.
4. Measure realistic AppWorld prompt/history sizes and near-boundary generation.
5. Soak concurrency 1, 2, then 4 for 30–60 minutes; require no OOM/crash/unexplained 5xx and record TTFT, TPOT, throughput, queueing, latency, and peak VRAM.
6. Only after a clean baseline, consider 16K/32K context and repeat quality plus load tests. Do not infer serving throughput from prior episode wall times.

All quality/capacity conclusions must come from new AppWorld runs. Prior PDDL success and latency are not transferable.
