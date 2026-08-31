# THIS IS UNDER DEVELOPMENT !!!!
```
CLAUDE OR ANY AI STILL DON'T  UNDERSTAND THE CONTEXT ABOUT HOW TO USE THE TOOL, NO UNLESS IT'S GUIDED AND PLANNED. BE CAREFUL
```



# EPLAN AI Automation Toolkit

**English** | [中文](#eplan-ai-自动化工具包)

A collection of AI-assisted automation tools for **EPLAN Electric P8** and **EPLAN EEC Pro 2026**, built around the Model Context Protocol (MCP).

The repo contains three independent sub-projects: a local MCP server that drives EPLAN P8 directly, and two remote MCP servers hosted on Cloudflare Workers that expose the indexed documentation via semantic search.

> Working with an LLM here? Read [`llm.md`](llm.md) — it explains, in LLM-facing
> terms, everything the toolkit can do and configure.

## Repository Layout

```
.
├── eplan-p8-mcp-server/          # LOCAL: MCP server that controls EPLAN P8
├── cloudflare-rag-eplan-p8/      # REMOTE: Cloudflare Worker that serves the P8 docs RAG over MCP
├── cloudflare-rag-eecpro/        # REMOTE: Cloudflare Worker that serves the EEC Pro docs RAG over MCP
└── claude-skills/                # SKILL: Claude Code skill for EPLAN P8 development
```

| Folder | Type | Purpose | EPLAN product |
|--------|------|---------|---------------|
| `eplan-p8-mcp-server/` | Local Python MCP | Drive a running EPLAN instance from Claude (open/close projects, exports, reports, scripts, etc.) | EPLAN Electric P8 |
| `cloudflare-rag-eplan-p8/` | Remote Cloudflare Worker | Serve the P8 doc index as a remote MCP + REST API | EPLAN Electric P8 |
| `cloudflare-rag-eecpro/` | Remote Cloudflare Worker | Serve the EEC Pro doc index as a remote MCP + REST API | EPLAN EEC Pro 2026 |
| `claude-skills/eplan-development/` | Claude Code skill | Teach Claude to write correct EPLAN scripts, API code, and Remote Client apps (patterns + pitfalls) | EPLAN Electric P8 |

Each sub-project has its own README with installation and usage details.

## What is MCP?

**MCP (Model Context Protocol)** is an open standard that lets AI assistants like Claude interact with external tools and services. Instead of just generating code, Claude can actually *execute* actions in EPLAN in real time and consult documentation through semantic search.

## Quick Start

### Local EPLAN automation (P8)

The local MCP server lets Claude drive a running EPLAN instance. It exposes
**182 tools**: 8 connection/utility tools, **170 EPLAN actions** (`eplan_*`,
every one executed silently inside a C# script under QuietMode — no EPLAN
dialog can block unattended runs), and **4 Asset Administration Shell tools**
(`aas_*`) for AAS/AASX digital-twin export and import. The 170 include 4
live-DataModel tools (`eplan_live_query_functions`, `eplan_live_query_pages`,
`eplan_live_set_function_text`, `eplan_live_set_connection_designations`) that
read and edit the currently open project's object model via runtime
reflection, working around a script-engine limitation on static `using`
directives. Beyond individual actions they also cover the building blocks for
fully unattended develop-deploy-test loops: EPLAN application lifecycle control
(`eplan_app_launch` / `eplan_app_shutdown` / `eplan_app_restart` — exit EPLAN,
swap add-in DLLs, relaunch, reconnect, reopen the project), disposable scratch
project fixtures cloned from a template (`eplan_scratch_project_*`), reading
EPLAN's system message tree (`eplan_get_system_messages` — see the same
errors/warnings the user sees in the GUI), and private extension modules (see
below).

The EPLAN version is **auto-detected**: the server scans
`C:\Program Files\EPLAN\Platform` and targets the newest installed version.
No configuration needed.

```bash
pip install pythonnet mcp
claude mcp add eplan -- python YOURPATH/eplan-p8-mcp-server/mcp_server/server.py
claude mcp list   # should list "eplan"
```

Then start EPLAN, open Claude Code, and say `connect to eplan`. See [`eplan-p8-mcp-server/mcp_server/README.md`](eplan-p8-mcp-server/mcp_server/README.md) for the full guide.

