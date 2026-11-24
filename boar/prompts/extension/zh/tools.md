# 工具扩展开发（tools）

> 用途：开发者中文参考版；LLM 消费英文版，如有出入以英文版为准。
> 位置：`boar/prompts/extension/tools.md`（英文权威源）

## 注册方式 A：`@tool` 装饰器（推荐，现有工具全用此法）

```python
from boarai.core.extension.tool import tool, ToolParameter, SecurityProfile, SuggestedCategory

@tool(name="my_tool",
      description="what it does (<= 60 chars, injected into LLM tool schema)",
      parameters=[ToolParameter(name="path", type="string", required=True, description="file path")],
      security_profile=SecurityProfile.FILE_READ,   # none/file_read/file_write/file_delete/shell
      path_param="path",                             # 哪个参数是文件路径（走 PathGuard）
      suggested_category=SuggestedCategory.READONLY) # readonly/safe/dangerous
async def my_tool(ctx, **kwargs) -> dict:
    path = kwargs["path"]
    ...
    return {"ok": True, "result": "..."}
```

**签名铁律**：handler 恒为 `async def xxx(ctx, **kwargs)` —— 首参永远是 `ctx`（ToolContext），其后才是工具参数。写成 `async def xxx(path: str)` 会缺 ctx 直接失败。

放置位置（对应 import 语义）：
- **user 层**：`@tool` 直接写在 `extension.py` 顶部 —— 模块加载即注册，`initialize` 无需做事
- **system 层**：可拆独立 `tools.py`，在 `initialize` 里 `from boar.extensions.tools.my_ext import tools  # noqa: F401` 触发注册（该 import 仅 system 层可用）

## 注册方式 B：manifest `contributes.tools`（user 层多文件推荐）

```yaml
contributes:
  tools:
    - id: my_tool
      handler: code/tools.py#my_tool   # "文件#函数"，相对扩展目录解析
      description: "what it does"
      security_profile: file_read
      schema:
        path: {type: string, description: "file path", required: true}
```

handler 为纯 `async def my_tool(ctx, **kwargs)`（**无**装饰器），loader 按声明注册。

## 安全档位选择

| security_profile | 场景 | 生效的守卫 |
|---|---|---|
| none | 纯计算/查询 | — |
| file_read / file_write / file_delete | 文件操作（配合 `path_param`） | PathGuard 防穿越 |
| shell | 执行命令 | ShellGuard（deny/allowlist） |

`suggested_category`（readonly/safe/dangerous）影响审批流：readonly 批量可自动放行（配置 `run.readonly_auto_approve`），dangerous 默认需确认。

## Pitfalls

- `description` ≤60 字符最佳（原样注入 LLM tool schema）
- 返回 dict；错误用 `{"error": "..."}` 而非抛异常
- 漏声明 `security_profile`/`path_param` = 安全检查链对该工具失效
- 同步阻塞逻辑（文件 IO、store 调用）用 `asyncio.to_thread` 包裹
- `contributes.tools` 条目缺 `id` 会被丢弃并打日志告警——写完任何工具都要执行 common.md 的验收节（工具没有 HTTP 列表端点：grep `.boarai/logs/` 并确认下一次 run 的工具列表）

## 参考实现

- `boar/extensions/tools/file_tools/` —— @tool + declare_hook、system 层 import 模式
- `boar/extensions/tools/skill_tools/` —— asyncio.to_thread 包同步 store、事件发布
