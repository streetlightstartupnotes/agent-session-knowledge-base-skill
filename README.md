# Agent Session 知识库重建 Skill

把不同 Agent 留下的 session，重建成一套能回到原话、能持续更新、能按任务少量调用的个人、项目和协作知识库。

当前版本是 v0.5.0 Beta。

## 直接把这句话给 Agent

> 请使用 `$agent-session-knowledge-rebuilder`，先只读盘点我能访问的 Agent session 并向我确认知识库保存位置，再按完整流程重建、校正项目链、验证检索；原始 session 不许修改，未知格式必须如实报告。

路径和必要权限确认后，格式识别、隐私清理、逐段复核、知识写作、关系检查和机器验证由 Agent 继续完成。用户不需要先理解 Python、JSON 或内部文件结构。

## 这套东西为什么分成两个 Skill

| Skill | 什么时候用 | 它负责什么 |
| --- | --- | --- |
| `$agent-session-knowledge-rebuilder` | 第一次建库、有新 session、发现知识错误，或要撤回和忘记内容 | 找记录、清理、重建项目、蒸馏知识、建立连接、增量更新和验收 |
| `$agent-knowledge-reader` | 知识库已经发布，开始一个新的真实任务 | 只取当前任务需要的知识；没有可靠匹配就返回 `no_match` |

Rebuilder 把历史变成知识。Reader 每次只拿少量相关知识，不会把整套私人历史塞进上下文。

## 一次完整重建会发生什么

```text
只读盘点可访问的 session
  → 用户确认私人保存位置
  → 冻结这一次真正要读的文件和字节边界
  → 识别格式，统一成可追溯事件
  → 清理凭据、隐私和二进制，隔离摘要与运行时噪声
  → 核对谁在说话，检查项目是否被错合并或错拆分
  → 逐段读完每条真实项目链
  → 写出目标、改口、行动、失败、验证、交付和未完成项
  → 从关系两端取证，生成能往返但不颠倒方向的链接
  → 把反馈变成候选规则，再经过批准和后续效果验证
  → 发布知识并跑多组相关任务和困难反例
  → 通过以后才允许 Reader 使用
```

## v0.5 主要增加了什么

### 项目链可以有证据地纠正

脚本最初给出的 `project_key` 只是分组建议，不是最终事实。同一个目录可能放着两个项目；同一个项目也可能换目录、换 Agent 或续接到另一条 session。

Agent 先阅读建议分组，再准备一份与当前事件哈希绑定的项目归属调整文件，也就是 membership plan。它可以执行三种纠正：

- `merge`：把两个或多个建议项目合成一条真实项目链。
- `split`：从一个建议项目中取出严格的一部分事件，形成另一条项目链。
- `reassign`：把一组事件移到一个稳定的项目键下。

每次纠正必须写出理由、证据类型和原始事件，并绑定来源项目和所选事件的完整哈希。系统会检查有没有事件丢失、重复分配、越过来源范围或使用过期计划。没有被明确处理的新事件仍留在原建议项目里，系统不会替用户猜。

```bash
python3 agent-session-knowledge-rebuilder/scripts/session_kb.py review-init \
  --kb /private/kb

python3 agent-session-knowledge-rebuilder/scripts/session_kb.py review-init \
  --kb /private/kb \
  --membership-plan /private/project-membership-plan.json
```

第二次命令生成正式的变更前后分母、逐事件差异和合并/拆分/转移审计。后续读取要把这份 review 传给 `review-packet --review`，确保 Agent 读的是纠正后的项目链。

### 增量更新可以复用没有变化的证据块

第一次仍然必须把每条项目链从头读到尾。阅读长项目时，Agent 会为连续证据块保存：

- 精确起止位置和相邻边界事件；
- 事件顺序哈希与完整语义哈希；
- 只引用本块事件的证据笔记；
- 阅读时间和阅读声明。

下一轮出现新尾部或局部变化时，只有内容和前后边界都没变的证据块可以复用。变化块、相邻边界变化的块和没有缓存覆盖的范围必须重新打开。旧项目的自由文本总结不会直接沿用，Agent 仍须把复用笔记与新读原文放回当前完整项目链，重新做一次全项目综合。

```text
第一次：完整阅读 → 分块证据笔记 → 全项目综合
下一次：复用未变块 → 重读变化和边界范围 → 重新全项目综合
```

这节省的是 Agent 重复理解旧原文的上下文成本。为防止旧 session 中间被改写，程序仍会读取旧前缀计算完整性摘要，因此不能宣传成“磁盘上只读新增尾部”。

### 知识可以撤回、忘记和安排复核

v0.5 把三种需求分开：

