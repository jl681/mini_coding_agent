# Issue 001: Ollama Model-Response Timeout

## Symptoms

The agent listed files and read `shipping.py`, then its next Ollama request timed out—even with a 600-second response timeout. This also happened without MCP. Independent grading never ran, so the reported 0% was an execution failure rather than a judged incorrect repair.

## Confirmed Findings

MCP search works: a direct search for `calculate_shipping` returned the relevant source locations. Ollama is reachable and can run `qwen3:8b` on the GPU.

A reconstructed failing conversation completed in 6.66 seconds with `think: false` and `num_predict: 1024`, returning a valid request to read `policies/order_quote.py`. It used 801 prompt tokens and generated 21 output tokens, ending normally.

## Suspected Cause

Prolonged model thinking or other generation behavior is the leading hypothesis, not a confirmed cause. The agent previously set neither an explicit thinking mode nor an output-token limit. Both settings changed, and Ollama was restarted, before the successful replay.

The Python agent loop coordinates actions and tools; it does not implement the model's internal thinking. There is no evidence that the conversation exceeded context capacity. Context limits concern input size, whereas the suspected problem concerns generation before a response is returned.

## Next Step

Replay the same saved request with thinking enabled while keeping the 1,024-token budget unchanged. Compare completion, timing, and token-limit termination. Then expose thinking and output limits in the agent and verify a complete benchmark run with controlled settings.

Status: one replay succeeds; the root cause and full-run recovery remain unverified. Recorded 2026-11-23.