![Claude CLI configured](image.png)

#### Precondition of use

To use remoting, please proceed as follows:

To start Eplan remoting, you must first activate the **Allow remote access via Remote Client** setting. You can do this via GUI in the settings dialog (**File > Settings... > Workstation > Interfaces > Remote access**).

![Allow remote access via Remote Client](Remoting_Setting_AllowLocalAccess.png)

### Remote documentation RAGs (P8 and EEC Pro)

These are already deployed and ready to use — no local data required:

```bash
# EPLAN Electric P8 documentation
claude mcp add eplan-rag -- cmd /c npx mcp-remote https://rag2026.covaga.xyz/mcp

# EPLAN EEC Pro 2026 documentation
claude mcp add eecpro-rag -- cmd /c npx mcp-remote https://rageecpro.covaga.xyz/mcp
```

They also expose a plain REST API (handy for verifying EPLAN action names and
parameters while developing):

```bash
curl -X POST https://rag2026.covaga.xyz/search -H "Content-Type: application/json" \
     -d "{\"query\": \"export project pdf\", \"topK\": 3}"
```

See [`cloudflare-rag-eplan-p8/README.md`](cloudflare-rag-eplan-p8/README.md) and [`cloudflare-rag-eecpro/README.md`](cloudflare-rag-eecpro/README.md) for the tools, REST endpoints, and architecture.

### Claude Code skill for EPLAN development

While the MCP servers let Claude *act* on EPLAN, the skill teaches Claude to *write correct EPLAN code*: scripting entry points, verified action parameters, parts-database access, Remote Client automation (dynamic ports, headless EPLAN, Cogineer), and the production pitfalls (pseudo-async command blocking, message-loop monitor thread, dispose discipline, EPLAN 2025 remoting changes).

Install from Claude Code (this repo is also a plugin marketplace):

```
/plugin marketplace add covagashi/eplan-rag-mcp
/plugin install eplan-development@eplan-tools
```

See [`claude-skills/eplan-development/README.md`](claude-skills/eplan-development/README.md) for manual installation and details.

## Adding New EPLAN Actions

The local MCP server registers tools **dynamically** from each actions package's
`__all__` list, so adding an action is just two steps (no per-tool boilerplate).

### 1. Implement the action

In `eplan-p8-mcp-server/mcp_server/api/actions/<your_module>.py`:

```python
def open_project(project_path: str, open_mode: str = None) -> dict:
    """Open a project in EPLAN.

    Args:
        project_path: Full path to the .elk project file.
        open_mode: "Standard", "ReadOnly", or "Exclusive" (optional).
    """
    manager, error = _get_connected_manager()
    if error:
        return error
    action = _build_action("ProjectOpen", Project=project_path, OpenMode=open_mode)
    return manager.execute_action(action)
```

### 2. Export it

Add the function to the imports **and** to `__all__` in
`eplan-p8-mcp-server/mcp_server/api/actions/__init__.py`. It is then
auto-registered as `eplan_open_project`.

### 3. Restart the MCP server

The new tool becomes available after restarting Claude / the server.

### 4. Validate against the official docs (optional)

`eplan-p8-mcp-server/tools/validate_actions.py` cross-checks every action name
and parameter declared in the wrappers against the official EPLAN docs RAG and
writes a markdown report:

```bash
python eplan-p8-mcp-server/tools/validate_actions.py
```

![EPLAN test](image-1.png)

### Tips

1. **Verify against the docs** — use the remote P8 RAG (`https://rag2026.covaga.xyz`) to confirm the exact EPLAN action name and parameters.
2. **Write meaningful docstrings + type hints** — they become the tool description and input schema the LLM sees and relies on.
3. **Handle paths carefully** — Windows paths need escaping (`\\`) or forward slashes (`/`).

## Private Extension Modules (`EPLAN_MCP_EXTENSIONS`)

The server can load **extra tool modules from outside this repo** — for
company-specific or private tooling (custom add-in test harnesses, internal
workflows) that must not live in a public repository.