| 动作 | 实际含义 |
| --- | --- |
| `retract` | 保留来源和历史记录，但把错误结论标为撤回，并从当前知识和关系图中停用 |
| `forget` | 从这套生成知识库的事件、文章、索引、图谱、复核文件和私人归档中清除所选内容，并留下不含原文的哈希墓碑，防止同一来源和记录位置在后续增量中把它重新带回来 |
| `schedule` | 为内容记录观察、检查、下次复核或保留期限；到期只进入待办报告，不会自动删除 |

先生成只读影响计划：

```bash
python3 agent-session-knowledge-rebuilder/scripts/session_kb.py lifecycle-plan \
  --kb /private/kb \
  --action forget \
  --event-id EVENT_ID \
  --plan /private/forget-plan.json
```

检查命中项、未命中项、受影响文章和后续重建要求。没有 `--commit` 时，应用命令仍然只是 dry-run：

```bash
python3 agent-session-knowledge-rebuilder/scripts/session_kb.py lifecycle-apply \
  --kb /private/kb \
  --plan /private/forget-plan.json
```

只有用户确认具体计划后才执行：

```bash
python3 agent-session-knowledge-rebuilder/scripts/session_kb.py lifecycle-apply \
  --kb /private/kb \
  --plan /private/forget-plan.json \
  --commit
```

计划与生成知识库当时的完整状态绑定。计划生成后文件发生变化，应用会拒绝，必须重新规划。`retract` 和 `forget` 会让发布与检索验收失效，需要重新复核受影响内容、蒸馏并跑检索测试。

提交采用可恢复事务。真正修改正文前，完成报告先进入 `lifecycle-applying`，Reader、注册和增量重建入口都会拒绝继续；清理后的最终文件先放入知识库内的私人暂存区，忘记墓碑先写入，正文修改和删除随后执行，最终完成报告最后写入。中途失败时只允许拿同一份计划重试，另一份计划会被拒绝；成功后事务记录和暂存文件会自动清理。`forget` 的暂存结果和事务记录也不得保留被忘记的原文。

这些动作始终不修改原始 session，也不能替用户删除云端记录、同步盘历史版本、系统备份、缓存、导出文件或其他设备上的副本。若要清理那些位置，必须由用户另行确认并在对应系统中操作。

### 最终检索不再只测一正一负

v0.5 的正式验收集至少包含：

- 两个表达不同、但都应该命中知识的相关任务；
- 两个很像真实请求、但不应该命中的困难反例。

相关任务可以指定应命中的项目键或文档类型。每个任务在隐私清理后必须仍然不同。整套测试只允许一份共同的 `retrieval_profile`，统一规定最低分数、最多项目数和最多关联文章数；单个任务不能另改阈值，避免为了让某一例通过而临时放宽或收紧。

```json
{
  "eval_set_version": 1,
  "retrieval_profile": {
    "min_score": 4,
    "max_projects": 3,
    "max_related": 2
  },
  "cases": [
    {
      "kind": "related",
      "task": "继续推进项目 Alpha",
      "expected_project_keys": ["project:alpha"]
    },
    {
      "kind": "related",
      "task": "回顾 Alpha 的目标变化和未完成项",
      "expected_project_keys": ["project:alpha"]
    },
    {"kind": "hard_negative", "task": "给阳台香草安排浇水"},
    {"kind": "hard_negative", "task": "比较两种露营炉的火力"}
  ]
}
```

```bash
python3 agent-session-knowledge-rebuilder/scripts/session_kb.py verify-retrieval \
  --kb /private/kb \
  --eval-set /private/retrieval-eval.json
```

审计只保存任务哈希、脱敏次数、选择摘要和发布文件清单哈希，不保存测试任务正文。测试集哈希和完成报告会同时绑定共同阈值、每条相关任务的最低项目命中数及其他预期。旧的 `--related-task` 加 `--unrelated-task` 仍可用于诊断，但对 v0.5 发布只能得到“旧双例已过、仍需测试集”的状态，不能打开最终完成门。

### Reader 每次使用都会返回一张回执

Reader 查询结果新增 `usage_receipt`，里面有本次知识库的运行编号、任务哈希、是否命中、选中的项目键、文档路径、实际查询阈值、是否沿用已验证阈值，以及发布清单哈希。它说明“这次任务选入了哪些知识”，方便后续核对检索是否正确。

Reader 没有收到阈值参数时，默认使用知识库通过验收的 `retrieval_profile`。调用者主动覆盖阈值时，回执会把 `verified_profile_used` 标为 `false`，不能再把这次选择效果说成原验收配置的表现。回执随命令结果返回，Reader 默认不会把它写回知识库，也不会因为一次调用就自动学习或修改规则。查询结果本身仍会显示当前任务；只有回执中的任务标识是哈希。

### 规则演进现在有可执行入口

完整过程仍然是：

