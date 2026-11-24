"""
Configuration schema definitions for BoarAi system.

This module defines the configuration schema and validation rules.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

class ConfigFormat(str, Enum):
    """Supported configuration file formats."""
    YAML = "yaml"
    TOML = "toml"
    JSON = "json"

from boarai.core.logging.config import LogLevel  # noqa: E402

@dataclass
class LLMProviderConfig:
    """Configuration for a single LLM provider."""
    name: str
    provider_type: str = "openai"  # "openai" or "anthropic"
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    default_model: Optional[str] = None
    supported_models: List[str] = field(default_factory=list)
    model_info: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    """Per-model metadata: {"model_name": {"context_window": 128000, ...}}"""
    timeout: float = 300.0
    """Idle read timeout (seconds) between streamed chunks / body reads. NOT a
    total request budget — long generations and long thinking phases are fine as
    long as the provider keeps emitting data (mirrors deepseek-harness)."""
    max_retries: int = 3
    options: Dict[str, Any] = field(default_factory=dict)

@dataclass
class LLMConfig:
    """LLM configuration schema."""
    default_provider: str = ""
    default_model: str = ""
    providers: Dict[str, LLMProviderConfig] = field(default_factory=dict)
    capability_models: Dict[str, Dict[str, str]] = field(default_factory=dict)
    context_windows: Dict[str, int] = field(default_factory=dict)
    """Global context window sizes (tokens) for models, keyed by model name.

    Merged with provider builtin defaults. Users can add/override entries
    in llm.yaml under the top-level ``context_windows`` key.
    Example:
        context_windows:
          gpt-4o: 128000
          deepseek-v4-flash: 1048576
    """
    vision: Dict[str, Any] = field(default_factory=dict)
    """History-image replay governance (llm.yaml top-level ``vision`` section).

    Keys (missing key = default; invalid value = replay disabled):
        replay_recent_images: int = 3    — newest N image attachments are
            re-injected as pixels on every turn; older ones degrade to the
            cached sidecar description (or a readable-path note).
        replay_max_bytes: int = 12582912 — per-run byte budget for the
            re-injected pixels; over-budget images degrade oldest-first.
    Example:
        vision:
          replay_recent_images: 3
          replay_max_bytes: 12582912
    """
    timeout: float = 300.0
    """Idle read timeout (seconds) between streamed chunks / body reads."""
    max_retries: int = 3
    stream: bool = False

    def get_provider_config(self, provider_name: str) -> Optional[LLMProviderConfig]:
        """Get configuration for a specific provider."""
        return self.providers.get(provider_name)

    def add_provider_config(self, config: LLMProviderConfig) -> None:
        """Add or update provider configuration."""
        self.providers[config.name] = config
        if not self.default_provider:
            self.default_provider = config.name

    def remove_provider_config(self, provider_name: str) -> bool:
        """Remove provider configuration."""
        if provider_name not in self.providers:
            return False
        del self.providers[provider_name]
        if self.default_provider == provider_name:
            self.default_provider = next(iter(self.providers)) if self.providers else ""
        return True

@dataclass
class PluginConfig:
    """Plugin configuration schema."""
    enabled: bool = True
    directories: List[str] = field(default_factory=lambda: ["plugins", ".boarai/plugins"])
    excluded: List[str] = field(default_factory=list)
    strict_hooks: bool = False
    """When True, an extension whose declared hooks never bind (unknown point)
    fails to load instead of loading with a silent no-op hook."""
    pip_auto_install: str = "system"
    """Auto-install policy for extension pip dependencies (supply-chain guard).

    - "off": never auto-install; a missing dependency fails the load with a
      clear error asking the user to install it manually.
    - "system": auto-install only for built-in system-layer extensions
      (trusted); user/local extensions must be installed manually or the
      policy changed. Recommended default.
    - "all": auto-install for every layer (legacy permissive behavior).
    """

@dataclass
class SessionConfig:
    """Session configuration schema."""
    persist: bool = True
    storage_path: str = ".boarai/sessions"
    compression_threshold: float = 0.70
    """Context usage ratio threshold for auto-compression (0.50-0.80).

    After 300s idle, compression only fires if context_usage / context_window
    exceeds this threshold. Default 0.70 = 70% of context window.
    """
    default_workspace_mode: str = "session_dir"
    """Default workspace mode for new sessions:
    - 'session_dir': data_dir/sessions/{session_id}/workspace (default)
    - 'shared_workspace': all sessions share data_dir/workspace
    - 'custom': use default_workspace_path
    """
    default_workspace_path: str = ""
    """Custom default workspace path when default_workspace_mode='custom'"""
    default_approval_mode: str = "ask"
    """Tool-approval mode for NEWLY CREATED sessions.

    - ``deny``: only readonly/safe tools run; everything else is auto-rejected.
    - ``ask``: dangerous tools prompt (category gating, no looseness shortcuts).
    - ``allow``: no prompts (the absolute floor still denies).

    Only ``deny``/``allow`` are snapshotted into
    ``session.security_overrides.approval_mode`` at creation (the fork path
    included); they are explicit user intent and therefore supersede
    ``tool_guard.auto_approve`` for new sessions. ``ask`` (the default) and
    unknown values stamp NOTHING — such sessions follow the global
    ``tool_guard.approval_mode`` fallback, so changing the global mode later
    keeps affecting them. A later change of this setting only affects sessions
    created after it.
    """

from boarai.core.logging.config import LoggingConfig  # noqa: E402

@dataclass
class SystemConfig:
    """System configuration schema."""
    name: str = "BoarAi"
    version: str = "0.1.0"
    debug: bool = False
    data_dir: str = ".boarai"
    temp_dir: str = ".boarai/temp"

# ==================== Security Config ====================

@dataclass
class PathGuardConfig:
    """Path sandbox configuration (v1.14 two-floor model)."""
    workspace_root: str = "auto"  # "auto"=project root, or explicit path
    allow_external_write: bool = False
    allow_external_read: bool = True
    # Absolute write floor: OS-bricking dirs + the security config itself
    # (protect-the-protector — if the agent could rewrite these it could
    # delete its own floor). Denied for write/delete in EVERY mode.
    absolute_write_paths: List[str] = field(default_factory=lambda: [
        "/etc/", "/boot/", "/usr/lib/systemd/", "/usr/share/", "/usr/bin/",
        "/sbin/", "/lib/", "/lib64/",
        "C:/Windows/System32/", "C:/Windows/SysWOW64/",
        ".boarai/config/",
    ])
    # Confirm-tier READ paths (credentials only — reads are otherwise free).
    # Prompted under "ask", waived under "allow", denied under "deny".
    forbidden_read_paths: List[str] = field(default_factory=lambda: [
        ".boarai/credentials",
        "~/.ssh/", "~/.aws/", "~/.kube/", "~/.docker/",
        "~/.gitconfig", "~/.npmrc",
        "**/*.key", "**/id_rsa", "**/id_ed25519",
        "**/*.pem", "**/*.ppk",
        # Credential / secret files (defense-in-depth, NOT a hard boundary —
        # the same-user process can read them by other means; the real
        # exfiltration gate is network approval + audit redaction).
        "**/.git-credentials", "~/.netrc", "**/.pypirc",
        "**/auth.json", ".boarai/config/llm.yaml",
        # WebUI access gate: the key hash is useless without the key, but the
        # signing secret beside it is what forges cookies — keep both behind
        # the confirm tier so an agent cannot quietly read them out.
        ".boarai/config/access.yaml", ".boarai/config/access.secret",
    ])
    # Confirm-tier WRITE/DELETE paths (sensitive but recoverable).
    forbidden_write_paths: List[str] = field(default_factory=lambda: [
        "~/.ssh/", "~/.aws/", "~/.kube/", "~/.docker/",
        "**/.env", "**/.env.*",
        ".boarai/sessions/", ".boarai/agents/",
        "**/.git-credentials", "~/.netrc", "**/.pypirc",
        "**/auth.json",
    ])

@dataclass
class ShellSafetyConfig:
    """Shell command safety configuration."""
    backend: str = "local"
    """Shell execution backend: "local" (the host) or "docker".

    An unknown value fails loudly at startup rather than falling back to
    local, because a deployment that asked for containment and silently got
    the host is a security failure, not a convenience.
    """
    docker_image: str = "python:3.12-slim"
    """Image for ephemeral ``docker run`` execution."""
    docker_container: str = ""
    """Named long-lived container to ``docker exec`` into; empty = ephemeral."""
    docker_workdir: str = "/work"
    """Working directory inside the container."""
    docker_mounts: List[str] = field(default_factory=list)
    """``-v`` specs. Any entry makes the backend host-exposed, which removes
    the containment-based approval relaxation."""
    docker_network: bool = False
    """Allow container network access. Off by default: a command that can
    reach the network can exfiltrate whatever it can read."""

    deny_patterns: List[str] = field(default_factory=lambda: [
        # ---- Absolute floor: machine-destroying, NO recovery path. These are
        # the ONLY commands hard-denied in EVERY mode — including a session
        # set to "allow". All four reference projects keep exactly
        # this class of block under their most permissive mode (hermes
        # hardline survives yolo; codex Never escalates to Forbidden).
        r"rm\s+-rf\s+/",
        r"mkfs\b",
        r"dd\s+.*\bof=/dev/sd",  # destructive side is of=, not if=
        r">\s*/dev/sd",
        r"format\s+[A-Za-z]:", r"cipher\s+/w:",
        # Redirect/tee into OS-critical dirs — the shell counterpart of the
        # file-tool absolute_write_paths floor (openclacky blocks > /etc, /usr,
        # /bin statically). Keeps shell and file tools consistent: a session
        # "allow" never bricks the OS via either path.
        r"(?:>>?|1>|2>)\s*/(?:etc|boot|usr|bin|sbin|lib)/",
        r"\btee\s+(?:-a\s+)?/(?:etc|boot|usr|bin|sbin|lib)/",
        r"(?:>>?|1>|2>)\s*[A-Za-z]:\\Windows\\System32",
        r":\(\)\s*\{[^}]*\};?",  # fork bomb
        r"kill\s+-9\s+-1",
        # NOTE (v1.13 removals → confirm_patterns): shutdown/reboot,
        # home-dir recursive wipes, net user /delete, credential injection
        # and credential reads are system-affecting but RECOVERABLE — they
        # prompt in on/smart mode and are waived under a session "allow"
        # (the user, not the framework, makes that call).
    ])
    # v1.13 confirm floor: system-affecting but recoverable. A hit returns
    # requires_approval=True (never a hard deny): the approval mode decides —
    # "on"/"smart" prompts the user, session "off"/"deny" waives it (F1).
    # Matches the codex semantic: dangerous commands Prompt instead of
    # Forbidden unless the user opted into a prompt-free mode.
    confirm_patterns: List[str] = field(default_factory=lambda: [
        # System state (recoverable by reboot/re-create)
        r"shutdown|reboot|halt|poweroff",
        r"net\s+user\s+\S+\s+/delete\b",
        # Home-dir recursive wipes (user's own data — the session owner
        # may waive this under "allow"; hermes hardline is stricter)
        r"rm\s+-rf\s+~", r"rm\s+-rf\s+\$\{?HOME\}?",
        r"\brd\s+/s\b.*~",
        r"Remove-Item\s+(?=[^;|&]*(?:~|\$HOME|%USERPROFILE%|\$env:USERPROFILE))(?=[^;|&]*(?:-Recurse|-r\b|-ri\b|-ra\b))",
        # Credential injection via redirect (recoverable: delete the line)
        r"(?:>>?|1>|2>)\s*~/\.(?:ssh|aws|kube|docker)/",
        # Credential reads (pure read — exfiltration risk, user decides).
        # `[^;&|]*` tolerates flags between the verb and the path.
        r"(?:^|[\s;|&])(?:cat|type|Get-Content|gc)\s+[^;&|]*~/\.(?:ssh|aws|kube|docker)/",
        r"(?:^|[\s;|&])(?:cat|type|Get-Content|gc)\s+[^;&|]*\.boarai[/\\]config[/\\]llm\.yaml",
    ])
    readonly_patterns: List[str] = field(default_factory=lambda: [
        # v1.12.1 adversarial hardening: every auto-allow verb below is
        # COMMAND-SAFE ONLY — no flag/argument of the verb can execute
        # arbitrary code or write files. Verbs that can (`env CMD`,
        # `java -jar`, `find -delete/-exec`, `sort -o`, `go env -w`,
        # `date -s`, `hostname NAME`, `ip link set`) are narrowed or
        # removed so they fall through to the approval prompt.
        r"^ls\b", r"^pwd\b", r"^cat\b", r"^grep\b",
        r"^echo\b", r"^head\b", r"^tail\b", r"^wc\b",
        r"^cd\b",  # directory change: no side effect
        # find: exclude mutating primaries (-delete/-exec/-ok/-fprint*)
        r"^find\s+(?!.*(?:-delete|-exec\b|-execdir\b|-ok\b|-okdir\b|-fprint|-fls\b))",
        # sort: whitelist flags without 'o' (output) and bare filename args
        r"^sort(?:\s+(?:-[runhbdfgkVMStzm]+|[^\s-]\S*))*$",
        # git read verbs, tolerating leading option+value pairs (git -C path status)
        r"^git\s+(?:-{1,2}[\w-]+\s+\S+\s+)*(?:status|diff|log|show|branch|remote)\b",
        r"^pip\s+list", r"^npm\s+list",
        r"^which\b", r"^type\b", r"^file\b", r"^stat\b", r"^du\b", r"^df\b",
        r"^where\b", r"^whoami\b",
        r"^Get-ChildItem", r"^Get-Content", r"^Select-String",
        # --- v1.12: high-frequency side-effect-free probes (auto-allow) ---
        # Toolchain / version probes. Version flags must TERMINATE the
        # command (\s*$): we never rely on the tool's own "print & exit"
        # semantics, so `java --version evil.jar` asks instead of allowing.
        r"^python[23]?\s+(?:--version|-V)\s*$",
        r"^python[23]?\s+-m\s+pip\s+(?:list|show)\b",
        r"^pip[23]?\s+(?:--version|-V)\s*$",
        r"^pip[23]?\s+(?:list|show)\b",
        r"^node\s+(?:--version|-v)\s*$",
        r"^npm\s+(?:--version|-v)\s*$",
        r"^npm\s+(?:list|ls|config\s+get)\b",
        r"^yarn\s+(?:--version|-v)\s*$",
        r"^yarn\s+list\b",
        r"^pytest\s+(?:--version|-V)\s*$",
        r"^java\s+(?:-version|--version)\s*$",
        r"^go\s+(?:version\b|env\s+(?!-w\b))",
        r"^rustc\s+--version\s*$",
        r"^cargo\s+(?:--version|-V)\s*$",
        r"^cargo\s+tree\b",
        r"^mvn\s+-version\s*$", r"^gradle\s+--version\s*$", r"^dotnet\s+--version\s*$",
        r"^docker\s+(?:ps|images|version|info)\b",
        r"^kubectl\s+(?:get|describe|version|config\s+view)\b",
        # Environment / system state (bare or display forms only)
        r"^env\s*$", r"^printenv\b",
        r"^date\b(?![^|;&]*(?:\s-s\b|--set\b))",
        r"^uname\b", r"^hostname\s*$",
        r"^ps\b", r"^tree\b", r"^diff\b", r"^cmp\b",
        r"^md5sum\b", r"^sha1sum\b", r"^sha256sum\b",
        r"^ip\s+(?:-br\s+|-brief\s+)?(?:a(?:ddr)?|r(?:oute)?|link)\s*(?:$|show\b)",
        r"^netstat\b",
        r"^tasklist\b", r"^systeminfo\b",
    ])
    # Hard cap for a single shell command's model-requested timeout. The
    # shell tool clamps its ``timeout`` argument into [1, max_command_timeout]
    # so commands like ``npm install`` / test suites can legitimately run for
    # minutes without the model being able to park a run forever.
    max_command_timeout: float = 600.0
    # When True, absolute path arguments outside project root are rejected.
    # v1.12: default False — external absolute paths now fall through to an
    # approval prompt (codex/deepseek let sandboxed runs reference any path;
    # we have no OS sandbox, so the prompt is the gate). Set True to restore
    # the old hard-deny behavior.
    block_external_absolute_paths: bool = False
    # When True, shell commands targeting an external network host (anything
    # beyond localhost/loopback) require approval even if otherwise readonly.
    # Default True. Local/loopback traffic is always allowed.
    network_approval: bool = True

@dataclass
class ToolGuardConfig:
    """Tool permission configuration."""
    # Default approval mode applied to NEWLY CREATED sessions (v1.15). A
    # session snapshots this at creation into security_overrides.approval_mode;
    # existing sessions keep their own value. "on" | "off" (smart removed).
    approval_mode: str = "on"
    approval_timeout: float = 30.0
    auto_approve: bool = False
    max_tool_calls_per_run: int = 0  # 0 = unlimited tool calls per run
    max_consecutive_errors: int = 5
    # "warn" (default) = fail-open: streak only attaches a hint, never blocks.
    # "block" = fail-closed: deny after max_consecutive_errors (opt-in).
    error_streak_policy: str = "warn"
    readonly_tools: List[str] = field(default_factory=list)
    safe_tools: List[str] = field(default_factory=list)
    dangerous_tools: List[str] = field(default_factory=list)
    forbidden_tools: List[str] = field(default_factory=list)
    # Pending-approval wait: how long a run may stay suspended waiting for a
    # user decision before auto-rejecting (fail-closed). The legacy 30s
    # approval_timeout terminated runs while the user was simply away from
    # the keyboard; 10 minutes is the new default.
    pending_timeout: float = 600.0
    # Unattended runs (no WS audience for the run/session): how a gated tool
    # batch resolves instead of hanging until pending_timeout.
    #   "ask"     — legacy behavior: wait for a (possibly absent) user
    #   "deny"    — reject immediately (audited), the model gets the reason
    #   "approve" — allow immediately (audited)
    unattended_policy: str = "ask"
    # NOTE (v1.15 removals): smart_approval_enabled / smart_approval_model
    # (auxiliary-LLM triage) and granted_signatures (persistent global grants)
    # were removed — smart approval was never wired to a working aux LLM in
    # practice, and global grants are superseded by per-session approval modes.

@dataclass
class AuditConfig:
    """Security audit configuration."""
    enabled: bool = True
    log_path: str = ".boarai/logs/security.log"
    events: List[str] = field(default_factory=lambda: [
        "tool_execute", "tool_deny", "path_block", "approval", "pip_install",
    ])

@dataclass
class WebAccessConfig:
    """WebUI access gate: one access key, N device sessions."""
    session_ttl_seconds: int = 604800  # 7 days
    session_ttl_max_seconds: int = 2592000  # 30 days
    sliding: bool = False
    trust_loopback: bool = True


@dataclass
class SecurityConfig:
    """Security configuration schema."""
    path_guard: PathGuardConfig = field(default_factory=PathGuardConfig)
    shell_safety: ShellSafetyConfig = field(default_factory=ShellSafetyConfig)
    tool_guard: ToolGuardConfig = field(default_factory=ToolGuardConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    web: WebAccessConfig = field(default_factory=WebAccessConfig)

@dataclass
class ThinkingConfig:
    """Thinking configuration schema."""
    enabled: bool = True
    default_mode: str = "none"  # "none"(auto), "off", "light", "deep", "ultra"
    auto_route: bool = True
    show_reasoning: bool = True

@dataclass
class RunConfig:
    """Single-agent run loop configuration (main path: RunExecutor)."""
    auto_route: bool = True
    max_turns: int = 100
    stagnation_threshold: int = 3
    force_final_on_max: bool = True
    max_input_tokens: int = 0
    """Per-run input-token budget; 0 = unlimited. Reaching the soft ratio first
    forces a final answer, reaching the limit aborts with finish_reason=budget."""
    max_total_tokens: int = 0
    """Per-run input+output token budget; 0 = unlimited."""
    max_cost_usd: float = 0.0
    """Per-run spend budget; 0 = unlimited. Enforced only when the provider
    reports a cost in its usage payload (most do not, so this is usually inert)."""
    budget_soft_ratio: float = 0.8
    """Fraction of a budget at which the run is forced to wrap up (0.1-1.0)."""
    skill_evolution: Dict[str, Any] = field(default_factory=dict)
    """Overrides for the skill evolution worker (enabled, thresholds, and the
    review policy knobs: review_failure_policy / repair_on_invalid /
    pending_max_attempts). Merged over the worker's built-in defaults."""
    readonly_auto_approve: bool = False
    """When True, all-readonly tool batches skip user confirmation."""
    max_concurrent_children: int = 3
    """Per-parent cap on simultaneously running background sub-agents.

    Submissions past the cap are queued rather than rejected, so a parent
    always gets a delegation id back. The cap is per parent session, not
    process-wide: two sessions delegating must not starve each other.
    """