Set the `EPLAN_MCP_EXTENSIONS` environment variable on the MCP server entry to
one or more directories (separated by `;` on Windows). Every top-level `*.py`
file there (not starting with `_`) is imported at startup and its `__all__`
functions are registered as MCP tools, exactly like the built-in actions:

```python
# my_company_tools.py  (in a private repo, NOT in eplan-rag-mcp)
TOOL_PREFIX = "acme_"          # optional, default "eplan_"
__all__ = ["run_smoke_test"]

import actions                  # the server's api/ folder is on sys.path
from actions._base import _get_connected_manager

def run_smoke_test(project_path: str) -> dict:
    """Docstring becomes the tool description the LLM sees."""
    clone = actions.scratch_project_create(project_path)
    ...
    return {"success": True}
```

Rules and behavior:

- `TOOL_PREFIX` namespaces the tools (`acme_run_smoke_test` above).
- Extensions can import everything the built-in actions use: `actions`,
  `actions._base`, `actions.scripted._execute_script` (run C# inside EPLAN),
  `eplan_connection`.
- A broken extension is reported on stderr and skipped — it never prevents the
  server from starting.
- `eplan_list_extensions` shows what was loaded.

Combined with the lifecycle and scratch-fixture tools this enables a fully
unattended loop for developing private EPLAN add-ins: build the DLL → deploy →
`eplan_app_restart` → verify the add-in's actions registered (e.g. via a
FindAction script) → run them against a disposable scratch project →
`eplan_get_system_messages` to catch anything EPLAN complained about.

## EPLAN Version Selection (automatic)

There is **nothing to configure**. On startup the server scans
`C:\Program Files\EPLAN\Platform` for installed versions and:

- **Auto mode (default):** `eplan_connect` targets the **newest installed
  version** and picks the right .NET runtime automatically (coreclr for
  EPLAN 2027+, .NET Framework for 2026 and older).
