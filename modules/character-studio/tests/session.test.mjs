import { test } from "node:test";
import assert from "node:assert/strict";
import { StudioSession } from "../src/session.mjs";
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
};
const request = (session, id = "one") => ({
  protocol: "wimipet/1",
  sessionId: session.sessionId,
  requestId: id,
  expiresAtMs: 2000,
  type: "character.confirm",
  payload: { value: 1 },
});
test("重复请求一次执行、不同 payload 冲突，容量不提前逐出有效记录", async () => {
  const session = new StudioSession({
    sessionId: "test",
    epochNow: () => 1000,
    monoNow: () => 0,
    maxRequests: 1,
  });
  let calls = 0;
  const wait = deferred();
  const r = request(session),
    first = session.execute(r, async () => {
      calls++;
      await wait.promise;
      return "done";
    });
  const second = session.execute(r, () => {
    throw Error("不应重复执行");
  });
  assert.equal(
    (await session.execute({ ...r, payload: { value: 2 } }, () => {})).error
      .code,
    "conflict",
  );
  assert.equal(
    (await session.execute(request(session, "two"), () => {})).error.code,
    "unavailable",
  );
  wait.resolve();
  assert.deepEqual(await first, await second);
  assert.equal(calls, 1);
  session.close();
});
test("过期、取消、关闭和重置返回结构化结果，迟到处理器不复活", async () => {
  const session = new StudioSession({
    sessionId: "test",
    epochNow: () => 1000,
  });
  assert.equal(
    (await session.execute({ ...request(session), expiresAtMs: 999 }, () => {}))
      .error.code,
    "stale",
  );
  const wait = deferred(),
    started = deferred();
  const r = request(session);
  const first = session.execute(r, async () => {
    started.resolve();
    await wait.promise;
    return "late";
  });
  await started.promise;
  session.reset("new");
  assert.equal((await first).error.code, "stale");
  wait.resolve();
  assert.equal((await session.execute(r, () => {})).error.code, "stale");
  const next = session.execute(request(session), () => new Promise(() => {}));
  session.cancel("one");
  assert.equal((await next).error.code, "cancelled");
  session.close();
  assert.equal(
    (await session.execute(request(session), () => {})).error.code,
    "unavailable",
  );
});
test("截止时间使用单调时间与可控定时器，不按固定 sleep 猜测", async () => {
  let mono = 10,
    timeout;
  const session = new StudioSession({
    epochNow: () => 1000,
    monoNow: () => mono,
    setTimer: (fn) => {
      timeout = fn;
      return 1;
    },
    clearTimer: () => {},
  });
  const pending = session.execute(
    request(session),
    () => new Promise(() => {}),
  );
  mono = 1010;
  timeout();
  assert.equal((await pending).error.code, "timeout");
  session.close();
});
test("未知版本及处理器错误不泄露提供方私有响应", async () => {
  const session = new StudioSession({ epochNow: () => 1000 });
  assert.equal(
    (await session.execute({ ...request(session), protocol: "old" }, () => {}))
      .error.code,
    "unsupported-version",
  );
  const result = await session.execute(request(session), () => {
    throw Error("secret-provider-body");
  });
  assert.equal(result.error.code, "internal");
  assert.ok(!JSON.stringify(result).includes("secret"));
  session.close();
});
test("请求内容快照与键顺序稳定；接收后墙上时钟跳变不改变单调截止", async () => {
  let epoch = 1000;
  const session = new StudioSession({
    sessionId: "test",
    epochNow: () => epoch,
    monoNow: () => 0,
  });
  const input = { ...request(session), payload: { a: 1, b: 2 } };
  const pending = session.execute(input, (payload) => payload);
  input.payload.a = 9;
  epoch = 10000;
  const duplicate = session.execute(
    { ...input, payload: { b: 2, a: 1 } },
    () => {
      throw Error("重复执行");
    },
  );
  assert.deepEqual((await pending).payload, { a: 1, b: 2 });
  assert.deepEqual(await pending, await duplicate);
  session.close();
});
