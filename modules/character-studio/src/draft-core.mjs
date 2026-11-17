export const VERSION = "character-studio/0.1";
export const clone = (value) => structuredClone(value);
const idOK = (value) => typeof value === "string" && !!value.trim();
const finite = (n, min, max) => Number.isFinite(n) && n >= min && n <= max;
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const b of bytes) {
    crc ^= b;
    for (let bit = 0; bit < 8; bit++)
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
export function pngSize(data) {
  if (
    typeof data !== "string" ||
    !/^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(data) ||
    data.length > 8_000_000
  )
    throw Error("资源必须是小于 6 MB 的 PNG");
  const bytes = Uint8Array.from(atob(data.split(",")[1]), (c) =>
    c.charCodeAt(0),
  );
  if (
    bytes.length < 45 ||
    [137, 80, 78, 71, 13, 10, 26, 10].some((n, i) => bytes[i] !== n) ||
    String.fromCharCode(...bytes.slice(12, 16)) !== "IHDR" ||
    String.fromCharCode(...bytes.slice(-8, -4)) !== "IEND"
  )
    throw Error("PNG 文件头或结束块无效");
  const view = new DataView(bytes.buffer);
  let offset = 8,
    hasData = false;
  while (offset < bytes.length) {
    if (offset + 12 > bytes.length) throw Error("PNG 块被截断");
    const length = view.getUint32(offset),
      end = offset + 12 + length;
    if (end > bytes.length) throw Error("PNG 块长度无效");
    const type = String.fromCharCode(...bytes.slice(offset + 4, offset + 8));
    if (crc32(bytes.subarray(offset + 4, end - 4)) !== view.getUint32(end - 4))
      throw Error("PNG 校验和错误");
    if (type === "IDAT") hasData = true;
    if (type === "IEND" && (length !== 0 || end !== bytes.length))
      throw Error("PNG 结束块无效");
    offset = end;
  }
  if (!hasData || view.getUint32(8) !== 13)
    throw Error("PNG 缺少图像数据或尺寸头无效");
  const size = [view.getUint32(16), view.getUint32(20)];
  if (size.some((n) => n < 1 || n > 2048))
    throw Error("PNG 尺寸应为 1–2048 像素");
  return size;
}
export function validate(pkg) {
  const errors = [];
  const check = (ok, message) => {
    if (!ok) errors.push(message);
  };
  if (!pkg || typeof pkg !== "object") return ["角色包必须是对象"];
  try {
    check(JSON.stringify(pkg).length <= 32_000_000, "角色包超过 32 MB");
  } catch {
    return ["角色包必须可序列化为 JSON"];
  }
  check(pkg.format === VERSION, "不支持的角色包格式");
  check(Number.isInteger(pkg.revision) && pkg.revision > 0, "版本必须为正整数");
  const c = pkg.character;
  check(c && idOK(c.id), "角色标识无效");
  check(
    c && typeof c.name === "string" && c.name.trim().length > 0,
    "角色名字不能为空",
  );
  check(
    c && typeof c.persona === "string" && [...c.persona].length <= 20000,
    "人设必须是文本且不超过 20000 字",
  );
  check(c && typeof c.appearance === "string", "形象说明必须是文本");
  const assets = pkg.assets;
  if (!assets || typeof assets !== "object" || Array.isArray(assets))
    return [...errors, "缺少资源表"];
  const dimensions = new Map();
  check(Object.keys(assets).length <= 256, "资源不能超过 256 张");
  for (const [id, data] of Object.entries(assets)) {
    check(idOK(id), `资源标识无效：${id}`);
    try {
      dimensions.set(id, pngSize(data));
    } catch (e) {
      errors.push(`${id}: ${e.message}`);
    }
  }
  for (const field of ["avatar", "reference"])
    check(!c?.[field] || dimensions.has(c[field]), `${field} 引用缺失`);
  if (
    !Array.isArray(pkg.actions) ||
    !pkg.actions.length ||
    pkg.actions.length > 64
  )
    return [...errors, "需要 1–64 个动作"];
  const ids = new Set();
  for (const a of pkg.actions) {
    if (!a || typeof a !== "object") {
      errors.push("动作格式无效");
      continue;
    }
    check(idOK(a.id) && !ids.has(a.id), `动作标识无效或重复：${a.id}`);
    ids.add(a.id);
    check(typeof a.name === "string" && !!a.name.trim(), "动作名称不能为空");
    check(
      typeof a.meaning === "string" && !!a.meaning.trim(),
      "动作表达含义不能为空",
    );
    check(typeof a.loop === "boolean", `${a.id}: loop 必须是布尔值`);
    check(
      ["in-place", "root-motion"].includes(a.motion),
      `${a.id}: 位移模式无效`,
    );
    check(finite(a.stride, 0, 2048), `${a.id}: 步幅无效`);
    check(
      Array.isArray(a.contacts) && a.contacts.length <= 16,
      `${a.id}: 接触点格式无效`,
    );
    for (const p of Array.isArray(a.contacts) ? a.contacts : [])
      check(
        p && idOK(p.name) && finite(p.x, 0, 1) && finite(p.y, 0, 1),
        `${a.id}: 接触点应在 0–1 之间`,
      );
    if (!Array.isArray(a.frames) || !a.frames.length || a.frames.length > 128) {
      errors.push(`${a.id}: 需要 1–128 帧`);
      continue;
    }
    let first;
    for (const f of a.frames) {
      const size = dimensions.get(f?.asset);
      check(!!size, `${a.id}: 资源引用缺失 ${f?.asset}`);
      check(
        f && finite(f.durationMs, 16, 10000),
        `${a.id}: 帧时长应在 16–10000 ms`,
      );
      if (size) {
        first ??= size;
        check(
          size[0] === first[0] && size[1] === first[1],
          `${a.id}: 帧尺寸不一致`,
        );
      }
    }
  }
  return errors;
}
export function assertPackage(pkg) {
  const errors = validate(pkg);
  if (errors.length) throw Error(errors.join("\n"));
  return clone(pkg);
}
export function parsePackage(text) {
  if (text.length > 32_000_000) throw Error("角色包超过 32 MB");
  return assertPackage(JSON.parse(text));
}
export function confirmDraft(project) {
  const next = clone(project.draft);
  next.revision = (project.confirmed?.revision ?? 0) + 1;
  project.confirmed = assertPackage(next);
  project.draft = clone(next);
  return clone(next);
}
export function acceptCandidate(project, candidate) {
  const next = clone(project.draft);
  for (const [id, data] of Object.entries(candidate.assets))
    if (Object.hasOwn(next.assets, id) && next.assets[id] !== data)
      throw Error(`资源标识冲突：${id}；请使用唯一资源标识，草稿未改变`);
  next.assets = { ...next.assets, ...clone(candidate.assets) };
  next.actions = next.actions.filter((a) => a.id !== candidate.action.id);
  next.actions.push(clone(candidate.action));
  assertPackage(next);
  project.draft = next;
}
export function fakeProvider(
  sample,
  { outcome = "success", delay = 1200 } = {},
) {
  return async (input, signal) => {
    await new Promise((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer);
        reject(new DOMException("已取消", "AbortError"));
      };
      const timer = setTimeout(() => {
        signal.removeEventListener("abort", abort);
        resolve();
      }, delay);
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
    });
    if (outcome === "failure")
      throw Error("固定失败场景：模拟提供方不可用，可重试");
    const action = clone(sample.actions[0]);
    action.id = input.id;
    action.name = input.name;
    action.meaning = input.description;
    return { action, assets: clone(sample.assets), source: "fixed-fixture-v1" };
  };
}
export class GenerationTask {
  constructor(
    provider,
    {
      timeoutMs = 60000,
      setTimer = (fn, ms) => setTimeout(fn, ms),
      clearTimer = (id) => clearTimeout(id),
    } = {},
  ) {
    this.provider = provider;
    this.timeoutMs = timeoutMs;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.state = "idle";
    this.candidate = null;
    this.error = null;
  }
  async run(input) {
    if (this.state === "processing") throw Error("已有任务进行中");
    const controller = new AbortController();
    this.controller = controller;
    const timer = this.setTimer(() => {
      if (this.controller === controller && this.state === "processing") {
        this.state = "failed";
        this.error = "生成请求超过截止时间";
        controller.abort(new DOMException("生成请求超时", "TimeoutError"));
      }
    }, this.timeoutMs);
    this.state = "processing";
    this.candidate = null;
    this.error = null;
    let rejectAborted;
    const aborted = new Promise((_, reject) => {
      rejectAborted = () => reject(controller.signal.reason);
      controller.signal.addEventListener("abort", rejectAborted, {
        once: true,
      });
    });
    try {
      const candidate = await Promise.race([
        this.provider(clone(input), controller.signal),
        aborted,
      ]);
      if (controller.signal.aborted || this.controller !== controller) return;
      assertPackage({
        format: VERSION,
        revision: 1,
        character: { id: "probe", name: "检查", persona: "", appearance: "" },
        actions: [candidate?.action],
        assets: candidate?.assets,
      });
      const { decodePNG } = await import("./png.mjs");
      for (const data of Object.values(candidate.assets))
        await decodePNG(data, { signal: controller.signal });
      if (controller.signal.aborted || this.controller !== controller) return;
      this.candidate = clone(candidate);
      this.state = "success";
    } catch (e) {
      if (this.controller !== controller) return;
      if (controller.signal.aborted) {
        if (controller.signal.reason?.name === "TimeoutError") {
          this.state = "failed";
          this.error = "生成请求超过截止时间";
        } else this.state = "cancelled";
      } else {
        this.state = "failed";
        this.error = e.message;
      }
    } finally {
      this.clearTimer(timer);
      controller.signal.removeEventListener("abort", rejectAborted);
    }
  }
  cancel() {
    if (this.state === "processing") {
      this.controller.abort();
      this.state = "cancelled";
    }
  }
  discard() {
    this.cancel();
    this.candidate = null;
    this.state = "idle";
  }
}
