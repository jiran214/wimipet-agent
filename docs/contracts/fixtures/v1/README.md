# wimipet/1 共同样例

规范来源：根目录 [AGENTS.md](../../../../AGENTS.md)。本目录只存协议样例，不是业务模块。

`character-package.json` 是首个共同消费包：组合 B 首轮原创固定 idle PNG 与 A 首轮原创固定 wave PNG。包声明站立 idle 和 wave，不声明悬挂/攀附能力。两个动作画风差异不用于评价视觉一致性；样例仅验证资源、动作和接口交换。

来源提交：B fbe7bfb、A 3c08b1a。JSON 为协调层按统一契约整理，并非 B 新实现已实际导出。后续 B 须实现导入、确认、导出往返；A 须实际加载解码；C 须从加载结果使用动作；D 须呈现身份与消息。当前不宣称这些迁移已通过。

共同验收场景：

- 同包创建 instance-one 和 instance-two，身份、位置和上下文分别管理。
- idle 循环被打断，不能自行报告 completed；standing 执行 wave 可以完成，悬挂请求应明确拒绝缺少能力。
- 仅发一张 PNG、text 为空应受理；非法类型、过大附件、重复请求和重置后迟到结果应明确处理。
- 私聊后公开接话不泄露内容；同一消息 seq/atMs 在气泡、面板和回放中保持语义一致。
- 动作 queued/accepted/completed/rejected/interrupted/failed 的完整转换、观察 policyRevision 和 sessionId 过期应分别验证。

模块可复制本样例用于离线演示，记录来源版本；不能修改副本形成自己的冲突协议。新增共同样例由装配负责人协调，业务变更归对应模块编号需求。
