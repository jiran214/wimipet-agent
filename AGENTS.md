# 项目协作规则

## 人工体验交付规则

- 每个模块在开发初期建立用户可操作的独立体验入口，随能力推进，不等全部开发结束才提供；不能只交付接口和自动测试。
- 提供简短启动说明、环境前提、自带固定样例、操作步骤和预期现象；用户不应手工调用接口或依赖其他模块准备数据。
- 明确标注模拟与真实能力及未接通项；无密钥时也能体验样例流程，真实模型模式另行说明配置方式。
- 提供恢复初始场景、重复执行或回放固定输入的能力；重置仅作用于体验数据，不静默删除用户资产。真实模型重试不保证相同输出。
- 支持记录场景、版本、输入步骤、预期与实际结果及必要错误信息，便于复现；不在反馈中暴露密钥，不默认收集个人截图。
- 样例可采用固定版本的公共参考素材，但模块必须自带运行所需内容，不读取其他模块私有目录；演示与正式产品使用同一核心实现。
- 自动检查、人工体验与真实模型效果评审分别记录。未实施或未验证的功能不得以模拟演示冒充完成。

- 使用中文回复，项目需求文档默认使用中文。

## 文档与需求管理

- 项目文档统一放在 `docs/`，需求及创意文档统一放在 `docs/PRD/`。
- 需求编号按模块独立递增：A = desktop-world，B = character-studio，C = interaction，D = companion-ui。文件格式为 `A0001-主题.md`；各模块分别从 0001 开始，编号是文件名前缀。
- 新建文档前检查所属模块已有编号，按该字母序列的最大编号递增；不得重复或复用已删除编号，标题调整不改变编号。
- 每次需求必须明确唯一的主责模块，聚焦该模块的目标和验收。跨模块变化拆成各模块独立编号的关联需求，通过链接说明协作契约，不写成一份混合需求。
- 同一需求的补充与修订优先更新原文件；独立需求使用新编号，并在需要时引用相关文档。
- 文档应明确区分已确认需求、建议、待验证事项和暂缓事项；不得将讨论中的示例、未采纳建议或未确认技术方案写成确定需求。
- 修改需求时保留仍然有效的约定，以用户最新确认的意见为准，并同步修正冲突内容。

## 模块架构

- 四模块首版需求分别为 `docs/PRD/A0001-desktop-world.md`、`docs/PRD/B0001-character-studio.md`、`docs/PRD/C0001-interaction.md`、`docs/PRD/D0001-companion-ui.md`；进入模块前阅读其 `AGENTS.md` 和对应需求。
- 业务模块不得导入其他业务模块的实现、访问其私有状态或要求其运行；使用角色包、显式接口和装配层注入连接。
- 每个模块拥有自己的权威状态；不要创建全局可变状态、第二份权威消息记录或以公共工具名义共享业务逻辑。
- 各模块未来实现必须提供独立演示、固定样例和无需真实模型的自动测试；真实系统及模型集成验证单独说明环境要求。
- 装配层只负责连接、配置、生命周期和发布，不承载感知策略、角色决策、身体规则或界面业务。
- 当前主工作区尚未合并四模块实现；独立实现与首轮验收见 `docs/reviews/first-development/README.md`。本文件统一契约是后续迁移基线，不代表现有分支已兼容；必须分别报告实现、模拟、集成与人工效果的状态。

## 整体目标

- 构建以陪伴为核心的多角色 Meta 桌面世界：用户自定义人设，角色感知真实桌面、交流和表达，用户可参与共同故事。
- 以四个可独立演示、测试和替换的模块提升整体效果；当前不考虑商业化，不硬编码双角色上限，不提前引入复杂长期记忆或小镇经营系统。
- 评价各模块贡献时同时检查独立能力和契约兼容性；统一装配后，模型等待不阻塞身体反馈，气泡和面板不产生两份对话。

## 首轮复盘后的开发纪律

