# Cursor SDK 纯文本提供商（AstrBot）

**一句话**：把 Cursor 官方 Python SDK（`cursor-sdk`）包装成 AstrBot 的纯文本模型提供商，可作为聊天、上下文压缩、记忆总结等场景的备用模型通道。它只支持纯文本，不提供工具调用、多模态输入和 embedding。

> 本插件不是 Cursor 官方项目，也不代表 Cursor 官方支持。使用前请确认你能正常使用 Cursor 服务，并遵守 Cursor 的服务条款与计费规则。

---

## 1. 功能特性

- **注册提供商类型**：插件加载后，AstrBot 的「服务提供商」页面会出现类型 `cursor_sdk_text`（显示名 `Cursor SDK (text only)`），可以像其他提供商一样配置 Key、模型和超时。
- **纯文本对话**：实现 AstrBot 的 `Provider.text_chat`，把文本请求交给 Cursor SDK，返回 `LLMResponse`；SDK 报告的 token 用量会映射成 AstrBot 的 `TokenUsage`。
- **系统人设与历史**：把 AstrBot 传来的 `system_prompt` 和会话历史封装成一个文本请求；历史按 system/user/assistant 的原始顺序写入 JSON 数组，AstrBot 始终是历史的权威来源。
- **人设外框**：当请求带有 `system_prompt` 时，会在请求前后加一段角色扮演说明，要求模型以人设身份回复，不要自称 Cursor、Auto、编程助手或 AI 模型。实际效果取决于模型，建议自己实测。
- **每次请求新 Agent**：即使共享底层 bridge，每次请求也会新建 SDK Agent 和空的临时工作目录，不恢复旧 SDK 会话；清空 AstrBot 会话后不会继续旧 SDK 对话。
- **bridge 自愈**：共享的 SDK bridge 在复用前会 `ping`，失效时先关闭旧 bridge，在发新请求前重建；不会为了"修连接"去重发已经失败的模型请求。
- **流式兼容**：`text_chat_stream` 先发一个包含完整文本的 chunk，再发最终响应；不提供逐 token 输出，但能兼容只显示 chunk 的消费端。
- **失败信息脱敏**：错误只返回状态码/异常类别，不输出 SDK 原始报错、URL 或请求体；聊天侧不会看到 Key、历史正文或请求内容。
- **不主动重试**：插件本身只发一次模型请求；超时、SDK 报错和地区限制都会直接以错误返回。

## 2. 适用场景 / 不适用场景

### 适用

- 日常纯文本聊天，想多一条模型通道或作为备用模型。
- 上下文压缩、记忆总结、日记草稿等"只吞吐文本"的后台任务。
- 想复用 Cursor 账号的可用模型额度，而不是再买一份厂商 API 额度。
- 需要把某个 OpenAI 兼容提供商换成 Cursor SDK 试试，但不依赖工具能力。

### 不适用

- 需要 function calling / 工具调用、工具历史、多步任务编排的会话。
- 需要图片、音频、视频等多模态输入或输出的场景。
- 需要 embedding / 向量检索、TTS、ASR 的场景（本插件只实现 chat completion）。
- 强依赖 JSON Schema 结构化输出、要求逐 token 流式输出的场景。
- 需要"每个用户都跑在独立系统账号里"的强隔离场景（本插件只做 SDK 工具限制，不是沙箱）。

## 3. 工作原理

```
AstrBot 会话 / 后台任务
        │  text_chat(contexts, system_prompt, prompt, ...)
        ▼
CursorTextProvider.text_chat（本插件）
        │  1. 拒绝图片/音频/工具/工具历史
        │  2. build_text_request：历史 + 人设 → 一个文本请求
        ▼
Cursor SDK（cursor-sdk 的 AsyncClient）
        │  3. 首次请求时 launch_bridge（127.0.0.1，max_retries=0）
        │  4. 每次请求：新建临时 workspace + 新建 Agent
        │  5. agent.send(文本) → run.wait()
        ▼
LLMResponse(completion_text, usage, id)
        │  6. 状态不是 finished → 抛脱敏错误；成功则映射 token 用量
        ▼
AstrBot 继续做会话记录、人设、记忆等上层逻辑
```

关键行为：

