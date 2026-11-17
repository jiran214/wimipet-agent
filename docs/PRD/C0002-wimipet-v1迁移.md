# C0002 — wimipet/1 输入、消息与运行事实迁移

- 日期：2026-11-17（Asia/Shanghai）
- 主责模块：C / interaction
- 状态：已实现并通过 37 项模块内机制/协议测试；真实消费者联调及真实模型效果未验收
- 规范：根 [AGENTS.md](../../AGENTS.md) 的 `wimipet/1` 是唯一公开边界。本需求不另立协议。
- 关联：[C0001](C0001-interaction.md)、[首轮复盘](../reviews/first-development/README.md)。

## 已确认目标

1. 接受正式 UserInput 信封与资源桥附件，允许图片无正文，不伪造用户台词；正文 4000、人设 20000 Unicode 码点、单张 PNG/JPEG 2MiB，拒绝超限且不静默截断。
2. 角色按 instanceId 独立管理；身份来自角色包，动作只能使用 A 的 CharacterLoaded 快照中确实可用的动作。用共同包覆盖同包多实例，不沿用旧 nod/look 预设。
3. C 保持唯一消息 List，提供 getView(viewer) 与可退订 subscribe。消息使用正式 speaker、audience、attachments、UTC epoch atMs 与会话内 seq；角色流不能使用管理员 diagnostics。
4. 请求具备 sessionId/requestId、有效期内去重、冲突拒绝、单调截止、取消与容量拒绝。重置生成新 sessionId，旧异步任务和引用不得写入新会话。
5. 保留 requested/queued/accepted/completed/rejected/interrupted/failed 的真实事实并校验转换。请求发送失败使用请求错误，不伪造已接收或完成；循环动作不得自行完成。
6. ObservationPolicy 使用递增 policyRevision；同步期间 C 禁止新观察，上游确认前不宣称暂停已生效。核对采集/模型结果的会话、场景及策略版本，暂停再恢复仍拒绝旧结果。
7. 提供能力握手、结构化错误和可报告的关闭状态。继续提供与公开核心共用实现的独立实验室、自带固定样例、重置、重放及诊断导出。

## 交付与责任

C 提供 `modules/interaction/src/public.py` 的宿主入口、正式 JSON 输入/视图、模型资源解析、动作命令与反馈消费、观察策略同步。实验室使用本模块假资源桥、模拟加载器、假身体/采集端；这些不替代 A/B/D。

共同样例只读来源为 `docs/contracts/fixtures/v1/character-package.json`。模块携带完全相同的固定副本，测试校验字节一致；C 仅校验包身份与已加载能力的关联，不替 A 做完整帧解码。生产方 B、加载消费方 A、消息消费方 D 和装配负责人须由协调对话连接并出具真实消费者测试。

## 独立验收

- 图片无正文入队后 Message.text 保持空，附件仍可由注入 resolver 读取。
- 私聊后公开接话的实际模型输入不含私聊；用户和角色的正式视图、订阅正确过滤。
- 同请求只执行一次；不同内容冲突、过期、取消、容量满、重置后旧请求均有明确结果。
- 模拟 A 覆盖完整合法动作路径、非法转换与循环动作拒绝完成。
- 策略未确认/失败时展示真实确认状态；暂停恢复、场景变化、重置使旧画面和模型结果失效。
- 订阅可释放；关闭报告残留任务和真实提供方工作线程数，不把取消标记等同于底层远端结束。

## 已验证结果与未决依赖

实际执行记录以 [验证记录](../interaction/验证记录.md) 为准，操作方式见 [运行与验收](../interaction/运行与验收.md)，宿主方法映射见 [接口与状态](../interaction/接口与状态.md)。本轮自己的共同样例、替身测试只能称 C 模块协议测试。

待真实消费者验证：A 输出 CharacterLoaded 与反馈事件、采集端实际 policyRevision、资源桥生命周期及真实截图排除；D 消费 getView/subscribe 和输入回执；装配层负责线程调度/IPC/关闭顺序。真实文本与视觉提供方、陪伴表达与节奏仍需独立人工评审。

## 暂缓

不新增长期记忆、独立故事引擎、原生桌面宿主、多图扩展或身体实现。默认冷却/自动轮数只是可调实验参数；不修改根契约或共同样例以迎合测试。
