# B 公开入口与统一契约映射

唯一规范：[根 AGENTS.md](../../AGENTS.md) 的 `wimipet/1`。本页仅记录 B 的实现入口、历史适配与验证范围，不另立协议或修改根限制。迁移需求 [B0002](../PRD/B0002-统一角色包交付.md)。

## 入口与能力

模块 `src/core.mjs` 是公开 facade，浏览器与 Node.js 22 使用同一实现；不导入其他业务模块。`getCapabilities()` 返回 `{protocol,module,implementationVersion,capabilities,limits}`，module=B、版本 0.2.0。`negotiate({protocol,requiredCapabilities})` 拒绝未知版本和必需能力，不只比较字符串。HTTP `GET /api/config` 同时提供声明、当前服务实例标识、真实适配器是否配置；不返回 URL 或凭据。

能力声明：角色包导入/确认/导出、PNG 实际解码、逐帧接触点、生成取消与截止时间。允许的身体状态/接触枚举是编辑器校验范围，不代表某个角色具备全部姿态。样例只提供站立 idle 和 wave；运行时能否执行仍由 A 决定。

## 角色包与原子确认

公共文件采用根规范 `{protocol,packageId,revision,character,assets,actions}`。UTF-8 字节预算使用 TextEncoder 计算，人设使用 Unicode 码点计数；PNG Base64 必须规范编码且二进制符合预算。资源声明尺寸须与实际图片一致。

- `validate(pkg)`：同步结构、预算和 PNG 块/CRC 校验，返回 `{code,message,retryable,path}` 数组；它本身不代表已通过解码。
- `await assertPackage(pkg,{signal}?)` / `await parsePackage(text,{signal}?)`：先深拷贝、结构校验，再实际解压 IDAT、重建扫描线/滤波并检查标准颜色类型、位深、Adam7 与调色板索引；返回独立运行包。浏览器显示时还使用原生 Image.decode。
- `fromPackage(pkg)` / `toPackage(draft)`：只在已校验公共包与 B 内部草稿间映射；保留包身份、版本、头像、文本、动作元数据和逐帧像素接触点。缺少语义不会补全为猜测值。从运行包导入的确认 PNG 全部保留；导出剔除新导入且未引用的创作参考图；非零步幅明确报 unsupported-capability。
- `await confirmDraft(project,{signal}?)`：捕获草稿快照、产生确认版本、完整校验解码；确认期间草稿变化返回 stale，失败/取消不更改旧确认版本。已有确认 revision +1；尚未确认的草稿保留其正版本号，至少为 1。
- `await exportPackage(project,{signal}?)`：仅返回重新校验过的 `project.confirmed`，不把草稿偷偷确认或增版。
- `serializePackage(pkg)`：为已校验快照产生实际 UTF-8 文件内容，按最终输出字节再次限制 32MiB；必要时只移除 JSON 排版空白，不丢字段/素材。界面和固定导出脚本使用同一函数。
- `await acceptCandidate(project,candidate,{signature,signal}?)`：B 内部候选事务接口；校验图片、拒绝资源冲突和过期草稿签名后再写草稿。它不等同于运行包确认，候选可缺少待用户填写的身体语义。

`StudioError` 含 `code/message/retryable`，`toJSON()` 提供相同基础字段及 details。错误遵循根代码，不输出模型正文或密钥。未知版本不自动迁移。运行限制全部按根规范；root-motion 不改成 in-place，分数时长不四舍五入，缺少姿态/接触不默认填充。

## 会话与请求生命周期

`src/session.mjs` 导出 `StudioSession`，接收根规范请求信封：`protocol/sessionId/requestId/expiresAtMs/type/payload`。这是 B 的生命周期适配，不包含第二份角色状态；handler 由同一核心闭包注入。制作台的确认与采用操作已使用该入口。

- `execute(envelope, handler)` 返回带原 requestId 的 `{protocol,sessionId,requestId,ok,payload|error}`。handler 接收 payload 副本及仅用于进程内的 AbortSignal，跨进程只传 JSON 请求及取消 ID。
- 接收时将 epoch 截止时间换算为内部单调截止时间；定时器、时钟可注入测试。处理器超时/取消即使不合作也不阻塞返回，核心写入方法仍检查信号和草稿签名。
- 同 ID/相同 JSON 内容复用同一结果，不重复执行；不同内容 conflict。有效记录保留到单调截止时间，默认 256 条，满时 unavailable，不提前逐出。字段顺序不同不构成 payload 变化。
- `cancel(requestId)`、`reset(newSessionId?)`、`close()` 中断在途请求；重置必须换 sessionId，迟到结果返回 stale，退出不再受理新请求。无事件订阅接口，不声明尚未实现的订阅能力。
- 示例 type 为 `character.confirm`、`candidate.accept`，属于 B 的方法调度名，不改变根契约。宿主负责绑定 handler 与唯一项目；不能通过任意外部代码绕过授权注入业务。

内部 `draft-core.mjs` 的 GenerationTask 只为创作任务服务：明确 idle/processing/success/failed/cancelled、60 秒默认截止时间、任务世代与取消/重试隔离，返回前解码候选图片。模拟提供方只复用固定资源。该内部结构不是跨模块角色包。

## 模型适配器（本模块内部边界）

`POST /api/generate` 是本地网页到 B 生图代理的私有传输，限制本地 Origin；不是 A/C/D 的请求入口。代理只调用预设 HTTPS URL，不接受用户输入任意目标、不跟随重定向。

向模型适配器发送 `{id,name,description,reference}`（reference 为 PNG data URL 或 null）；适配器返回 `{action,assets}`：action 使用根规范动作字段 `actionId/allowedStates/priority/contactKind/frames[].assetId/contact`，assets 使用根规范 `{mime,dataBase64,width,height}`。候选不是完整角色包，故无需它自身包含 idle；完整角色确认必须包含规范 idle。

代理先用 `assertCandidate` 检查结构、预算并真实解码，再返回规范候选；网页显式转为 B 内部待采用结构。凭据为服务环境中的可选 Bearer，错误不回显上游正文。输入约 16MB、响应约 32MB、截止 60 秒；真实厂商和生成效果仍未验证。

## 固定资产与消费方验收边界

共同样例副本 `fixtures/common-v1.character.json` 与 `docs/contracts/fixtures/v1/character-package.json` 逐字节相同，原始共享文件只读。运行 `npm run export:sample` 后，真实导出在 `fixtures/exports/common-v1.export.character.json`，回执在同目录；包为 contract-sample-moss revision 2。

本模块已验证公开核心、真实解码和浏览器导入确认导出回导；尚未运行 A 对这个文件的加载，也未运行 C/D 装配。因此不能将 B 自测称为跨模块消费通过。后续由 A 用这份实际导出加载，再由协调层连接已加载动作清单与身份。