1. **消息封装**：没有工具、没有原生 chat 角色接口，所以把 system/user/assistant 历史序列化成 JSON 数组，放在一段说明文字里。历史里的 `think` 内容块会被跳过；其他非文本内容块（图片等）一律报错。
2. **请求隔离**：每次调用创建新的临时目录作为 `cwd`，SDK Agent 明确设置 `tools=[]`、`disallowed_tools=["mcp", "task"]`、空 MCP 服务器、空子代理、空自定义工具、`setting_sources=[]`。模型没有读文件、写文件、Shell、浏览器或子代理通道。
3. **用量映射**：`input_other = input_tokens + cache_write_tokens`，`input_cached = cache_read_tokens`，`output = output_tokens`；SDK 的 reasoning tokens 不重复计入。
4. **本地状态**：SDK bridge 的临时工作目录和状态目录放在 AstrBot 数据目录下的插件数据区（见第 7 节），插件自己不保存聊天历史。

### 注册时固定的字段

以下字段由注册模板和 AstrBot 决定，不需要手填，也不能通过插件配置修改：

| 字段 | 值 | 说明 |
| --- | --- | --- |
| `type` | `cursor_sdk_text` | 适配器类型，决定使用本插件 |
| `provider` | `cursor_sdk` | 标识这是 Cursor SDK 驱动 |
| `provider_type` | `chat_completion` | 本插件只提供对话补全类型 |


## 4. 安装

### 4.1 插件市场安装（推荐）

1. 打开 AstrBot 后台，进入「插件」页面。
2. 如果本插件已经在 AstrBot 插件市场上架，在市场里搜索 `astrbot_plugin_cursor_sdk` 或「Cursor SDK」。
3. 点安装，等待加载完成后在「服务提供商」页面检查新提供商类型。

### 4.2 后台上传 zip

1. 把本仓库目录压缩成 zip（压缩包根目录应为插件目录，即直接包含 `main.py`、`metadata.yaml`）。
2. 在 AstrBot 后台「插件」页的安装入口上传压缩包（按钮名称以你的 AstrBot 版本为准）。
3. 安装完成后重载插件。

### 4.3 手动放 data/plugins

1. 把整个目录复制到 AstrBot 数据目录下的 `plugins/astrbot_plugin_cursor_sdk`，例如 `<AstrBot 数据目录>/plugins/astrbot_plugin_cursor_sdk`。
2. 确认 `main.py`、`metadata.yaml`、`requirements.txt` 都在这个目录里。
3. 重启或重载 AstrBot。

### 4.4 安装 Python 依赖

在 **AstrBot 实际运行的 Python 环境** 里安装依赖（不要装到系统全局 Python）：

```bash
pip install -r requirements.txt
```

`requirements.txt` 只有一行：`cursor-sdk>=1.0.27,<2`。插件启动时如果导入不到 `cursor_sdk`，会在调用时提示安装这个包。

## 5. 配置项详解

本插件是**提供商适配器**，参数都在 AstrBot 的「服务提供商」页面配置，而不是「插件配置」页。`_conf_schema.json` 收录了同样的字段名和默认值，方便在后台查看；插件代码读取的是提供商配置里的值。

| 配置项 | 类型 | 默认值 | 作用 | 示例 | 注意事项 |
| --- | --- | --- | --- | --- | --- |
| `id` | 字符串 | `cursor_sdk_text` | 提供商源 ID，AstrBot 用「提供商ID/模型ID」引用它 | `cursor_sdk_text` | 已有提供商不会被自动改名；改了 ID 要同步改上层引用 |
| `enable` | 布尔 | `true` | 是否启用该提供商 | `true` | 关闭后该提供商不会加载 |
| `key` | 列表/字符串 | `[]` | Cursor 用户 API Key，代码取第一个 | `["你的-Cursor-API-Key"]` | 插件不内置 Key；SDK 启动参数已关闭环境变量回退，必须填在这里 |
| `model` | 字符串 | `auto` | 模型 ID，可带固化参数 | `auto` 或 `模型ID@参数=值,参数=值` | 插件不查询账号模型目录，只把这里填的当成可用模型 |
| `timeout` | 整数 | `120` | 单次请求超时秒数 | `120` | 合法范围 1–600；超时不会自动重发 |

