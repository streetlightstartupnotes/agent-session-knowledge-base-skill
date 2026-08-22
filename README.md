# Agent Session 知识库重建 Skill

把不同 Agent 保存的 session 重建成一套可以继续维护的个人、项目和协作知识库。

当前版本 v0.3.4 Beta

## 直接复制这句话给 Agent

> 请使用 `agent-session-knowledge-rebuilder`，先探查当前环境中能够合法读取的 Agent session，报告已支持格式、未知格式和扫描缺口，向我确认知识库保存位置，再冻结并读取原始记录，保留用户消息、Agent 正文、工具调用与结果、补丁、浏览器和设备事件及真实交付，隔离摘要、运行时注入、旧记忆、嵌套转录、二进制和隐私信息，按真实项目链合并续接会话，逐项目完成带事件证据的对话蒸馏，分析并建立拥有两端证据的双向连接，校验通过后发布三份基础知识和项目详录，最后配置 `agent-knowledge-reader` 供后续任务按需读取；未知格式必须报告不支持，原始 session 保持只读。

用户只需要在 Agent 询问知识库保存位置时选择路径。其他读取、清理、复核、连接、校验和发布工作应由 Agent 继续完成。

## 两个 Skill 怎么分工

| Skill | 什么时候用 | 负责什么 |
| --- | --- | --- |
| `agent-session-knowledge-rebuilder` | 第一次建库，或者以后增加了新 session | 找 session、识别格式、清理数据、合并项目链、蒸馏知识、建立连接、发布知识库、执行增量更新 |
| `agent-knowledge-reader` | 知识库已经发布，准备开始一个新任务 | 根据当前任务读取少量相关知识，沿有限的确认连接补充上下文，没有匹配时返回 `no_match` |

## 整体运行逻辑

```text
发现 session
  → 确认知识库保存位置
  → 冻结本次读取范围
  → 识别格式并统一事件
  → 清理噪声、隐私和二进制
  → 去重并合并续接会话
  → 按真实项目链蒸馏
  → 分析项目关系并建立双向连接
  → 校验证据和完成状态
  → 发布知识库
  → 后续任务按需读取
  → 新 session 进入下一次增量更新
```

## 各个功能分别做什么

| 功能 | 做什么 | 内部逻辑 |
| --- | --- | --- |
| 自动发现 | 寻找当前环境里可能存在的 Agent 和 session | 检查已知目录、系统应用数据目录、环境变量、Agent 特征目录和显式指定路径。扫描有边界，报告会写明检查过哪里、哪里没有权限、哪里被数量限制截断 |
| 格式识别 | 判断每个文件应该交给哪个适配器 | 每个适配器用文件特征和内容头部识别自己的格式。无法确认的文件进入未支持报告，不会硬套现有解析器 |
| 统一事件 | 把不同 Agent 的记录变成同一种结构 | 用户消息、Agent 正文、工具调用、工具结果、补丁、浏览器事件、设备事件、状态和交付都会获得统一字段、来源位置和事件 id |
| 噪声隔离 | 防止旧总结和运行时内容污染知识 | 压缩摘要、系统注入、旧记忆读取、外部导入转录、嵌套审批、委派包装和隐藏推理会进入排除审计，不参与身份与项目结论 |
| 身份隔离 | 防止把别人写成用户本人 | 序列化字段里的 `user` 只代表记录角色。系统继续区分本人、未知用户、客户、测试角色、引用材料、编排者和子 Agent |
| 隐私与二进制处理 | 避免知识库和公开源码带出敏感内容 | 清理常见凭据、Cookie、联系方式、私有网络地址和私人本机路径。图片、音频和长 Base64 正文只保留安全占位信息 |
| 去重与续接合并 | 合并重复表示和同一条项目链 | JSON 与 chunk 的重复消息会去重。父子 session、原生续接 id、稳定项目 id、工作目录和具体产物用于提出项目分组，语义复核会继续检查错误合并与错误拆分 |
| 冻结与增量读取 | 固定本轮分母，后续只处理变化 | 每轮先记录文件大小、边界哈希和快照。追加型文件只读经过验证的新尾部，文件被重写时执行有审计记录的完整重读。本轮刚生成的日志不会被反复吃回去 |
| 对话蒸馏 | 把完整 session 变成可以继续工作的项目史 | 程序负责抽取、清理和初步分组。执行 Skill 的 Agent 按顺序读完每条项目链，记录原始目标、后续纠正、实际动作、验证范围、失败与回退、交付层级和剩余工作。每一条知识都引用事件 id |
| 双向连接 | 根据原始记录和用户意图连接文章与项目 | Agent 在项目详录稳定后再比较续接、依赖、共同产物、交接、纠正和矛盾。确认关系需要两端项目各自提供事件证据。关系方向写进 `knowledge-graph.json`，两篇 Markdown 同时生成正向入口和返回入口 |
| 关系候选隔离 | 保留可能有关但证据不足的项目关系 | 文件名、目录和关键词只负责寻找候选。证据不足的关系标为 `uncertain`，被否定的关系标为 `rejected`，两者都留在审计区，不进入正式导航 |
| 发布门 | 阻止草稿和错误结论进入正式知识库 | 项目链哈希、事件数量、证据等级、身份来源、关系两端、隐私检查和全部复核状态都要通过。错误项目分组无法修正时直接停止发布 |
| 按任务读取 | 避免每次把全部知识塞进上下文 | Reader 先读证据规则，再按任务匹配基础知识和少量项目详录，最多沿指定数量的确认关系读取一跳内容 |
| 审计与 dry-run | 让用户知道实际读了什么、排除了什么 | dry-run 不写正式知识库。正式运行输出文件统计、事件统计、去重数量、隐私处理、二进制剥离、错误、回退、未知格式、扫描缺口和兼容矩阵 |