```text
用户反馈
  → 只按精确字段聚类，Agent 再判断语义
  → 生成不生效的候选规则
  → 用户批准或拒绝
  → 后续真实任务提供前后行为证据
  → 通过后才标记 validated
```

对应命令是 `evolution-clusters`、`evolution-propose`、`evolution-queue`、`evolution-decide` 和 `evolution-evaluate`。命令让状态变化可以审计，但不会替 Agent 判断两条反馈是否真的相同，也不会把命令行里的 `--explicit-global-approval` 当成用户授权。规则状态变化以后，已有的跨项目复核会重新打开，必须针对新状态再检查，不能沿用旧签名假装没有变化。

### 仓库里有公开合成黄金样本

`tests/fixtures/golden/v1/manifest.json` 内联保存七种已声明格式的纯合成小样本。它们不含真实用户、真实项目或私人 session，可以公开进入 GitHub，用来防止适配器升级时把已有格式读坏。

```bash
python3 agent-session-knowledge-rebuilder/scripts/session_kb.py golden-check \
  --fixture-root tests/fixtures/golden/v1 \
  --manifest tests/fixtures/golden/v1/manifest.json
```

真实样本必须放在仓库外的私人目录，测试输出也必须隔离。黄金检查要求每个样本没有未知或未支持候选、没有扫描缺口、没有发现错误；“一个支持文件加一个未知文件”不能蒙混过关。公开合成样本通过，只证明回归契约没有被破坏，不会自动把某个新 Agent、宿主或操作系统写进兼容矩阵。新增兼容声明仍需要该精确格式的真实样本、实现、测试和端到端证据。

## 对话蒸馏保存什么

每个真实项目至少保留这些内容：

| 内容 | 要回答的问题 |
| --- | --- |
| `objective` | 用户最初到底想解决什么，当时有什么语境 |
| `changes_and_corrections` | 后来增加、撤回、改口或否定了什么 |
| `actions_and_artifacts` | 实际做了哪些动作，真正生成了什么 |
| `validation_and_observations` | 测试、浏览器、设备和工具观察覆盖到哪里 |
| `failures_and_fallbacks` | 哪些方法报错、中断、失败或被回退 |
| `delivery_and_state` | 会话结束时最高能由证据支持到哪一层 |
| `remaining_work` | 哪些仍未验证、未交付、有争议或需要确认 |

每项重要结论都回到事件 id。Agent 自己说“完成了”、代码存在、自动测试通过、真实交互发生、用户接受和公开可访问是不同状态，不能互相替代。

## 双向连接为什么不会把方向说反

文件名相似、目录相邻和关键词重合只能发现候选。确认关系时要重新打开两个项目的证据，并分别找到支持该关系的事件。

```text
项目 A  -- provides-foundation-for -->  项目 B

项目 A 的文章可以点到项目 B
项目 B 的文章也可以返回项目 A
图谱里的真实方向仍然是 A → B
```

证据不足的候选保留为 `uncertain`，被证据否定的候选保留为 `rejected`，都不会进入 Reader 的正式导航。

## 其他基础能力

| 功能 | 实际做法 |
| --- | --- |
| 自动发现和显式路径 | 检查当前环境的已知位置、Agent 安装线索和用户给出的导出目录，同时报告扫描范围和缺口 |
| 适配器识别 | 每种格式独立识别；无法唯一判断时进入未支持报告，不强行套格式 |
| 统一事件 | 保留用户消息、Agent 正文、工具调用与结果、补丁、浏览器和设备事件、错误、状态和真实交付 |
| 噪声隔离 | 压缩摘要、运行时注入、旧记忆、隐藏推理、嵌套审批和导入转录只进排除审计 |
| 身份隔离 | `user` 只说明记录通道，仍要区分本人、客户、第三方、测试角色、编排者和子 Agent |
| 隐私和二进制 | 清理 Token、密码、Cookie、授权头、私钥、联系方式、私人地址、用户目录和 Base64 等载荷 |
| 冻结与恢复 | 用完整字节摘要固定本轮分母，发现读取中变化就保留上一份可信状态并停止推进 |
| 去重和续接 | 消除重复传输记录，根据原生续接、用户意图和真实产物重建项目链 |
| 关系白名单 | Reader 只使用当前发布索引声明的图和文档，拒绝旧归档、图外文件和失效边 |
| dry-run 与审计 | 报告读了多少、排除了什么、清理了什么、失败在哪里、哪些格式未知 |

## 最终会生成什么

```text
knowledge/
  00-evidence-rules.md
  01-identity-and-current-direction.md
  02-collaboration-and-expression.md
  projects/*.md
  archive/<run-id>/*.md
  knowledge-graph.json
  knowledge-index.json

review/
  review.json

audit/
  events.jsonl
  project-membership.json
  lifecycle-*.json / lifecycle-*.jsonl
  compatibility-matrix.md
  completion-report.json
  retrieval-verification.json
  ...
```