补充说明：

- **模型 ID 语法**：普通 ID 原样传给 SDK；写成 `模型ID@reasoning=none,fast=true` 时，会转成 SDK 的 `ModelSelection`（`{"id": ..., "params": [...]}`），可以用来给模型固定参数。参数名和值只允许 `[A-Za-z0-9_.-]`，单个最长 40 字符。
- **获取模型列表**：AstrBot 的「获取模型列表」只会返回当前配置的那一个模型，不会真的去查账号目录，也不会在初始化时发模型请求。
- **Key 来源**：只来自提供商配置的密钥字段。插件不读浏览器登录态、不读其他实例凭据、不读环境变量（启动 bridge 时显式 `allow_api_key_env_fallback=False`）。

## 6. 使用方法

1. **新建提供商**：在「服务提供商」页面新增提供商，类型选 `Cursor SDK (text only)`；ID 可用默认值。
2. **填 Key**：把 Cursor 用户 API Key 填进密钥字段。不要填到其他厂商的 API 配置里。
3. **填模型**：默认 `auto`；若要指定模型，请先用 Cursor 官方模型目录确认你的账号可用，再把精确 ID 填进来。
4. **设置超时**：默认 120 秒，按网络情况在 1–600 秒之间调。
5. **切换会话模型**：在 AstrBot 的会话/全局设置里把对话模型指向这个提供商；记忆、日记、总结等后台插件如果也要用，需要把它们的 `llm_provider_id` 一类字段指向同一个提供商模型。
6. **关闭工具**：使用本提供商的会话请关闭所有模型工具，并把"电脑能力"设为无；历史中如果已有工具调用记录，请新建会话，不要为了绕过报错删除原历史。
7. **首次验证**：先用纯文本私聊做一条短请求，确认回复、token 显示和会话隔离正常；AstrBot 的"测试模型"按钮也会真实调用模型。

权限边界：

- 插件本身不读取 QQ 消息、不监听事件、不注册命令；是否可用完全由 AstrBot 的会话和提供商配置决定。
- 所有走到该提供商的调用都受本插件限制：带工具、带图、带历史的工具消息会被拒绝。

## 7. 数据与隐私

- **API Key**：保存在 AstrBot 的提供商配置中，由 AstrBot 管理；插件不写日志、不在错误信息里回显，也不内置任何 Key。
- **聊天历史**：插件不落盘保存历史。每次请求把 AstrBot 传来的上下文封装后直接发给 SDK。
- **本地目录**：
  - `plugin_data/cursor_sdk_text/<sha256(提供商ID) 前 16 位>/sdk-state`：SDK 自身的运行状态。
  - `plugin_data/cursor_sdk_text/<sha256(提供商ID) 前 16 位>/workspaces/`：bridge 临时目录和每次请求的临时目录。
  - 这些目录都在 AstrBot 数据目录下，由插件按提供商 ID 的哈希隔离。
- **临时文件清理**：单次请求的临时目录用 `TemporaryDirectory` 管理，请求结束即清理；插件卸载/重载时会关闭 bridge 并清理 bridge 的临时目录。进程异常退出可能留下残目录，可停止实例后手工删除。
- **SDK 自身记录**：Cursor SDK 可能按自己的机制保存本地运行记录；插件不把这些记录当长期记忆、不回放旧记录，也不擅自清理 SDK 数据。
- **外发范围**：除 Cursor 官方服务外，插件不向其他地址发送数据；SDK bridge 在 `127.0.0.1` 上与本机通信。
- **错误信息**：只包含状态和异常类别；SDK 原始错误、URL、请求体、历史正文都不会进入聊天文案。

## 8. 依赖与前置条件

| 项目 | 要求 | 说明 |
| --- | --- | --- |
| AstrBot | `>=4.16.0`（`metadata.yaml` 声明） | 插件按 2026-11 的 AstrBot Provider 接口核对；更旧版本未验证 |
| Python 包 | `cursor-sdk>=1.0.27,<2` | 见 `requirements.txt`，需装在 AstrBot 的运行环境 |
| 账号 | Cursor 用户 API Key | 从 Cursor 官方渠道获取，本插件不代登录、不读浏览器凭据 |
| 外部服务 | 可访问 Cursor 服务 | 网络不通、地区限制、账号无可用模型都会导致请求失败 |
| 平台 | 由 `cursor-sdk` 自身支持 | 本仓库只在 Windows 上实际验证过；其他平台以 SDK 官方说明为准 |
| 计费 | 按 Cursor 官方规则 | 使用 Cursor 计划额度不等于无限免费，具体以 Cursor 官方页面为准 |