- 每轮跨模块工作明确生产方、消费方及装配负责人；装配负责人负责集成入口和链路验证，不能让所有模块都将集成推迟给无人负责的后续阶段。
- 接口开发第一个检查点交付最小共同样例和生产者/消费者验证，再扩大功能。完成标准是“独立可体验 + 真实消费者消费共同样例”，不是只有内部测试通过。
- 共同样例放在 `docs/contracts/fixtures/v1/`，作为只读协议资产，不是第五个业务模块；独立演示可复制固定版本并注明来源，不能各自改写成不同协议。已提供协调层整理的角色包，生产者真实导出与消费者迁移仍待验证，不宣称联调通过。
- 测试使用状态等待、任务排空和可控时钟；不用毫秒级固定 sleep 猜测完成。命令指定工作目录，服务测试优先临时端口，显示版本/实例/真实或模拟模式，只清理本次创建的进程。
- 模块修复仍遵守 A/B/C/D 主责独立编号；只更新一个 worktree 的规则不等于其他工作树已同步，后续开发前必须同步本规则提交，再处理模块本地文档冲突。

## 模块间统一契约 v1

以下是项目唯一的跨模块规范，版本标识为 `wimipet/1`。模块内部可用不同结构，但公开边界必须符合本规范。旧的 A-display/1、character-studio/0.1、c-lab/0.1 和 D 本地草案仅作历史实现记录；旧格式接入必须有显式适配与验证，不能声称天然兼容。

本规范中的尺寸/数量限制是首个可互通配置，不是产品永久上限。新增能力通过版本/能力声明扩展；未知主版本拒绝，新增可选字段可忽略，未知必需能力明确拒绝。只有完成共同样例测试后才能声明支持 v1。

### 1. 身份、时间、事件和错误

- `packageId` 标识创作角色包，`revision` 是正整数确认版本；`instanceId` 标识本次加载的运行角色。同一包可有多个不同实例，A 的位置、C 的上下文、D 的接收对象均使用 instanceId，不用包 ID 替代。
- `sessionId` 标识会话；重置产生新 sessionId，既有异步结果不得写入新会话。标识为非空字符串，运行实例不可使用保留值 `all`。
- 请求公共字段：`protocol: "wimipet/1"`、`sessionId`、`requestId`、`expiresAtMs`、`type`、`payload`。事件另含 `eventId`、生产方 `source: A|B|C|D|assembly`、`sourceSeq`、`atMs`，响应包含原 requestId。sourceSeq 在生产方实例内单调递增，重启需改变生产方实例标识 `sourceInstanceId`。下文类型描述为逻辑字段，公共字段放信封，其余放 payload，不重复两份状态。
- `atMs` 是 UTC epoch 整数毫秒，仅用于展示和关联；超时、冷却和物理推进使用模块内部单调时间。禁止将单调秒直接乘 1000 当作 epoch。消息排序使用 C 的 seq，不按时钟重新排序；回放显式声明回放时钟。
- 同一 sessionId/requestId 的重复请求不得重复执行；同 ID 不同 payload 返回 `conflict`。接收方至少保留记录至 expiresAtMs；过期返回 stale，不静默重放。容量不足拒绝新请求，不能提前逐出仍有效的去重记录。超时推进使用接收时计算的单调截止时间，跨进程需由宿主保证 epoch 基准一致。
- 失败为 `{code, message, retryable}`，基础 code：`invalid-input|unsupported-version|unsupported-capability|not-found|conflict|stale|cancelled|timeout|unavailable|internal`。不泄露密钥或提供方私有响应。
- 所有异步请求有明确截止时间或可取消信号，跨进程不发送语言对象/AbortSignal/原生句柄，改用 requestId 与取消请求。取消不等于上游模型停止计费。订阅必须可退订，退出需终结任务和释放资源。

### 2. B 输出的角色包及 A 的加载能力