前三份文章保存跨项目仍稳定的证据规则、身份与当前方向、协作与表达规则。项目文章保留真实项目链。`archive` 是私人历史，不进入当前索引。`review` 绑定 Agent 的语义阅读结果，`audit` 保存分母、排除、错误、关系、规则和验收证据。

这些都是普通 Markdown、JSON 和 JSONL 文件，不强制依赖某个笔记软件、数据库、向量库或云服务。

## 当前准确支持哪些输入

只有下面七种格式可以声明已实现并有真实样本证据：

1. Codex rollout JSONL
2. Clacky session JSON
3. Clacky chunk Markdown
4. Claude Code project JSONL
5. WorkBuddy project JSONL
6. Neo Claude-compatible project JSONL
7. Cursor `agent-transcripts` JSONL

这不是优先级。其他格式会进入未支持报告。适配器契约说明以后可以扩展，不代表现在兼容所有 Agent。

兼容还要分成三件事：

| 兼容轴 | 当前能说什么 |
| --- | --- |
| 输入格式 | 只包括上面七种精确格式 |
| 执行宿主 | 只有真实完成 Skill 发现、语义复核、发布和 Reader 调用的宿主才能单独声明 |
| 操作系统 | 路径逻辑有 Windows、macOS、Linux 自动测试；真实格式端到端证据来自 macOS，不能写成三个系统都已真实跑通 |

详细边界见[兼容矩阵](agent-session-knowledge-rebuilder/references/compatibility.md)。v0.5 没有按私人真实 session 重新跑一轮；本次新能力由公开合成样本和隔离测试验证，原有格式的真实样本证据仍按矩阵记录。

## 为什么需要 Python 3

Skill 是 Agent 要遵守的工作方法。Python 负责重复执行时必须完全一致的工作，例如扫描文件、识别格式、脱敏、哈希、冻结、去重、生成审计和卡住不合格发布。Agent 负责理解用户意图、项目关系、身份归属和知识写作。

运行脚本需要 Python 3.9 或更高版本，只使用标准库，不需要额外安装 Python 包。本文里的 `python3` 和 `/private/...` 只是为了让示例容易读，不是固定系统要求。Agent 应先探测当前环境已有的启动方式和路径；Windows 常见为 `py -3`，macOS 和 Linux 常见为可用的 `python3`。没有合适运行时就说明缺口，不能擅自安装。

```bash
python3 agent-session-knowledge-rebuilder/scripts/session_kb.py --help
python3 agent-knowledge-reader/scripts/read_knowledge.py --help
```

没有 Python 时仍能阅读 Skill 说明，但无法可靠完成可重复的全量处理和机器验收；是否另行安装或升级运行时由用户决定。

## 保存位置一定要由用户决定

知识库可能包含私人经历、项目过程和协作信息。Rebuilder 写入前必须解释选项并询问位置，不能默认放进 Skill 仓库、公开仓库、原 session 目录、整个用户目录或文件系统根目录。

- 私人本地目录通常最容易控制。
- 项目目录只有在项目本身私有且不会提交时才合适。
- 云同步目录可能把内容上传给第三方，并保留远端历史版本。
- 共享目录可能让团队成员或持有链接的人看到内容。
- 还要确认文件权限、磁盘加密、备份和删除后的历史保留方式。

注册 Reader 只是在本地保存一条位置映射，不等于上传，也不授予修改知识库的权限。

## 什么时候才算完成

`rebuild` 只生成确定性证据层。`review-init` 之后 Agent 还要纠正项目链、完整阅读、写 review、验证关系和规则。`distill` 只说明语义知识已经发布，状态仍是 `needs_retrieval_verification`。

v0.5 只有在至少两个相关任务和两个困难反例全部通过以后，状态才能进入 `complete` 或 `complete_with_unsupported_formats`。后一个状态只是如实保留用户已确认的未知格式，不会把未知格式升级成兼容。

检索验证、Reader 查询和 Reader 注册都会各自重新检查七个前置门：冻结完成、传输记录对账、解析无未解决错误、扫描范围完整、语义复核完成、知识图谱完成、正式知识已发布。若扫描里仍有未支持格式，还必须已有明确确认。不能因为前一个命令检查过，后一个入口就跳过。

发布后任何索引内知识文件被修改、替换或删除，Reader 都会发现清单哈希不一致并拒绝旧验收。生命周期中的撤回和忘记也会关闭发布与检索门。

## 许可

本仓库使用 [PolyForm Noncommercial License 1.0.0](LICENSE) 提供有限的非商业许可。允许在许可条件内非商业使用、修改和再分发。商业使用需要另行获得授权。它不是 MIT，也不应描述成 OSI 定义的开源软件。