**重要**：Cursor SDK 是 **Agent API**，不是 OpenAI 风格的 chat completions API。它没有公开的原生 `system_prompt` 接口（截至作者核对时），所以本插件的人设是通过文本外框实现的，不是系统级身份替换。角色效果需要实测，不要假设和原生聊天补全完全一致。

## 9. 常见问题 FAQ

**Q1：装好了插件，但「服务提供商」页面找不到 `Cursor SDK (text only)`。**
A：先确认插件已启用且加载成功（看插件页状态和日志），再刷新提供商页面。常见原因是 AstrBot 版本不匹配导致导入失败，或插件目录层级不对（`main.py` 必须在插件目录根）。

**Q2：报错提示安装 `cursor-sdk`。**
A：说明 AstrBot 的运行环境里导入不到 `cursor_sdk`。在 AstrBot 实际使用的 Python 环境里执行 `pip install "cursor-sdk>=1.0.27,<2"`，重启实例后重试。注意不是装到系统全局 Python。

**Q3：已经填了 Key，还是提示"请在本地提供商配置中填写 Cursor 用户 API Key"。**
A：检查 Key 是否填在**这个提供商的密钥字段**里，而不是插件配置或其他厂商配置；`key` 是列表时取第一个元素。插件不会回退去读环境变量，所以只填环境变量无效。

**Q4：请求报"请为此会话关闭所有模型工具"。**
A：本插件拒绝任何工具。请在会话设置中关闭所有工具，把电脑能力设为无，并新建纯文本会话；带工具调用的历史也会被拒绝。

**Q5：发图片或语音就报错。**
A：本插件只接受纯文本，图片、音频、其他多模态内容都会被拒绝。需要识图请换用支持视觉的提供商。

**Q6：用作记忆插件的总结模型时，偶尔失败会连续重试。**
A：本插件本身只尝试一次，但调用方（例如某些记忆插件）可能自己配置了多次重试。可把调用方的重试次数调低，或把本提供商只作为备用通道。

**Q7：流式输出时界面空白，或者只出现一次完整回复。**
A：这是设计行为：插件先发一个 `is_chunk=True` 的完整文本块，再发最终响应，没有逐 token 输出。请确认消费端版本支持这种两次 yield 的流式协议。

**Q8：请求超时怎么办？**
A：调大提供商配置里的 `timeout`（最多 600 秒），或确认网络能稳定访问 Cursor 服务。超时后插件不会自动重发，避免重复计费。

**Q9：报错里说"模型在当前地区不可用"或"模型运行失败"。**
A：这来自 SDK 返回的状态。换一个账号可用的模型 ID，或检查网络/地区限制。错误信息是脱敏后的，不会带 SDK 原始报错和请求体。

**Q10：模型自称 Cursor / Auto，人设没有完全生效。**
A：Cursor Agent 有自己的助手身份，本插件只能通过文本外框强调角色，不能替换原生系统提示词。换模型、调整人设或减少身份类提问可能有帮助；这一点需要自己实测取舍。

**Q11：用这个插件会不会产生额外费用？**
A：会按 Cursor 官方规则消耗你的 Cursor 计划/额度，具体以 Cursor 后台为准；插件不负责计费，也不会自动充值或轮换 Key。测试模型按钮、后台总结任务都会真实调用模型。

**Q12：重载插件后提供商配置还在吗？**
A：提供商配置由 AstrBot 保存，重载插件只是重新注册适配器；已配置的提供商会在插件初始化时被补加载。若插件被禁用或卸载，该类型的提供商将无法加载，但本插件只注销适配器，不会删除 AstrBot 配置里的提供商条目。

**Q13：能拿它做 embedding 或 TTS 吗？**
A：不能。只实现了 chat completion，不提供 embedding、TTS、ASR；向量检索请另外配置嵌入模型。