## 对话蒸馏会写入哪些内容

每个真实项目按同一组字段保存。

| 项目字段 | 保存内容 |
| --- | --- |
| `objective` | 用户最初要解决的问题和当时语境 |
| `changes_and_corrections` | 后续增加、撤回、改口和明确否定的版本 |
| `actions_and_artifacts` | 实际执行过的动作和真正生成的文件 |
| `validation_and_observations` | 测试、浏览器、设备和工具观察覆盖到哪里 |
| `failures_and_fallbacks` | 失败、报错、中断、绕过与回退造成的影响 |
| `delivery_and_state` | 会话结束时能够观察到的最高完成层级 |
| `remaining_work` | 没有验证、没有交付或仍需确认的部分 |

一段顺滑的总结不能通过发布门。项目详录需要保留需求怎样变化、Agent 做过什么、用户怎样纠正、失败发生在哪里，以及最终真实进度。

## 双向连接怎样落到文件里

假设项目 A 产出一个共享工具，项目 B 的原始记录明确继续使用它。关系通过复核后会同时产生三份结果。

```text
knowledge-graph.json
项目 A  →  continued-as  →  项目 B

项目 A.md
相关知识  →  项目 B

项目 B.md
相关知识  →  项目 A  reverse of continued-as
```

图谱保留真实方向，Markdown 负责来回导航。反向链接只帮助查找，不会把原关系的因果方向倒过来。

## 最终生成哪些文件

| 位置 | 内容 |
| --- | --- |
| `knowledge/00-evidence-rules.md` | 证据等级、阅读规则、隐私边界和完成状态标准 |
| `knowledge/01-identity-and-current-direction.md` | 已确认身份、角色和当前方向，保留时间与适用范围 |
| `knowledge/02-collaboration-and-expression.md` | 协作方式、表达偏好、反馈样本和风险确认边界 |
| `knowledge/projects/*.md` | 按真实项目链整理的详细过程史 |
| `knowledge/knowledge-graph.json` | 文档、结论、项目断言和关系边 |
| `knowledge/knowledge-index.json` | Reader 用来按任务选择文档的已发布索引 |
| `review/` | 与本次事件集合哈希绑定的语义复核文件 |
| `audit/` | 统计、排除、错误、回退、未知格式、兼容矩阵、关系候选和完成报告 |

## 已实现并测试的 session 格式

- Codex rollout JSONL
- Clacky session JSON
- Clacky chunk Markdown
- Claude Code project JSONL
- WorkBuddy project JSONL
- Neo Claude-compatible project JSONL
- Cursor `agent-transcripts` JSONL

其他格式会进入未支持报告。适配器接口存在，只说明可以继续扩展。新增格式需要真实样本、解析实现和测试证据。

## 安装与运行条件

需要 Python 3.9 或更高版本，只使用 Python 标准库。原始 session 默认只读。

把两个 Skill 目录放到所用 Agent 支持的位置。不同宿主的自动发现和调用方式可能不同。宿主无法自动发现时，可以直接让 Agent 读取对应的 `SKILL.md`。

```text
agent-session-knowledge-rebuilder/SKILL.md
agent-knowledge-reader/SKILL.md
```

查看脚本提供的全部命令。

```bash
python3 agent-session-knowledge-rebuilder/scripts/session_kb.py --help
python3 agent-knowledge-reader/scripts/read_knowledge.py --help
```

## 当前验证边界

- 真实格式的端到端样本验证发生在 macOS。
- Windows 和 Linux 已测试发现路径及配置路径，没有完成真实环境端到端验证。
- 未知 Agent 宿主没有自动获得兼容声明。
- 语义层需要执行 Skill 的 Agent 逐项目阅读和判断，Python 单独运行会停在 `needs_semantic_review`。
- 错误合并或拆分的项目链目前需要停止发布并人工修正分组。
- 超大项目链使用连续检查点阅读，命令行还没有原生分页。

详细证据见[兼容矩阵](agent-session-knowledge-rebuilder/references/compatibility.md)。

## 有限许可

从 v0.3.3 起使用 [PolyForm Noncommercial License 1.0.0](LICENSE)。允许非商业使用、修改和非商业再分发，必须保留许可与版权声明。商业使用需要另行取得授权。
