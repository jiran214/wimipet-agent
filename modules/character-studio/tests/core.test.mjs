import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  clone,
  validate,
  assertPackage,
  parsePackage,
  confirmDraft,
  acceptCandidate,
  fakeProvider,
  GenerationTask,
  pngSize,
} from "../src/core.mjs";
const sample = JSON.parse(
  await readFile(
    new URL("../fixtures/sample.character.json", import.meta.url),
    "utf8",
  ),
);
const makeProject = () => ({ draft: clone(sample), confirmed: clone(sample) });
test("固定 PNG 样例尺寸及离线包往返，消费者仅需 JSON 和资源表", () => {
  assert.deepEqual(validate(sample), []);
  assert.deepEqual(pngSize(sample.assets.sample_0), [160, 160]);
  const result = parsePackage(JSON.stringify(sample));
  assert.deepEqual(result, sample);
  for (const action of result.actions)
    for (const frame of action.frames) assert.ok(result.assets[frame.asset]);
});
test("缺帧、无效时长、重复动作、无效包版本被识别", () => {
  const pkg = clone(sample);
  pkg.format = "future";
  pkg.revision = 0;
  pkg.actions.push(clone(pkg.actions[0]));
  pkg.actions[0].frames[0].asset = "missing";
  pkg.actions[0].frames[1].durationMs = 0;
  const errors = validate(pkg).join("\n");
  for (const text of ["格式", "版本", "重复", "缺失", "时长"])
    assert.ok(errors.includes(text), errors);
  assert.throws(() => assertPackage(pkg));
});
test("损坏图片、帧尺寸、接触点、位移信息校验", () => {
  const pkg = clone(sample);
  pkg.assets.sample_0 = "data:image/png;base64,AAAA";
  pkg.actions[0].contacts[0].x = 2;
  pkg.actions[0].motion = "teleport";
  const bytes = Buffer.from(pkg.assets.sample_1.split(",")[1], "base64");
  bytes.writeUInt32BE(159, 16);
  let crc = 0xffffffff;
  for (const b of bytes.subarray(12, 29)) {
    crc ^= b;
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  bytes.writeUInt32BE((crc ^ 0xffffffff) >>> 0, 29);
  pkg.assets.sample_1 = `data:image/png;base64,${bytes.toString("base64")}`;
  const errors = validate(pkg).join("\n");
  for (const text of ["PNG", "尺寸不一致", "接触点", "位移"])
    assert.ok(errors.includes(text), errors);
  const damaged = Buffer.from(sample.assets.sample_0.split(",")[1], "base64");
  damaged[50] ^= 1;
  assert.throws(
    () => pngSize(`data:image/png;base64,${damaged.toString("base64")}`),
    /校验和/,
  );
});
test("编辑与确认快照分离，确认递增且失败保持快照", () => {
  const p = makeProject(),
    before = clone(p.confirmed);
  p.draft.character.persona = "完全自由的用户人设";
  assert.deepEqual(p.confirmed, before);
  const result = confirmDraft(p);
  assert.equal(result.revision, 2);
  assert.equal(result.character.persona, p.draft.character.persona);
  p.draft.character.name = "另一份草稿";
  assert.notEqual(p.confirmed.character.name, p.draft.character.name);
  const snapshot = clone(p.confirmed);
  p.draft.actions[0].frames = [];
  assert.throws(() => confirmDraft(p));
  assert.deepEqual(p.confirmed, snapshot);
});
test("模拟成功只产生候选；采用替换单动作并保留其他动作与确认版本", async () => {
  const p = makeProject();
  p.draft.actions.push({ ...clone(sample.actions[0]), id: "wave" });
  const confirmed = clone(p.confirmed);
  const task = new GenerationTask(fakeProvider(sample, { delay: 1 }));
  await task.run({ id: "idle", name: "新动作", description: "表达惊喜" });
  assert.equal(task.state, "success");
  assert.deepEqual(p.confirmed, confirmed);
  assert.equal(p.draft.actions[0].name, "轻轻呼吸");
  acceptCandidate(p, task.candidate);
  assert.equal(p.draft.actions.length, 2);
  assert.equal(
    p.draft.actions.find((a) => a.id === "idle").meaning,
    "表达惊喜",
  );
  assert.deepEqual(p.confirmed, confirmed);
});
test("失败、取消、迟到响应和放弃候选均不覆盖确认资产，可重试", async () => {
  const p = makeProject(),
    before = clone(p);
  const failure = new GenerationTask(
    fakeProvider(sample, { outcome: "failure", delay: 1 }),
  );
  await failure.run({});
  assert.equal(failure.state, "failed");
  assert.match(failure.error, /固定失败/);
  const task = new GenerationTask(fakeProvider(sample, { delay: 30 }));
  const pending = task.run({ id: "test", name: "动作", description: "说明" });
  assert.equal(task.state, "processing");
  task.cancel();
  await pending;
  assert.equal(task.state, "cancelled");
  assert.equal(task.candidate, null);
  await task.run({ id: "test", name: "动作", description: "说明" });
  assert.equal(task.state, "success");
  task.discard();
  assert.equal(task.candidate, null);
  const slow = new GenerationTask(async () => {
    await new Promise((r) => setTimeout(r, 5));
    return { action: sample.actions[0], assets: sample.assets };
  });
  const late = slow.run({});
  slow.cancel();
  await late;
  assert.equal(slow.state, "cancelled");
  assert.equal(slow.candidate, null);
  assert.deepEqual(p, before);
});
test("候选无效、资源冲突时不部分写入；多角色互相独立", () => {
  const a = makeProject(),
    b = makeProject(),
    before = clone(a);
  const bad = {
    action: {
      ...clone(sample.actions[0]),
      contacts: [{ name: "feet", x: -1, y: 0 }],
    },
    assets: sample.assets,
  };
  assert.throws(() => acceptCandidate(a, bad));
  assert.deepEqual(a, before);
  const collision = {
    action: sample.actions[0],
    assets: { sample_0: sample.assets.sample_1 },
  };
  assert.throws(() => acceptCandidate(a, collision), /资源标识冲突/);
  assert.deepEqual(a, before);
  a.draft.character.name = "角色 A";
  confirmDraft(a);
  assert.equal(b.draft.character.name, "苔苔");
});
test("错误结构给出校验信息而非破坏状态", () => {
  for (const pkg of [
    null,
    {},
    { ...clone(sample), actions: [null] },
    { ...clone(sample), actions: [{ id: "x", frames: [null] }] },
    { ...clone(sample), assets: [] },
  ])
    assert.ok(validate(pkg).length);
  assert.throws(() => parsePackage("{bad"));
  assert.throws(() => parsePackage(" ".repeat(32_000_001)), /超过/);
});
test("提供方返回无效候选标记为失败；取消后重试不会被旧响应覆盖", async () => {
  const bad = new GenerationTask(async () => ({
    action: { id: "bad" },
    assets: {},
  }));
  await bad.run({});
  assert.equal(bad.state, "failed");
  assert.equal(bad.candidate, null);
  let resolveFirst,
    resolveSecond,
    calls = 0;
  const task = new GenerationTask(
    () =>
      new Promise((resolve) => {
        if (calls++ === 0) resolveFirst = resolve;
        else resolveSecond = resolve;
      }),
  );
  const first = task.run({});
  task.cancel();
  const second = task.run({});
  resolveSecond({
    action: { ...clone(sample.actions[0]), id: "latest" },
    assets: sample.assets,
  });
  await second;
  resolveFirst({
    action: { ...clone(sample.actions[0]), id: "obsolete" },
    assets: sample.assets,
  });
  await first;
  assert.equal(task.state, "success");
  assert.equal(task.candidate.action.id, "latest");
});