**Q14：多个 AstrBot 实例能共用同一个 Key 吗？**
A：技术上可以（Key 就是 Cursor 账号的 Key），但额度、限流和会话互相独立；插件不共享 Agent 会话，每个实例各跑各的 bridge。

## 10. 兼容性

- 与 AstrBot 其他提供商可以并存：把本提供商当主模型或备用模型都可以，切换由 AstrBot 的模型配置决定。
- 本插件只实现对话补全；如果某个插件要求工具调用、结构化输出或多模态，不能依赖本插件完成全部任务，需要保留原生提供商。
- 插件代码不引用任何平台适配器，QQ 和其他 AstrBot 平台都可以用；`metadata.yaml` 里的 `support_platforms` 按市场模板填写。
- 与 AstrBot 的流式消费兼容，但只提供"整段文本"级别的流式。
- 记忆类插件可配合使用，但前提是它们发出的任务确实是纯文本、且不要求工具。

## 11. 开发者说明

### 目录结构

```
astrbot_plugin_cursor_sdk/
├── main.py              # 提供商实现、请求封装、插件生命周期
├── requirements.txt     # cursor-sdk>=1.0.27,<2
├── metadata.yaml        # 插件元信息
├── _conf_schema.json    # 提供商字段说明（字段名/默认值）
├── README.md
├── LICENSE
├── .gitignore
└── tests/
    └── test_bridge.py   # 离线契约测试（标准库桩，不联网）
```

### 关键符号

- `PROVIDER_TYPE` / `SDK_MIN_VERSION`：提供商类型名与 SDK 最低版本。
- `build_text_request(...)`：把历史、prompt、system_prompt、附加文本拼成 SDK 请求。
- `model_selection(model)`：解析 `模型ID@参数=值` 语法。
- `sdk_agent_options(...)`：构造 Agent 选项（空工具、空 MCP、空 setting sources 等）。
- `_token_usage(usage)`：SDK 用量 → AstrBot `TokenUsage`。
- `_public_run_error(...)`：把 SDK 失败状态转成不含敏感信息的文案。
- `CursorTextProvider`：AstrBot 提供商实现。
- `CursorSDKPlugin`：插件加载/卸载时注册和清理提供商。

### 跑测试

```bash
py -m unittest discover -s tests -v
```

测试只用标准库，把 `astrbot` 和 `cursor_sdk` 换成桩对象，不需要 AstrBot 环境、不需要 Key、不会启动 bridge、不会联网、不产生费用，共 18 个用例。覆盖请求封装、工具/多模态拒绝、模型参数解析、token 映射、超时、错误脱敏、并发 bridge 复用、bridge 自愈和终止清理。

### 语法检查

```bash
py -m py_compile main.py tests/test_bridge.py
```

### 改代码注意

- 新增请求类型前先确认它是纯文本；不要让工具、图片或结构化协议漏进来。
- 错误文案必须保持脱敏：不要拼接 SDK 原始异常、URL、请求体或 Key。
- 不要在 `get_models` 或提供商初始化阶段发模型请求。
- `text_chat_stream` 的第一次 yield 必须是 `is_chunk=True`，否则只显示 chunk 的消费端会看到空回复。
- 修改超时、重试逻辑时注意：插件不自动重发失败请求。
- 插件终止时要关闭 bridge 并注销适配器，避免重载后重复注册。

### 已知未验证项

- 未在 Linux/macOS 上验证 `cursor-sdk` 的实际运行。
- 未逐一验证各模型的参数组合和人设效果。
- SDK 内部可能有自己的传输重试；本插件只保证自己不重发。
- 厂商计费、额度和限流以 Cursor 官方为准，本插件无法核对。

## 12. 更新记录

| 版本 | 日期 | 内容 |
| --- | --- | --- |
| 0.1.0 | 2026-11-08 | 首个公开版本：脱敏与通用化、补齐 `_conf_schema.json`、重写中文文档、保留离线测试 |

## 13. 许可证与作者

- 许可证：MIT，见 `LICENSE`（`Copyright (c) 2026 David Xu`）。
- 作者：David Xu
- 仓库：`https://github.com/DavidXu1209/astrbot_plugin_cursor_sdk`（发布前请把占位符替换成实际 GitHub 用户名）