- 统一离线包为 UTF-8 JSON：`{protocol, packageId, revision, character, assets, actions}`；`character: {name, persona, appearance, avatarAssetId?}`。persona 为用户自由文本，最多 20000 Unicode 码点，各消费方不得静默截断。
- `assets` 为资源 ID 到 `{mime: "image/png", dataBase64, width, height}` 的映射；只内嵌确认素材，不含远程地址、凭据或草稿。尺寸必须与实际解码结果相符。
- 首个互通配置：单帧不超过 512×512、单资源解码前二进制不超过 2MiB、资源最多 256 个、包 UTF-8 总大小不超过 32MiB、动作 1–64 个、单动作 1–128 帧。超出配置可保留为创作草稿，但不能标为 v1 可运行包。
- 动作：`{actionId, name, meaning, loop, allowedStates, priority, motion: "in-place", contactKind, frames}`；actionId 包内唯一，priority 为 0–100 整数。v1 不支持 root-motion，不能丢掉位移后假装原地动作。
- 身体状态枚举：`standing|lying|clinging|hanging|falling|dragging`；接触类型：`feet|body|top-grip|side-grip`。这些描述必须由制作端确认，装配不能猜测。允许状态是必要条件，A 仍负责实际合法性。
- 每帧为 `{assetId, durationMs, contact: {x,y}}`，durationMs 为 16–10000 整数；接触点为该帧左上原点的像素坐标，范围含画布边界。单动作画布尺寸一致；需要不同接触点时逐帧填写，不混用归一化坐标。
- 必须有站立空闲 `idle`，loop=true、contactKind=feet，allowedStates 覆盖 standing/falling/dragging。不能以 feet idle 代替悬挂身体；lying/clinging/hanging 能力由相应真实姿态资产及 A 实现共同决定。A 旧的“idle 覆盖所有状态”约束需迁移，不能倒逼 B 虚报动作能力。
- A 通过 `loadCharacter({instanceId, package})` 完整校验、实际解码，原子返回 `CharacterLoaded {instanceId, packageId, revision, supportedActions, supportedPostures}` 或错误。supportedActions 含动作 ID、含义与允许状态；C 只能选择这份已加载能力列表，不能使用独立硬编码 nod/look 清单。
- 角色包源配置归 B；运行实例映射归装配，身体归 A。修改版本通过显式重新加载，更新 C/D 快照；失败保持原可用版本。B/A 必须用同一个导出包完成验收，明确缺少姿态时的可用能力。

### 3. D → C 用户输入与附件

- `UserInput {requestId, sessionId, kind: "message"|"story-event", recipient: "all"|instanceId, text, attachments}`。至少有非空白 text 或一个附件；图片无正文是合法输入，不添加伪造用户台词绕过校验。
- 基线：text 最多 4000 Unicode 码点，attachments 最多 1 个，仅 PNG/JPEG，二进制不超过 2MiB；多图、WebP/GIF 或更长正文需双方声明扩展支持，D 不能先接受再静默丢弃。单位 1MiB = 1048576 字节。
- 附件：`{attachmentId, name, mime, sizeBytes, contentRef}`。contentRef 是装配资源桥可解析的不透明引用，不是浏览器 File、blob URL、任意路径或第三方地址。资源桥保存字节并验证类型/大小，C 的模型适配器通过注入的 resolver 读取；D 通过资源桥取得独立 `previewUrl`（受信 http(s) 或当前浏览器 blob），不把它当模型输入引用。
- 资源桥只管理资源生命周期，不理解对话。草稿取消可释放未提交引用；已提交引用在当前会话及在途请求使用期间有效，会话释放后清理。模型提供方需要编码时由 C 的提供方适配器处理。
- C 返回 `InputReceipt {requestId, accepted, error?}`；accepted 仅代表输入有效且已入队。D 仅在受理后清空草稿，处理结果从消息/状态源取得；附件准备失败或输入拒绝保留草稿，不自动改发全体。

### 4. C → D 消息与只读视图

- C 是消息 List、可见性和顺序的唯一拥有者。`Message {messageId, seq, atMs, speaker, audience, kind, text, attachments, requestId?}`。
- `speaker: {type: "user"|"character"|"environment"|"director"|"body", instanceId?, name, avatarRef?}`；character 必须带 instanceId，历史显示身份保留在消息中。`audience` 为 `"all"` 或 instanceId 数组；kind 为 `message|story-event|desktop-observation|action-feedback`。
- C 提供 `getView(viewer)` 与 `subscribe(viewer, listener)`；viewer 明确区分用户视图与角色视图。用户可见私聊不意味着其他角色可见。公开生成上下文须按输出受众再次筛选，不能引用先前私聊到群聊。
- 视图为 `{sessionId, revision, messages, processing}`，revision 在该会话变化时递增；同一时刻快照原子一致。subscribe 不在返回前同步回调，返回退订函数；宿主启动订阅后取快照，利用 revision 去重和补取，不能漏掉订阅切换时更新。
- 装配组合 C 的获准消息视图、A 的位置和 B 的身份摘要成 D 所需只读快照。可以有可丢弃视图缓存，不得有第二份可编辑权威消息源。D 不读取 C 调试 snapshot 来规避过滤，也不能把 demo/host 当正式消息源。
- D 使用同一来源切换气泡/面板，按 C seq 保序；消息获取时刻不替换 atMs，不能使历史消息反复变成新气泡。头像/附件显示地址由资源桥解析。

