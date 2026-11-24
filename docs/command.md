> **English** | [简体中文](command_zh.md)

# Command System (core/command)

Two unrelated domains live here: in-session (chat) `/commands`, and server process management for the entry point. **There is no injected shell command and no terminal command registry.**

## Modules

| File | Responsibility |
|------|----------------|
| `registry.py` | In-session command registry: `/commands` contributed by core and extensions (`CommandInfo` with source ownership) |
| `chat.py` | Chat command interception and execution — the whole command runtime |
| `dispatcher.py` | Server process management behind the `main.py` entry (stop / restart / status); not a command system |
| `bootstrap.py` | Single source of the directory layout: every entry subcommand calls `create_directories` |

## Chat command semantics

- A message whose text is a single `/token` (optionally followed by arguments) that resolves to a registered command is intercepted BEFORE any LLM run.
- `handler` kind: executed in-process; the output is persisted as the assistant reply; the LLM is never involved.
- `prompt` kind: the template is expanded with the raw argument text and the result REPLACES the message content, which then flows through the normal run pipeline (reaching the LLM by design).
- The matcher is deliberately conservative: `/path/to/file` never matches (the token must be followed by whitespace or end-of-text); an unregistered `/token` falls through to the model as ordinary text.
- All three message surfaces — the WebUI WS `session.run.create` path, REST run creation and the ChannelHost turn pipeline — share the matching/expansion/persistence implementation here, keeping a single source of truth.

## Extension contributions

Extensions register via the facade's `api.register_command(name, handler, description, aliases=..., kind=..., template=...)`; the facade records ownership and sweeps on unload (a command taken over by someone else is not dropped on their behalf).

## Process management

`cmd_stop / cmd_restart / cmd_status` back the `boarai` console subcommands; on restart the re-spawn picks the channel-appropriate entry shape and passes the data-dir argument through.
