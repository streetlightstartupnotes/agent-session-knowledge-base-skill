# Agent Session 知识库 Skill 包

这是一套以隐私和证据为先的 Codex Skill。它可以把已支持的 Agent 会话记录重建成可维护、可审计的知识库，并在后续任务中只读取真正相关、已经审核发布的知识。

当前版本：**v0.3.2（Beta）**

## 包含什么

- `agent-session-knowledge-rebuilder`：自动发现或接收用户明确指定的 session 目录，识别已支持格式，清理隐私和二进制内容，合并续接会话，生成可审计的语义复核工作区，并把审核通过的个人、项目与协作知识发布为带证据的双向链接文档。
- `agent-knowledge-reader`：根据当前任务读取少量相关知识。找不到匹配内容时返回 `no_match`，不会把整套知识库全部塞进上下文，也不会编造缺失信息。

Python 程序负责可重复的抽取、清理、审计、增量状态、规则校验和检索。真正理解项目意图、纠正、失败、交付状态和项目关系的语义复核由调用 Skill 的 Agent 完成。语义复核没有满足证据契约时，系统不会把草稿冒充成已完成的知识库。

## 已验证的输入格式

以下具体格式已经实现适配器并拥有测试证据：

- Codex rollout JSONL
- Clacky session JSON 与 chunk Markdown
- Claude Code project JSONL
- WorkBuddy project JSONL
- Neo Claude-compatible project JSONL
- Cursor `agent-transcripts` JSONL

未知格式会明确报告为不支持。发现某个 Agent 的安装目录，或者提供了适配器扩展契约，都不等于已经兼容这个 Agent。各格式的证据等级和当前候选项见[兼容矩阵](agent-session-knowledge-rebuilder/references/compatibility.md)。

## 运行要求与安装

- 当前只有 Codex 经过了原生 Skill 打包与宿主验证。
- 需要 Python 3.9 或更高版本，命令行工具只使用 Python 标准库，不需要安装第三方 Python 包。
- 默认只读原始 session，不会修改或删除源会话。

把下面两个目录分别复制到 Codex 的 Skills 目录中，保留各自独立的名称：

```text
agent-session-knowledge-rebuilder/
agent-knowledge-reader/
```

随后调用 `$agent-session-knowledge-rebuilder`。它会先清点当前环境中可访问的来源；如果用户尚未指定知识库位置，它会先说明可选位置并等待用户确认，再写入私人知识库。

知识库经过语义复核并正式发布后，调用 `$agent-knowledge-reader`，即可按当前任务读取有限的相关上下文。

直接查看命令行帮助：

```bash
python3 agent-session-knowledge-rebuilder/scripts/session_kb.py --help
python3 agent-knowledge-reader/scripts/read_knowledge.py --help
```

## 安全与审计

重建流程会区分用户本人、Agent、子 Agent、测试角色、客户角色、第三方材料和运行时角色。压缩摘要、运行时注入、旧记忆转录、委派转录和嵌套审批转录会被隔离，不能直接进入个人事实。

流程还会处理同一消息的重复表示，剥离二进制与 Base64 正文，清理常见凭据和私人联系方式，冻结源文件状态，并生成错误、回退、覆盖缺口、未知格式、处理统计和兼容矩阵等审计文件。

公开发布自己的分支前，应运行发布检查：

```bash
python3 agent-session-knowledge-rebuilder/scripts/session_kb.py release-check --root .
```

## 当前边界

- 真实格式的冒烟测试证据来自 macOS。Windows 与 Linux 的发现路径和注册位置有自动化测试，但尚未在这两个系统上完成发布环境中的真实端到端验证。
- 当前不能自动纠正语义上错误合并或错误拆分的项目链。发现分组错误时，Agent 必须停止发布并明确报告阻塞。
- 超大复核包目前使用文档规定的连续检查点协议，还没有原生命令行分页功能。
- 自动发现是有边界的启发式扫描，只能承诺覆盖审计报告中列出的目录与过滤条件；显式导出的 session 仍是可靠的补充方式。
- `agent-knowledge-reader` 目前根据已审核索引中的标题、别名和关键词进行词法匹配，并沿确认关系读取有限的一跳链接。返回 `no_match` 不代表原始 session 从未提过相关内容。

完整工作流、证据规则和完成门请查看两个 Skill 各自的 `SKILL.md` 与 `references/` 文档。

## 测试覆盖

仓库包含合成单元测试和契约测试，覆盖格式识别、统一事件模型、去重、续接会话合并、摘要排除、身份隔离、隐私清理、二进制处理、增量冻结、语义复核与关系校验、按任务检索和输出契约。

## 开源许可

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
