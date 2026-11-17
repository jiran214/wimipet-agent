# D 独立体验使用的共同样例副本

`character-package.json` 原样复制自规范同步提交 a24c406101262d35a53da7f1f7bd0d9639d19994 的 `docs/contracts/fixtures/v1/character-package.json`。原始素材来自 B fbe7bfb、A 3c08b1a，由协调层整理为共同包；不是本轮 B 真实导出结果。

D 核心测试计算此副本和共享原件的 SHA-256 并要求完全相同；不修改共同样例。浏览器体验不访问仓库其他模块，直接加载本副本，经公开 summary 消费器建立 instance-one、instance-two 两个独立接收对象并显示头像。第三角色为本模块占位样例。

D 只消费身份摘要和预览引用，不负责代替 A/B 做完整角色包动作与 PNG 像素校验。生产装配应在 B/A 校验通过后注入正式摘要与资源桥。本项不证明 B→A 或 C→D 实际消费者链路已贯通。