### 5. C ↔ A 动作与位置

- `ActionCommand {requestId, sessionId, instanceId, actionId, enqueue}`；A 反馈同一请求和实例的 `ActionResult {requestId, sessionId, instanceId, status, reasonCode?, message?}`。
- 状态机：发出请求后为 requested；可进入 queued、accepted 或 rejected；queued 可进入 accepted/rejected/interrupted/failed；accepted 可进入 completed/interrupted/failed。rejected/completed/interrupted/failed 为终态，不能复活；requested 尚未被接收时的传输失败由请求错误表达。
- 循环动作不自行 completed；拖拽或取消是 interrupted；播放故障是 failed。C 必须保留这些事实，不能把未支持终态映射成完成。排队后仍需 A 重新验证可执行性。
- A 发出 `Position {instanceId, x, y, originX, originY, width, height, anchor: {x,y}, coordinateSpace: "desktop-physical-px", displayId, scaleFactor, visible}`；x/y 为角色图像左上角，anchor 为图像内像素接触点；originX/originY 为目标显示器桌面原点，可为负，scaleFactor 为物理像素/CSS 像素比例。
- 装配根据原生宿主的舞台原点及每显示器变换将角色位置转换为 D 的舞台 CSS 像素；跨屏拆分/重定位需要宿主明确处理。D 不推导物理运动，不直接混用 A 接触点与图像左上角。

### 6. C ↔ A 观察与 D 设置

- C 管理 `ObservationPolicy {policyRevision, paused, scope: "active-window"|"desktop", cooldownMs, excludedRegions}`；cooldownMs 为 0–300000 整数，排除矩形采用桌面物理像素。D 提交具体数值，不再传无法解释的 low/balanced 枚举；预设只在 UI 映射到明确数值。
- A 执行 `applyObservationPolicy(policy)` 并回报实际 policyRevision，C/D 不在 A 确认前宣称暂停已生效；配置应用期间 C 先禁止新观察，失败保留可见错误。C 自己也必须停止分析和发布旧画面结果。
- `CaptureRequest {requestId, sessionId, policyRevision, sceneId, scope}`；`CaptureResult {requestId, sessionId, policyRevision, sceneId, capturedAtMs, source: "simulation"|"native", rect, contentRef}` 或明确错误。active-window 对应 A 采集时解析的实际窗口范围；desktop 仍受授权与排除策略约束。
- 模型返回时核对 sessionId/sceneId/policyRevision；暂停再恢复也不能接受旧 revision 画面。采集与模型不阻塞身体主循环。
- 所有自有角色、气泡、面板宿主在采集前向 A 登记排除目标；原生句柄仅在平台适配器内部保留，对外用不透明 targetId。无法保证本次排除时返回 unavailable，不默默截图自身输出并声称已排除。
- UI 设置请求含 requestId，最终展示上游确认策略；仅本地勾选不能代表真实暂停。截图默认不落盘，长期保存策略另议。

### 7. 装配边界、能力握手与迁移门槛

- 启动时各模块声明 `{protocol, module, implementationVersion, capabilities, limits}`；装配核对共同配置，未实现项显式拒绝或显示降级模式，不仅判断版本字符串。
- 装配可做 JSON/文件解析、资源引用、字段转换、单位与坐标换算、线程/进程转发和生命周期连接；不能编造缺失动作接触类型、放大权限、添加用户台词、吞掉附件、伪造动作完成或维护第二份 List。
- Python/Tk、asyncio、浏览器的宿主方案仍需选型；必须先确定线程归属、IPC、透明桌面宿主、焦点穿透、截止时间与关闭顺序。浏览器舞台只能标为舞台模式，不标为原生桌面气泡。
- 根契约由装配工作负责人协调维护；B 主责包、A 主责身体与采集、C 主责对话与输入、D 主责呈现。变更先更新此处，再更新各模块映射和共同样例；不在各自草案另立冲突标准。
- v1 迁移至少验证：同一 B 包进入 A；C→A→C 完成/拒绝/排队/打断/失败；图片无正文进入 C；私聊后公开接话；C 消息到 D；A 位置到 D；暂停时在途截图；重置后迟到结果；同包多实例及退出清理。
- 本次只统一文档，现有分支的格式差异与未实现能力仍是待修复项。不得仅更新版本标识或通过演示替身就宣称联调通过。
