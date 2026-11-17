# B 模块候选接口 0.1

状态：本模块已实现并自测，**未与 A/C/D 协商为正式协议，未声称消费者兼容**。这里记录实现选择，不新增产品要求。样例：`modules/character-studio/fixtures/sample.character.json`。不要求其他模块采用 Node.js 或本模块代码。

## 离线角色包

UTF-8 JSON 单文件；`format: "character-studio/0.1"`；`revision` 为确认快照的正整数版本。导入仅接受这个格式，未来版本拒绝，不猜测迁移。单文件上限 32,000,000 字符，浏览器文件导入还限制 32,000,000 字节；大文件受本地存储容量进一步限制。

| 字段 | 当前语义 |
| --- | --- |
| `character.id` | 稳定角色标识，1–80 位 ASCII 字母、数字、短横线、下划线 |
| `character.name` | 1–100 字名字，不得全空白 |
| `character.persona` | 自由文本，最多 20,000 字；没有预设 AI 自知或世界观约束 |
| `character.appearance` | 自由形象说明文本 |
| `character.avatar/reference` | 可选，指向资源表标识，不是 URL 或文件路径 |
| `assets` | 标识 → `data:image/png;base64,...`；PNG 嵌入包内，无路径基准、远程下载或路径穿越问题 |
| `actions` | 1–64 个确认动作，id 在包内唯一 |

每张图片不超过约 6 MB（data URL 长度不超过 8,000,000），最大 2048×2048；资源最多 256 张。结构校验检查 PNG 签名、IHDR、块边界、CRC、IDAT、IEND；浏览器导入/确认还实际解码。消费者应自行解码并按自身资源限制拒绝异常图片。单动作所有帧画布必须同尺寸，可不同于其他动作；参考与头像不要求和动作同尺寸。

动作字段：

```json
{
  "id": "idle",
  "name": "轻轻呼吸",
  "meaning": "安静陪伴、等待回应",
  "loop": true,
  "motion": "in-place",
  "stride": 0,
  "contacts": [{"name":"feet","x":0.5,"y":0.86}],
  "frames": [{"asset":"sample_0","durationMs":220}]
}
```

名称与含义非空。帧顺序即播放顺序，1–128 帧；每帧时长 16–10,000 ms，允许有限小数。接触点 0–16 个，名称使用标识语法，x/y 为相对完整画布左上角的归一化坐标，范围 [0,1]；首版点位对整个动作静态生效。`motion` 为 `in-place` 或 `root-motion`，`stride` 为每完整周期的名义水平步幅像素（0–2048）；方向、逐帧根位移和运行时协调尚未约定。预览不执行桌面位移。

草稿、候选和确认快照归本模块所有。导出只使用确认快照；不导出任务、凭据或提供方私有响应。外部消费方独立读 JSON 和嵌入资源，不要求制作台常驻；通过装配层显式重新加载或切换版本，热更新未实现。

## 核心公开入口

`src/core.mjs` 无其他业务模块依赖，可由浏览器或 Node.js 导入：

- `validate(pkg): string[]`、`assertPackage(pkg): 深拷贝`、`parsePackage(text): 深拷贝`：验证/解析候选协议。
- `confirmDraft({draft,confirmed})`：验证后递增 revision，写入独立确认快照，失败不更改确认版本。
- `acceptCandidate(project,{action,assets,source})`：仅在全包验证成功后替换同 id 动作或新增，资源同 id 内容冲突时拒绝，不覆盖其他动作使用的图片。
- `GenerationTask(provider)`：`run(input)`、`cancel()`、`discard()`；状态 `idle → processing → success / failed / cancelled`。`candidate` 仅成功时存在，调用方显式采用。失败可新建任务重试。状态不是全局单例。
- 提供方签名：`async (input, AbortSignal) => {action, assets, source}`。`fakeProvider(sample,{outcome,delay})` 使用固定包实现相同接口。

调用方应序列化同一个项目的变更，不在处理中的同一任务对象上重复 `run`。UI 为每次生成创建独立任务，切换项目取消旧任务并忽略其响应。真实结果先经过结构和 PNG 解码校验，确认前再检查完整包。

## 通用真实适配器 HTTP

本机 `POST /api/generate` 仅接受本地同源 Origin，转发到环境变量配置的 HTTPS URL，不跟随重定向。示例请求体：

```json
{"id":"idle","name":"轻轻呼吸","description":"轻轻呼吸，保持轮廓","reference":"data:image/png;base64,..."}
```

`reference` 可为 null。适配器返回 `{"action":上述动作对象,"assets":资源表}`。其自身负责厂商鉴权、生成、图像处理和格式转换；本模块不承诺任意厂商 API 可直接填写 URL。可选环境密钥以 Bearer 发送；错误仅返回统一信息或上游 HTTP 状态码，避免回显提供方正文。60 秒超时，最大输入约 16 MB，最大响应 32 MB 字符；失败不改确认快照。

## 待协商

正式包格式与版本迁移、加载事件、位移单位和方向、逐帧接触点、动作最低集合、资源预算、消费者支持的格式，均需装配与消费模块确认。当前样例仅证明 B 模块可以独立构建、验证、离线交付，不构成联调通过证据。
