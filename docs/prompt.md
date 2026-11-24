> **English** | [简体中文](prompt_zh.md)

# Prompt System (core/prompt)

Three-zone prompt assembly, agent (persona) storage, history compression, context fragments and token counting.

## Modules

| File | Responsibility |
|------|----------------|
| `assembler.py` | v5 Stateful Prompt Engine: zoned assembly of the final system/messages |
| `document.py` | `PromptDocument`: provider-neutral prompt representation (AST) |
| `loader.py` | Centralized file-based prompt loading: plain text under `boar/prompts/`, loaded as constants at import time |
| `fragments.py` | Context Fragment interface: composable Layer 4/5 context sources |
| `agent.py` | `AgentConfig` + `AgentRegistry` (multi-agent support) |
| `agent_store.py` | `AgentStore`: folder-based agent persistence |
| `compressor.py` | Conversation history compression (Insert-then-Compress) |
| `assistant_compress.py` | Assistant response compressor: compact "context version" |
| `tool_result_trimmer.py` | WOTR: deterministic inline tiered retention of tool results |
| `task_state.py` | Structured conversation state management |
| `context_detector.py` | Context window detection (read from data, never guessed) |
| `token_counter.py` | Accurate pre-call estimation with tiktoken |
| `cache.py` | Session-level prompt prefix cache |
| `timer.py` | Idle compression timer |

## Prompt assets

- All prompt text lives in `boar/prompts/` (`system_core.md`, `compression/compress_instruction.md`, `compression/summary_template.md`, `extension/*.md`); prompt text is never hardcoded in py files; directory discovery walks up from this module to the project root, including the pip-install parent directory.
- Runtime system injection is **append-only**: after the stable prefix (agent/tools/config) only appends happen, the front is never rewritten — prefix drift costs every downstream turn its cache hit; drift itself is published as an observability event.

## Agents (personas)

- One directory per agent: `<data>/agents/{name}/` with `soul.md` (pure Markdown persona: Identity + Behavior Rules, the two Zone-1 texts) + `agent.yaml` (all structured fields).
- `AgentConfig` is a **flat model**: no inheritance chain between agents; persona and behavior rules are fully independent.
- Concrete agent names must never appear in core code; "there must always be one agent" is guaranteed by the API layer; the default is decided by the `is_default` flag.
- Agent deletion goes through the recycle bin (soft delete).

## Compression

- History compression uses Insert-then-Compress; the result is written to `session.compressed_summary` and **must never enter history as a message** (the gate/trigger lives in the session run subsystem; instructions and templates live in `boar/prompts/compression/`).
- Tool-result trimming is deterministic tiered inline retention (WOTR); it never asks the model again.
- Idle compression is timer-driven and never runs synchronously inside a conversation turn.

## Context window

The window size is a vendor fact: prefer the user's `context_windows` override in `llm.yaml`, then the model catalog; detection logic never hardcodes model names.