- **Explicit mode (LLM's choice):** the LLM can call `eplan_versions` to list
  what is installed and then connect to a specific one with
  `eplan_connect(version="2026")` — e.g. "connect to eplan 2026".

Notes:
- EPLAN installed somewhere non-standard? Set the `EPLAN_PLATFORM_ROOT`
  environment variable to its `Platform` folder.
- Once one version's DLLs are loaded into the process, switching to another
  version requires restarting the MCP server (a .NET runtime cannot be swapped
  at runtime).
- `eplan_connect` also accepts a `host` (and `"host:port"`) to reach an EPLAN
  instance on another machine; port auto-detection only works on localhost.

## Resources

- [EPLAN API Documentation](https://www.eplan.help/)
- [MCP Protocol Specification](https://modelcontextprotocol.io/)
- [Claude Code Documentation](https://docs.anthropic.com/claude-code)

---

# EPLAN AI 自动化工具包

[English](#eplan-ai-automation-toolkit) | **中文**

> ⚠️ **本项目仍在开发中！**
> Claude 或任何 AI 目前都还不能自行理解该工具的使用场景，除非有人加以引导和规划。请谨慎使用。

一套面向 **EPLAN Electric P8** 与 **EPLAN EEC Pro 2026** 的 AI 辅助自动化工具，基于模型上下文协议（MCP）构建。

本仓库包含三个相互独立的子项目：一个直接驱动 EPLAN P8 的本地 MCP 服务器，以及两个部署在 Cloudflare Workers 上、通过语义搜索提供文档检索的远程 MCP 服务器。

> 正在和大语言模型一起使用本仓库？请阅读 [`llm.md`](llm.md) —— 它以面向 LLM 的方式
> 说明了本工具包能做什么、可配置哪些内容。

## 仓库结构

```
.
├── eplan-p8-mcp-server/          # 本地：控制 EPLAN P8 的 MCP 服务器
├── cloudflare-rag-eplan-p8/      # 远程：通过 MCP 提供 P8 文档 RAG 的 Cloudflare Worker
├── cloudflare-rag-eecpro/        # 远程：通过 MCP 提供 EEC Pro 文档 RAG 的 Cloudflare Worker
└── claude-skills/                # 技能：用于 EPLAN P8 开发的 Claude Code Skill
```

| 目录 | 类型 | 用途 | 适用的 EPLAN 产品 |
|--------|------|---------|---------------|
| `eplan-p8-mcp-server/` | 本地 Python MCP | 从 Claude 驱动正在运行的 EPLAN 实例（打开/关闭项目、导出、报表、脚本等） | EPLAN Electric P8 |
| `cloudflare-rag-eplan-p8/` | 远程 Cloudflare Worker | 以远程 MCP + REST API 的形式提供 P8 文档索引 | EPLAN Electric P8 |
| `cloudflare-rag-eecpro/` | 远程 Cloudflare Worker | 以远程 MCP + REST API 的形式提供 EEC Pro 文档索引 | EPLAN EEC Pro 2026 |
| `claude-skills/eplan-development/` | Claude Code Skill | 教 Claude 写出正确的 EPLAN 脚本、API 代码和 Remote Client 应用（模式与陷阱） | EPLAN Electric P8 |

每个子项目都有各自的 README，其中包含安装和使用的详细说明。

## 什么是 MCP？

**MCP（Model Context Protocol，模型上下文协议）** 是一项开放标准，它让 Claude 这类 AI 助手能够与外部工具和服务交互。Claude 不再只是生成代码，而是可以实时在 EPLAN 中真正*执行*操作，并通过语义搜索查阅文档。

## 快速开始

### 本地 EPLAN 自动化（P8）

本地 MCP 服务器让 Claude 能够驱动正在运行的 EPLAN 实例，共提供
**172 个工具**：7 个连接/辅助工具、**161 个 EPLAN 操作**（`eplan_*`，
每一个都在 QuietMode 下的 C# 脚本中静默执行 —— 不会有任何 EPLAN
对话框阻塞无人值守的运行），以及 **4 个资产管理壳工具**
（`aas_*`），用于 AAS/AASX 数字孪生的导出与导入。这 161 个操作中
包含 4 个实时 DataModel 工具（`eplan_live_query_functions`、
`eplan_live_query_pages`、`eplan_live_set_function_text`、
`eplan_live_set_connection_designations`），通过运行时反射读取和
编辑当前打开项目的对象模型，绕开脚本引擎对静态 `using` 指令的限制。

EPLAN 版本会被**自动检测**：服务器会扫描
`C:\Program Files\EPLAN\Platform` 并选用已安装的最新版本，
无需任何配置。

```bash
pip install pythonnet mcp
claude mcp add eplan -- python YOURPATH/eplan-p8-mcp-server/mcp_server/server.py
claude mcp list   # 应当能列出 "eplan"
```

随后启动 EPLAN，打开 Claude Code，并输入 `connect to eplan`。完整指南见 [`eplan-p8-mcp-server/mcp_server/README.md`](eplan-p8-mcp-server/mcp_server/README.md)。

![Claude CLI configured](image.png)

#### 使用前提

要使用远程控制（remoting），请按以下步骤操作：

启动 EPLAN remoting 之前，必须先启用 **允许通过 Remote Client 进行远程访问** 这一设置。可在设置对话框中通过图形界面完成（**文件 > 设置... > 工作站 > 接口 > 远程访问**）。

![Allow remote access via Remote Client](Remoting_Setting_AllowLocalAccess.png)

### 远程文档 RAG（P8 与 EEC Pro）

这两个服务已经部署完毕、开箱即用 —— 无需任何本地数据：

```bash
# EPLAN Electric P8 文档
claude mcp add eplan-rag -- cmd /c npx mcp-remote https://rag2026.covaga.xyz/mcp

# EPLAN EEC Pro 2026 文档
claude mcp add eecpro-rag -- cmd /c npx mcp-remote https://rageecpro.covaga.xyz/mcp
```

它们同时提供普通的 REST API（在开发过程中用于核对 EPLAN 操作名称和
参数非常方便）：

```bash
curl -X POST https://rag2026.covaga.xyz/search -H "Content-Type: application/json" \
     -d "{\"query\": \"export project pdf\", \"topK\": 3}"
```

工具、REST 接口和架构说明见 [`cloudflare-rag-eplan-p8/README.md`](cloudflare-rag-eplan-p8/README.md) 与 [`cloudflare-rag-eecpro/README.md`](cloudflare-rag-eecpro/README.md)。

### 用于 EPLAN 开发的 Claude Code Skill

如果说 MCP 服务器让 Claude 能够对 EPLAN *执行操作*，那么这个 Skill 则教会 Claude *写出正确的 EPLAN 代码*：脚本入口点、经过验证的操作参数、部件数据库访问、Remote Client 自动化（动态端口、无界面 EPLAN、Cogineer），以及生产环境中的各种陷阱（伪异步命令阻塞、消息循环监视线程、dispose 规范、EPLAN 2025 remoting 的变化）。

从 Claude Code 中安装（本仓库同时也是一个插件市场）：

```
/plugin marketplace add covagashi/eplan-rag-mcp
/plugin install eplan-development@eplan-tools
```

手动安装方式和更多细节见 [`claude-skills/eplan-development/README.md`](claude-skills/eplan-development/README.md)。

## 添加新的 EPLAN 操作

本地 MCP 服务器会根据每个 actions 包的 `__all__` 列表**动态**注册工具，
因此新增一个操作只需两步（无需为每个工具编写样板代码）。

### 1. 实现该操作

在 `eplan-p8-mcp-server/mcp_server/api/actions/<your_module>.py` 中：

```python
def open_project(project_path: str, open_mode: str = None) -> dict:
    """Open a project in EPLAN.

    Args:
        project_path: Full path to the .elk project file.
        open_mode: "Standard", "ReadOnly", or "Exclusive" (optional).
    """
    manager, error = _get_connected_manager()
    if error:
        return error
    action = _build_action("ProjectOpen", Project=project_path, OpenMode=open_mode)
    return manager.execute_action(action)
```

### 2. 导出该函数

在 `eplan-p8-mcp-server/mcp_server/api/actions/__init__.py` 中，把该函数
加入 imports **以及** `__all__`。它随后会被自动注册为 `eplan_open_project`。

### 3. 重启 MCP 服务器

重启 Claude / 服务器之后，新工具即可使用。

### 4. 对照官方文档进行校验（可选）

`eplan-p8-mcp-server/tools/validate_actions.py` 会把封装函数中声明的每一个
操作名称和参数，与官方 EPLAN 文档 RAG 进行交叉核对，并输出一份 markdown 报告：

```bash
python eplan-p8-mcp-server/tools/validate_actions.py
```

![EPLAN test](image-1.png)

### 小贴士

1. **对照文档核实** —— 使用远程 P8 RAG（`https://rag2026.covaga.xyz`）确认 EPLAN 操作的准确名称和参数。
2. **写好文档字符串和类型注解** —— 它们会成为 LLM 所看到并依赖的工具描述和输入 schema。
3. **谨慎处理路径** —— Windows 路径需要转义（`\\`）或改用正斜杠（`/`）。

## EPLAN 版本选择（自动）

**无需任何配置**。服务器启动时会扫描
`C:\Program Files\EPLAN\Platform` 查找已安装的版本，然后：

- **自动模式（默认）：** `eplan_connect` 会连接**已安装的最新版本**，
  并自动选用相应的 .NET 运行时（EPLAN 2027 及以上使用 coreclr，
  2026 及更早版本使用 .NET Framework）。
- **显式模式（由 LLM 决定）：** LLM 可以调用 `eplan_versions` 列出
  已安装的版本，再用 `eplan_connect(version="2026")` 连接到指定版本 ——
  例如「connect to eplan 2026」。

注意事项：
- EPLAN 安装在非标准路径？把 `EPLAN_PLATFORM_ROOT` 环境变量设为其
  `Platform` 目录即可。
- 一旦某个版本的 DLL 被加载进进程，切换到另一个版本就需要重启 MCP
  服务器（.NET 运行时无法在运行期间更换）。
- `eplan_connect` 也接受 `host`（以及 `"host:port"`），用于连接另一台
  机器上的 EPLAN 实例；端口自动检测仅在 localhost 上有效。

## 相关资源

- [EPLAN API 文档](https://www.eplan.help/)
- [MCP 协议规范](https://modelcontextprotocol.io/)
- [Claude Code 文档](https://docs.anthropic.com/claude-code)

---
  [![MCP Badge](https://lobehub.com/badge/mcp/covagashi-eplan_2026_ia_mcp_scripts)](https://lobehub.com/mcp/covagashi-eplan_2026_ia_mcp_scripts)
