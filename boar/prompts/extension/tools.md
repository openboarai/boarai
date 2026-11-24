# Tool Extension Development (tools)

> Purpose: `file_read` this file when registering LLM-callable tools (read
> common.md first).
> Location: `boar/prompts/extension/tools.md`

## Way A: `@tool` decorator (recommended; used by all existing tools)

```python
from boarai.core.extension.tool import tool, ToolParameter, SecurityProfile, SuggestedCategory

@tool(name="my_tool",
      description="what it does (<= 60 chars, injected into LLM tool schema)",
      parameters=[ToolParameter(name="path", type="string", required=True, description="file path")],
      security_profile=SecurityProfile.FILE_READ,   # none/file_read/file_write/file_delete/shell
      path_param="path",                             # which param is a file path (PathGuard)
      suggested_category=SuggestedCategory.READONLY) # readonly/safe/dangerous
async def my_tool(ctx, **kwargs) -> dict:
    path = kwargs["path"]
    ...
    return {"ok": True, "result": "..."}
```

**Signature rule**: the handler is always `async def xxx(ctx, **kwargs)` —
the first param is always `ctx` (a `ToolContext`), then the tool params.
Writing `async def xxx(path: str)` misses ctx and fails outright.

Placement (maps to the import semantics in common.md):
- **user layer**: put `@tool` directly at the top of `extension.py` —
  registration happens at module load; `initialize` needs to do nothing
- **system layer**: you may also split into `tools.py` and trigger
  registration in `initialize` via
  `from boar.extensions.tools.my_ext import tools  # noqa: F401`
  (that import works only on the system layer)

## Way B: manifest `contributes.tools` (user-layer multi-file)

```yaml
contributes:
  tools:
    - id: my_tool
      handler: code/tools.py#my_tool   # "file#function", resolved against the extension dir
      description: "what it does"
      security_profile: file_read
      schema:
        path: {type: string, description: "file path", required: true}
```

The handler is a plain `async def my_tool(ctx, **kwargs)` (**no** decorator);
the loader registers it from the declaration.

## Choosing the security profile

| security_profile | Scenario | Guard applied |
|---|---|---|
| none | pure computation/queries | — |
| file_read / file_write / file_delete | file operations (with `path_param`) | PathGuard anti-traversal |
| shell | command execution | ShellGuard (deny/allowlist) |

`suggested_category` (readonly/safe/dangerous) drives the approval flow:
readonly batches may auto-pass (config `run.readonly_auto_approve`),
dangerous defaults to confirmation.

## Pitfalls

- `description` ≤60 chars is best (injected verbatim into the LLM tool schema)
- Return a dict; errors as `{"error": "..."}` — never raise
- A missing `security_profile`/`path_param` = the security chain skips the tool
- Wrap blocking logic (file IO, store calls) in `asyncio.to_thread`
- A `contributes.tools` item without `id` is discarded with a log warning —
  after writing any tool, run the Acceptance checks in common.md (no HTTP
  listing exists for tools: grep `.boarai/logs/` and check the next run's
  tool list)

## Reference implementations

- `boar/extensions/tools/file_tools/` — @tool + declare_hook, system-layer import pattern
- `boar/extensions/tools/skill_tools/` — asyncio.to_thread around a sync store, event publishing
