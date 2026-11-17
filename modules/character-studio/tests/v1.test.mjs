import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { deflateSync } from "node:zlib";
import { createHash } from "node:crypto";
import {
  clone,
  validate,
  parsePackage,
  assertPackage,
  fromPackage,
  toPackage,
  confirmDraft,
  exportPackage,
  acceptCandidate,
  getCapabilities,
  negotiate,
  serializePackage,
} from "../src/core.mjs";
import { decodePNG } from "../src/png.mjs";
import { GenerationTask } from "../src/draft-core.mjs";
const text = await readFile(
  new URL("../fixtures/common-v1.character.json", import.meta.url),
  "utf8",
);
const sample = JSON.parse(text);
const project = () => ({
  draft: fromPackage(sample),
  confirmed: clone(sample),
});
test("共同样例副本逐字节等于只读源；真实解码、公开往返保留全部字段", async () => {
  assert.equal(
    text,
    await readFile(
      new URL(
        "../../../docs/contracts/fixtures/v1/character-package.json",
        import.meta.url,
      ),
      "utf8",
    ),
  );
  const pkg = await parsePackage(text);
  assert.deepEqual(await parsePackage(serializePackage(pkg)), pkg);
  assert.deepEqual(validate(pkg), []);
  assert.deepEqual(toPackage(fromPackage(pkg)), pkg);
  for (const a of Object.values(pkg.assets)) {
    const decoded = await decodePNG("data:image/png;base64," + a.dataBase64);
    assert.equal(decoded.width, a.width);
    assert.ok(decoded.scanlines.length > 0);
  }
});
test("确认真实 v1 包保留 packageId、头像、逐帧接触与状态；导出仅确认版本", async () => {
  const p = project();
  p.draft.actions[1].frames[1].contact = { x: 12.5, y: 42 };
  p.draft.actions[1].meaning = "新的挥手含义";
  const result = await confirmDraft(p);
  assert.equal(result.packageId, sample.packageId);
  assert.equal(result.revision, 2);
  assert.deepEqual(result.actions[1].frames[1].contact, { x: 12.5, y: 42 });
  assert.deepEqual(result.actions[1].allowedStates, ["standing"]);
  assert.equal(result.character.avatarAssetId, sample.character.avatarAssetId);
  p.draft.character.name = "新的草稿";
  assert.deepEqual(await exportPackage(p), result);
  assert.deepEqual(await parsePackage(JSON.stringify(result)), result);
});
test("禁止猜测旧草稿身体语义，拒绝 root-motion、分数时长、不完整 idle", async () => {
  for (const change of [
    (p) => (p.actions[0].motion = "root-motion"),
    (p) => (p.actions[0].frames[0].durationMs = 20.5),
    (p) => delete p.actions[0].frames[0].contact,
    (p) => delete p.actions[0].contactKind,
    (p) => (p.actions[0].allowedStates = ["standing"]),
    (p) => (p.actions = p.actions.filter((a) => a.actionId !== "idle")),
  ]) {
    const p = clone(sample);
    change(p);
    await assert.rejects(assertPackage(p));
  }
  const p = project(),
    before = clone(p.confirmed);
  p.draft.actions[0].motion = "root-motion";
  await assert.rejects(
    confirmDraft(p),
    (e) => e.code === "unsupported-capability",
  );
  assert.deepEqual(p.confirmed, before);
  assert.equal(p.draft.actions[0].motion, "root-motion");
  delete p.draft.actions[0].allowedStates;
  assert.ok(
    validate(toPackage(p.draft)).some((e) => e.path.endsWith("allowedStates")),
  );
});
test("运行预算、重复动作、像素坐标及声明尺寸严格检查", () => {
  const p = clone(sample);
  p.assets.sample_0.width = 161;
  p.actions.push(clone(p.actions[1]));
  p.actions[0].frames[0].contact.x = 161;
  p.actions[0].priority = 101;
  const errors = validate(p);
  for (const part of ["声明尺寸", "重复", "像素坐标", "优先级"])
    assert.ok(errors.some((e) => e.message.includes(part)));
  const huge = clone(sample);
  huge.assets.sample_0.dataBase64 = "A".repeat((2 * 1024 * 1024 * 4) / 3 + 8);
  assert.ok(validate(huge).some((e) => e.message.includes("2MiB")));
  const oversized = clone(sample);
  oversized.character.appearance = "界".repeat(
    Math.ceil((32 * 1024 * 1024) / 3),
  );
  assert.ok(validate(oversized).some((e) => e.message.includes("UTF-8")));
});
test("Unicode 码点与非 ASCII 包标识无损，无静默截断", async () => {
  const p = clone(sample);
  p.packageId = "角色 包 一";
  p.character.persona = "🦊".repeat(20000);
  await assertPackage(p);
  assert.equal(toPackage(fromPackage(p)).packageId, p.packageId);
  p.character.persona += "🦊";
  assert.ok(validate(p).some((e) => e.path === "character.persona"));
});
function crc(bytes) {
  let c = 0xffffffff;
  for (const b of bytes) {
    c ^= b;
    for (let i = 0; i < 8; i++) c = (c >>> 1) ^ (c & 1 ? 0xedb88320 : 0);
  }
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const name = Buffer.from(type),
    size = Buffer.alloc(4),
    sum = Buffer.alloc(4);
  size.writeUInt32BE(data.length);
  sum.writeUInt32BE(crc(Buffer.concat([name, data])));
  return Buffer.concat([size, name, data, sum]);
}
function customPNG({
  width = 1,
  height = 1,
  raw = Buffer.from([0, 255, 0, 0, 255]),
  compressed = deflateSync(raw),
} = {}) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 6;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", compressed),
    chunk("IEND", Buffer.alloc(0)),
  ]).toString("base64");
}
test("CRC 正确但 IDAT/滤波/解压长度损坏仍拒绝；尺寸超过512拒绝运行包", async () => {
  for (const png of [
    customPNG({ compressed: Buffer.from([0, 1, 2]) }),
    customPNG({ raw: Buffer.from([5, 1, 2, 3, 4]) }),
    customPNG({ raw: Buffer.from([0]) }),
    customPNG({ raw: Buffer.alloc(40) }),
  ]) {
    const p = clone(sample);
    p.assets.broken = {
      mime: "image/png",
      dataBase64: png,
      width: 1,
      height: 1,
    };
    assert.equal(validate(p).length, 0);
    await assert.rejects(
      assertPackage(p),
      (e) => e.code === "invalid-input" && e.message.includes("解码"),
    );
  }
  const p = clone(sample);
  p.assets.large = {
    mime: "image/png",
    dataBase64: customPNG({ width: 513, raw: Buffer.alloc(513 * 4 + 1) }),
    width: 513,
    height: 1,
  };
  assert.ok(validate(p).some((e) => e.message.includes("512")));
});
test("确认期间编辑、候选过期或资源冲突不覆盖确认资产", async () => {
  const p = project(),
    before = clone(p.confirmed);
  const pending = confirmDraft(p);
  p.draft.character.persona = "校验期间改变";
  await assert.rejects(pending, (e) => e.code === "stale");
  assert.deepEqual(p.confirmed, before);
  const candidate = {
    action: clone(p.draft.actions[1]),
    assets: clone(p.draft.assets),
  };
  await assert.rejects(
    acceptCandidate(p, candidate, { signature: "old" }),
    (e) => e.code === "stale",
  );
  candidate.assets.sample_0 = sample.assets.sample_1.dataBase64;
  await assert.rejects(
    acceptCandidate(p, candidate),
    (e) => e.code === "conflict",
  );
  assert.deepEqual(p.confirmed, before);
});
test("能力握手检查必需能力而非仅版本号；未知版本结构化拒绝", async () => {
  assert.equal(getCapabilities().limits.maxFrameWidth, 512);
  assert.equal(
    negotiate({ protocol: "wimipet/1", requiredCapabilities: ["png.decode"] })
      .ok,
    true,
  );
  assert.equal(
    negotiate({ protocol: "wimipet/1", requiredCapabilities: ["root-motion"] })
      .error.code,
    "unsupported-capability",
  );
  assert.equal(
    negotiate({ protocol: "wimipet/2" }).error.code,
    "unsupported-version",
  );
  await assert.rejects(
    parsePackage(JSON.stringify({ ...sample, protocol: "wimipet/2" })),
    (e) => e.toJSON().code === "unsupported-version",
  );
});
test("生成截止时间使用可控定时器，忽略取消的提供方不会阻塞退出", async () => {
  let expire, late;
  const task = new GenerationTask(() => new Promise((r) => (late = r)), {
    setTimer: (fn) => {
      expire = fn;
      return 1;
    },
    clearTimer: () => {},
  });
  const run = task.run({});
  expire();
  await run;
  assert.equal(task.state, "failed");
  assert.match(task.error, /截止/);
  late({});
  await Promise.resolve();
  assert.equal(task.candidate, null);
});
test("固定导出是真实公开核心产物，回执哈希与共同包内容一致", async () => {
  const exported = await readFile(
    new URL(
      "../fixtures/exports/common-v1.export.character.json",
      import.meta.url,
    ),
    "utf8",
  );
  const receipt = JSON.parse(
    await readFile(
      new URL(
        "../fixtures/exports/common-v1.export.receipt.json",
        import.meta.url,
      ),
      "utf8",
    ),
  );
  const pkg = await parsePackage(exported);
  const expected = clone(sample);
  expected.revision = 2;
  assert.deepEqual(pkg, expected);
  assert.equal(
    receipt.exportSHA256,
    createHash("sha256").update(exported).digest("hex"),
  );
  assert.equal(
    receipt.sourceSHA256,
    createHash("sha256").update(text).digest("hex"),
  );
});
test("不完整身体字段均返回错误；非零步幅、取消确认保留草稿与快照", async () => {
  const malformed = clone(sample);
  malformed.actions[0].allowedStates = {};
  assert.ok(validate(malformed).length);
  const p = project(),
    before = clone(p.confirmed);
  p.draft.actions[1].stride = 4;
  await assert.rejects(
    confirmDraft(p),
    (e) => e.code === "unsupported-capability",
  );
  assert.equal(p.draft.actions[1].stride, 4);
  assert.deepEqual(p.confirmed, before);
  p.draft.actions[1].stride = 0;
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(confirmDraft(p, { signal: controller.signal }));
  assert.deepEqual(p.confirmed, before);
});
test("已确认未引用 PNG 无损保留，新增创作参考图不混入运行包", async () => {
  const imported = clone(sample);
  imported.assets.author_extra = clone(sample.assets.sample_0);
  const pkg = await assertPackage(imported),
    draft = fromPackage(pkg);
  draft.assets.private_reference =
    "data:image/png;base64," + sample.assets.sample_1.dataBase64;
  draft.character.reference = "private_reference";
  assert.deepEqual(toPackage(draft), pkg);
  assert.ok(!Object.hasOwn(toPackage(draft).assets, "private_reference"));
});
test("候选真解码失败时没有部分写入", async () => {
  const p = project(),
    before = clone(p);
  const action = clone(p.draft.actions[1]);
  action.frames[0].asset = "new-broken";
  const candidate = {
    action,
    assets: {
      "new-broken":
        "data:image/png;base64," +
        customPNG({ raw: Buffer.from([5, 1, 2, 3, 4]) }),
    },
  };
  await assert.rejects(
    acceptCandidate(p, candidate),
    (e) => e.code === "invalid-input",
  );
  assert.deepEqual(p, before);
});