@dataclass
class SkillsConfig:
    """Skill subsystem configuration (hot-refresh watcher)."""
    watch_interval: float = 5.0
    """Seconds between skill-dir signature scans; <= 0 disables the watcher.
    Default matches the extension manager's file-watcher cadence."""

@dataclass
class UpdateConfig:
    """Online update configuration (pip / git / source channels) — core/update/."""
    enabled: bool = True
    auto_check: bool = True
    check_interval_hours: float = 24.0
    cooldown_hours: float = 6.0
    pypi_url: str = "https://pypi.org/pypi/boarai/json"
    pip_extra_args: List[str] = field(default_factory=list)
    git_remote: str = "origin"
    git_tag_prefix: str = "v"
    github_repo: str = ""
    github_proxies: List[str] = field(
        default_factory=lambda: ["https://ghfast.top/", "https://gh-proxy.com/"]
    )
    archive_mirrors: List[str] = field(default_factory=list)
    backup_keep: int = 1

@dataclass
class BoarAiConfig:
    """Main configuration schema for BoarAi system."""
    system: SystemConfig = field(default_factory=SystemConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    plugin: PluginConfig = field(default_factory=PluginConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    thinking: ThinkingConfig = field(default_factory=ThinkingConfig)
    run: RunConfig = field(default_factory=RunConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to a YAML/JSON-safe dictionary."""
        import dataclasses
        from enum import Enum

        def _convert(obj: Any) -> Any:
            if isinstance(obj, Enum):
                return obj.value
            if isinstance(obj, Path):
                return str(obj)
            if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
                return {f.name: _convert(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
            if isinstance(obj, dict):
                return {str(k): _convert(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_convert(v) for v in obj]
            return obj

        return _convert(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BoarAiConfig":
        """Create config from dictionary."""
        config = cls()

        # Update system config
        if "system" in data:
            for key, value in data["system"].items():
                if hasattr(config.system, key):
                    setattr(config.system, key, value)

        # Update LLM config
        if "llm" in data:
            llm_data = data["llm"]
            config.llm.default_provider = llm_data.get("default_provider", "")
            config.llm.default_model = llm_data.get("default_model", "")
            config.llm.timeout = llm_data.get("timeout", 300.0)
            config.llm.max_retries = llm_data.get("max_retries", 3)
            config.llm.stream = llm_data.get("stream", False)
            # Normalize keys to str: bare YAML keys like 5.2 become float otherwise
            raw_cw = llm_data.get("context_windows", {}) or {}
            cw: dict = {}
            if isinstance(raw_cw, dict):
                for k, v in raw_cw.items():
                    key = str(k).strip()
                    if not key:
                        continue
                    try:
                        cw[key] = int(v)
                    except (TypeError, ValueError):
                        continue
            config.llm.context_windows = cw

            # Parse capability model mappings
            cap_data = llm_data.get("capability_models", {})
            if isinstance(cap_data, dict):
                config.llm.capability_models = {
                    k: {"provider": v.get("provider", ""), "model": v.get("model", "")}
                    for k, v in cap_data.items()
                    if isinstance(v, dict)
                }

            # Vision replay governance section (raw dict; the LLM manager
            # validates values so a bad number can never brick loading).
            vision_data = llm_data.get("vision", {})
            config.llm.vision = vision_data if isinstance(vision_data, dict) else {}

            # Parse provider configs - support both dict and list formats
            providers_data = llm_data.get("providers", {})

            if isinstance(providers_data, list):
                # List format: [{name: "openai", type: "openai", ...}, ...]
                for provider_data in providers_data:
                    name = provider_data.get("name", "")
                    if not name:
                        continue
                    raw_options = provider_data.get("options")
                    options_data = raw_options if isinstance(raw_options, dict) else {}
                    provider_config = LLMProviderConfig(
                        name=name,
                        provider_type=(
                            provider_data.get("provider_type")
                            or provider_data.get("type")
                            or options_data.get("type")
                            or "openai"
                        ),
                        api_key=provider_data.get("api_key"),
                        api_base=provider_data.get("api_base"),
                        default_model=provider_data.get("default_model"),
                        supported_models=provider_data.get("supported_models", []),
                        model_info=(
                            provider_data.get("model_info")
                            if isinstance(provider_data.get("model_info"), dict)
                            else {}
                        ),
                        timeout=provider_data.get("timeout", config.llm.timeout),
                        max_retries=provider_data.get("max_retries", config.llm.max_retries),
                        options=options_data,
                    )
                    config.llm.providers[name] = provider_config
            else:
                # Dict format: {openai: {name: "openai", type: "openai", ...}, ...}
                for name, provider_data in providers_data.items():
                    raw_options = provider_data.get("options")
                    options_data = raw_options if isinstance(raw_options, dict) else {}
                    provider_config = LLMProviderConfig(
                        name=provider_data.get("name", name),
                        provider_type=(
                            provider_data.get("provider_type")
                            or provider_data.get("type")
                            or options_data.get("type")
                            or "openai"
                        ),
                        api_key=provider_data.get("api_key"),
                        api_base=provider_data.get("api_base"),
                        default_model=provider_data.get("default_model"),
                        supported_models=provider_data.get("supported_models", []),
                        model_info=(
                            provider_data.get("model_info")
                            if isinstance(provider_data.get("model_info"), dict)
                            else {}
                        ),
                        timeout=provider_data.get("timeout", config.llm.timeout),
                        max_retries=provider_data.get("max_retries", config.llm.max_retries),
                        options=options_data,
                    )
                    config.llm.providers[name] = provider_config

        # Update plugin config
        if "plugin" in data:
            for key, value in data["plugin"].items():
                if hasattr(config.plugin, key):
                    setattr(config.plugin, key, value)

        # Update session config
        if "session" in data:
            for key, value in data["session"].items():
                if hasattr(config.session, key):
                    setattr(config.session, key, value)

        # Update logging config (enums via LoggingConfig.from_dict)
        if "logging" in data and isinstance(data["logging"], dict):
            try:
                config.logging = LoggingConfig.from_dict(data["logging"])
            except Exception:
                for key, value in data["logging"].items():
                    if hasattr(config.logging, key):
                        setattr(config.logging, key, value)

        # Update thinking config
        if "thinking" in data:
            for key, value in data["thinking"].items():
                if hasattr(config.thinking, key):
                    setattr(config.thinking, key, value)

        # Update run (single-agent loop) config
        if "run" in data:
            for key, value in data["run"].items():
                if hasattr(config.run, key):
                    setattr(config.run, key, value)

        # Update skills config (hot-refresh watcher)
        if "skills" in data and isinstance(data["skills"], dict):
            for key, value in data["skills"].items():
                if hasattr(config.skills, key):
                    setattr(config.skills, key, value)

        # Update online-update config (pip / git / source channels)
        if "update" in data and isinstance(data["update"], dict):
            for key, value in data["update"].items():
                if hasattr(config.update, key):
                    if key in ("pip_extra_args", "github_proxies", "archive_mirrors"):
                        value = [str(a) for a in value] if isinstance(value, list) else []
                    elif key == "backup_keep":
                        try:
                            value = max(0, int(value))
                        except (TypeError, ValueError):
                            value = 1
                    setattr(config.update, key, value)

        # Update security config
        if "security" in data:
            sec = data["security"]
            # PathGuard sub-config
            if "path_guard" in sec:
                for key, value in sec["path_guard"].items():
                    if hasattr(config.security.path_guard, key):
                        setattr(config.security.path_guard, key, value)
            # ShellSafety sub-config
            if "shell_safety" in sec:
                for key, value in sec["shell_safety"].items():
                    if hasattr(config.security.shell_safety, key):
                        setattr(config.security.shell_safety, key, value)
            # ToolGuard sub-config
            if "tool_guard" in sec:
                for key, value in sec["tool_guard"].items():
                    if hasattr(config.security.tool_guard, key):
                        setattr(config.security.tool_guard, key, value)
            # Audit sub-config
            if "audit" in sec:
                for key, value in sec["audit"].items():
                    if hasattr(config.security.audit, key):
                        setattr(config.security.audit, key, value)
            # WebUI access gate
            if "web" in sec and isinstance(sec["web"], dict):
                for key, value in sec["web"].items():
                    if hasattr(config.security.web, key):
                        setattr(config.security.web, key, value)

        return config

    def validate(self) -> List[str]:
        """Validate configuration."""
        errors = []

        # Validate system config
        if not self.system.name:
            errors.append("System name cannot be empty")

        # Validate LLM config
        if self.llm.timeout <= 0:
            errors.append("LLM timeout must be positive")

        if self.llm.max_retries < 0:
            errors.append("LLM max_retries cannot be negative")

        # Validate logging config
        if not isinstance(self.logging.level, LogLevel):
            errors.append(f"Invalid logging level: {self.logging.level}")

        # Validate shell pattern lists compile. A typo used to be swallowed at
        # match time (`except re.error: continue`), which silently deleted a
        # floor while the config still looked accepted — the worst possible
        # failure mode for a deny list.
        errors.extend(self._validate_shell_patterns())

        return errors

    def _validate_shell_patterns(self) -> List[str]:
        """Every shell pattern must compile; report field + index + reason."""
        import re as _re

        shell = getattr(self.security, "shell_safety", None) or getattr(
            self.security, "shell", None
        )
        if shell is None:
            return []
        errors: List[str] = []
        for field_name in ("deny_patterns", "confirm_patterns", "readonly_patterns"):
            patterns = getattr(shell, field_name, None) or []
            for index, pattern in enumerate(patterns):
                try:
                    _re.compile(pattern)
                except _re.error as exc:
                    errors.append(
                        f"security.shell.{field_name}[{index}] is not a valid "
                        f"regex ({exc}): {pattern!r}"
                    )
        return errors

# Default configuration
DEFAULT_CONFIG = BoarAiConfig()
