import { VERSION, getCapabilities, failure, StudioError } from "./core.mjs";
const canonical = (value) =>
  Array.isArray(value)
    ? value.map(canonical)
    : value && typeof value === "object"
      ? Object.fromEntries(
          Object.keys(value)
            .sort()
            .map((key) => [key, canonical(value[key])]),
        )
      : value;

// B 的公开请求生命周期适配器；业务 handler 由调用方显式注入，保持唯一创作状态。
// 跨进程信封只含 JSON，AbortSignal 仅用于模块内部 handler。
export class StudioSession {
  constructor({
    sessionId = crypto.randomUUID(),
    maxRequests = 256,
    epochNow = () => Date.now(),
    monoNow = () => performance.now(),
    setTimer = (fn, ms) => setTimeout(fn, ms),
    clearTimer = (id) => clearTimeout(id),
  } = {}) {
    this.sessionId = sessionId;
    this.maxRequests = maxRequests;
    this.epochNow = epochNow;
    this.monoNow = monoNow;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.requests = new Map();
    this.closed = false;
  }
  getCapabilities() {
    return getCapabilities();
  }
  async execute(request, handler) {
    const respond = (error) => ({
      protocol: VERSION,
      sessionId: this.sessionId,
      requestId: request?.requestId ?? "",
      ok: false,
      error,
    });
    if (this.closed) return respond(failure("unavailable", "制作会话已关闭"));
    if (request?.protocol !== VERSION)
      return respond(failure("unsupported-version", "只支持 wimipet/1"));
    if (request.sessionId !== this.sessionId)
      return respond(failure("stale", "会话已变化"));
    if (
      typeof request.requestId !== "string" ||
      !request.requestId.trim() ||
      typeof request.type !== "string" ||
      !request.type.trim() ||
      !Number.isSafeInteger(request.expiresAtMs) ||
      request.payload === undefined
    )
      return respond(failure("invalid-input", "请求信封不完整"));
    const mono = this.monoNow(),
      epoch = this.epochNow();
    for (const [id, item] of this.requests)
      if (mono >= item.deadline) this.requests.delete(id);
    let signature, payload;
    try {
      payload = structuredClone(request.payload);
      signature = JSON.stringify(
        canonical({
          type: request.type,
          payload,
          expiresAtMs: request.expiresAtMs,
        }),
      );
    } catch {
      return respond(failure("invalid-input", "请求必须可序列化"));
    }
    const previous = this.requests.get(request.requestId);
    if (previous)
      return previous.signature === signature
        ? previous.promise
        : respond(failure("conflict", "同 requestId 的内容不同"));
    if (request.expiresAtMs <= epoch)
      return respond(failure("stale", "请求已过期"));
    if (this.requests.size >= this.maxRequests)
      return respond(
        failure("unavailable", "请求去重容量已满，未逐出有效记录", true),
      );
    const controller = new AbortController(),
      deadline = mono + request.expiresAtMs - epoch,
      sessionId = this.sessionId;
    const item = { signature, controller, deadline };
    this.requests.set(request.requestId, item);
    item.promise = (async () => {
      let timeout;
      const aborted = new Promise((_, reject) => {
        controller.signal.addEventListener(
          "abort",
          () => reject(controller.signal.reason),
          { once: true },
        );
      });
      const schedule = () => {
        const remaining = deadline - this.monoNow();
        if (remaining <= 0)
          controller.abort(
            new StudioError([failure("timeout", "请求超过截止时间", true)]),
          );
        else timeout = this.setTimer(schedule, Math.min(remaining, 2147483647));
      };
      schedule();
      try {
        const result = await Promise.race([
          Promise.resolve().then(() => {
            controller.signal.throwIfAborted();
            return handler(payload, controller.signal);
          }),
          aborted,
        ]);
        if (controller.signal.aborted) throw controller.signal.reason;
        if (this.closed || sessionId !== this.sessionId)
          throw new StudioError([failure("stale", "结果所属会话已结束")]);
        if (this.monoNow() >= deadline)
          throw new StudioError([failure("timeout", "结果已过期", true)]);
        return {
          protocol: VERSION,
          sessionId,
          requestId: request.requestId,
          ok: true,
          payload: result,
        };
      } catch (e) {
        return {
          protocol: VERSION,
          sessionId,
          requestId: request.requestId,
          ok: false,
          error:
            e instanceof StudioError
              ? e.toJSON()
              : failure("internal", "制作操作失败；未返回提供方私有响应"),
        };
      } finally {
        this.clearTimer(timeout);
      }
    })();
    return item.promise;
  }
  cancel(requestId) {
    const item = this.requests.get(requestId);
    if (!item) return { ok: false, error: failure("not-found", "找不到请求") };
    item.controller.abort(
      new StudioError([failure("cancelled", "请求已取消")]),
    );
    return { ok: true };
  }
  reset(sessionId = crypto.randomUUID()) {
    if (sessionId === this.sessionId)
      throw new StudioError([failure("conflict", "重置必须产生新 sessionId")]);
    for (const item of this.requests.values())
      item.controller.abort(new StudioError([failure("stale", "会话已重置")]));
    this.requests.clear();
    this.sessionId = sessionId;
  }
  close() {
    this.closed = true;
    for (const item of this.requests.values())
      item.controller.abort(
        new StudioError([failure("cancelled", "制作台已退出")]),
      );
    this.requests.clear();
  }
}
