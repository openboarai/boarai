> **English** | [简体中文](process_zh.md)

# Process Management (core/process)

Server process detection: **port-authoritative**, no PID files.

## Principle

- The listener on the server port is the single source of truth: whoever holds it is the candidate, and its command line must prove it is a BoarAI server entry (`_cmdline_is_boar`) before the runtime reports "running" or kills anything.
- **No PID file, no lock file**: both go stale after a crash and would authorise a wrong kill.

## Entry-shape recognition

The command line is matched against three server entry shapes (lowercased, basename-anchored):

| Shape | Example |
|-------|---------|
| Source tree | `python <root>/main.py start` |
| pip module | `python -m boarai start` |
| Console script | `.../boarai[.exe] start` |

Basename anchoring guarantees that a checkout path merely *containing* "boarai" (or a test runner like `tests/run.py`) never impersonates the server; the `-m` shape requires the bare package name so `-m boarai.core.update.worker` (the updater subprocess) is never mistaken for a server.

## Consumers

The entry's stop / restart / status subcommands and the restart's verified-kill fallback all go through `ProcessManager` (`get_process_manager()`); on restart the re-spawn picks the entry shape matching the current channel and passes the data-dir argument through.
