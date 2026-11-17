// wimipet/1 公共边界。内部草稿格式仅在 draft-core.mjs 中存在。
import { clone, pngSize, VERSION as DRAFT_VERSION } from "./draft-core.mjs";
import { decodePNG } from "./png.mjs";
export { clone };
export const VERSION = "wimipet/1";
export const LIMITS = Object.freeze({
  maxFrameWidth: 512,
  maxFrameHeight: 512,
  maxAssetBytes: 2 * 1024 * 1024,
  maxAssets: 256,
  maxPackageBytes: 32 * 1024 * 1024,
  maxActions: 64,
  maxFramesPerAction: 128,
  maxPersonaCodePoints: 20000,
});
const states = [
  "standing",
  "lying",
  "clinging",
  "hanging",
  "falling",
  "dragging",
];
const kinds = ["feet", "body", "top-grip", "side-grip"];
const object = (x) => x !== null && typeof x === "object" && !Array.isArray(x);
const nonempty = (x) => typeof x === "string" && !!x.trim();
const finite = (x, min, max) => Number.isFinite(x) && x >= min && x <= max;
const integer = (x, min, max) => Number.isSafeInteger(x) && finite(x, min, max);
export const failure = (code, message, retryable = false) => ({
  code,
  message,
  retryable,
});
export class StudioError extends Error {
  constructor(errors) {
    super(errors.map((e) => e.message).join("\n"));
    this.name = "StudioError";
    this.errors = errors;
    this.code = errors[0]?.code ?? "internal";
    this.retryable = errors[0]?.retryable ?? false;
  }
  toJSON() {
    return {
      code: this.code,
      message: this.message,
      retryable: this.retryable,
      details: this.errors,
    };
  }
}
export function getCapabilities() {
  return {
    protocol: VERSION,
    module: "B",
    implementationVersion: "0.2.0",
    capabilities: [
      "character-package.import",
      "character-package.confirm",
      "character-package.export",
      "png.decode",
      "action.frame-contact",
      "generation.cancel",
      "generation.deadline",
    ],
    limits: {
      ...LIMITS,
      motions: ["in-place"],
      states: [...states],
      contactKinds: [...kinds],
    },
  };
}
export function negotiate({ protocol, requiredCapabilities = [] } = {}) {
  if (protocol !== VERSION)
    return {
      ok: false,
      error: failure("unsupported-version", "只支持 wimipet/1"),
    };
  if (!Array.isArray(requiredCapabilities))
    return {
      ok: false,
      error: failure("invalid-input", "requiredCapabilities 必须是数组"),
    };
  const own = getCapabilities();
  const missing = requiredCapabilities.filter(
    (c) => !own.capabilities.includes(c),
  );
  return missing.length
    ? {
        ok: false,
        error: failure(
          "unsupported-capability",
          `未实现能力：${missing.join(", ")}`,
        ),
      }
    : { ok: true, ...own };
}
export function validate(pkg, { requireIdle = true } = {}) {
  const errors = [];
  const check = (ok, path, message, code = "invalid-input") => {
    if (!ok) errors.push({ ...failure(code, message), path });
  };
  if (!object(pkg))
    return [{ ...failure("invalid-input", "角色包必须是对象"), path: "$" }];
  try {
    check(
      new TextEncoder().encode(JSON.stringify(pkg)).length <=
        LIMITS.maxPackageBytes,
      "$",
      "包 UTF-8 大小超过 32MiB",
    );
  } catch {
    return [{ ...failure("invalid-input", "角色包必须可序列化"), path: "$" }];
  }
  check(
    pkg.protocol === VERSION,
    "protocol",
    "不支持的角色包主版本",
    "unsupported-version",
  );
  check(nonempty(pkg.packageId), "packageId", "packageId 必须非空");
  check(
    integer(pkg.revision, 1, Number.MAX_SAFE_INTEGER),
    "revision",
    "revision 必须是安全正整数",
  );
  const c = pkg.character;
  check(object(c), "character", "缺少角色配置");
  check(nonempty(c?.name), "character.name", "名字不能为空");
  check(
    typeof c?.persona === "string" && [...(c?.persona ?? "")].length <= 20000,
    "character.persona",
    "人设最多 20000 Unicode 码点",
  );
  check(
    typeof c?.appearance === "string",
    "character.appearance",
    "形象说明必须是文本",
  );
  const dimensions = new Map();
  if (!object(pkg.assets)) {
    check(false, "assets", "缺少资源表");
    return errors;
  }
  check(Object.keys(pkg.assets).length <= 256, "assets", "资源最多 256 个");
  for (const [id, a] of Object.entries(pkg.assets)) {
    const path = `assets.${id}`;
    check(nonempty(id), path, "资源 ID 必须非空");
    if (!object(a)) {
      check(false, path, "资源必须是对象");
      continue;
    }
    check(a.mime === "image/png", path + ".mime", "只支持 PNG");
    if (
      typeof a.dataBase64 !== "string" ||
      !a.dataBase64.length ||
      a.dataBase64.length > Math.ceil(LIMITS.maxAssetBytes / 3) * 4 ||
      !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(
        a.dataBase64,
      )
    ) {
      check(false, path + ".dataBase64", "资源 Base64 无效或超过 2MiB");
      continue;
    }
    try {
      const binary = atob(a.dataBase64);
      check(binary.length <= LIMITS.maxAssetBytes, path, "资源二进制超过 2MiB");
      check(btoa(binary) === a.dataBase64, path, "Base64 不是规范编码");
      const [w, h] = pngSize("data:image/png;base64," + a.dataBase64);
      dimensions.set(id, [w, h]);
      check(w <= 512 && h <= 512, path, "运行配置仅支持不超过 512×512 的资源");
      check(a.width === w && a.height === h, path, "声明尺寸与 PNG 尺寸不符");
    } catch (e) {
      check(false, path, e.message);
    }
  }
  check(
    c?.avatarAssetId === undefined || dimensions.has(c.avatarAssetId),
    "character.avatarAssetId",
    "头像资源缺失",
  );
  if (!Array.isArray(pkg.actions)) {
    check(false, "actions", "动作必须是数组");
    return errors;
  }
  check(
    pkg.actions.length >= 1 && pkg.actions.length <= 64,
    "actions",
    "动作数量应为 1–64",
  );
  const seen = new Set();
  for (const [index, a] of pkg.actions.entries()) {
    const path = `actions[${index}]`;
    if (!object(a)) {
      check(false, path, "动作必须是对象");
      continue;
    }
    check(
      nonempty(a.actionId) && !seen.has(a.actionId),
      path + ".actionId",
      "动作标识为空或重复",
    );
    seen.add(a.actionId);
    check(
      nonempty(a.name) && nonempty(a.meaning),
      path,
      "动作名称和表达含义不能为空",
    );
    check(typeof a.loop === "boolean", path + ".loop", "loop 必须是布尔值");
    check(
      Array.isArray(a.allowedStates) &&
        a.allowedStates.length > 0 &&
        new Set(a.allowedStates).size === a.allowedStates.length &&
        a.allowedStates.every((s) => states.includes(s)),
      path + ".allowedStates",
      "必须明确允许的身体状态，不推测姿态",
    );
    check(
      integer(a.priority, 0, 100),
      path + ".priority",
      "优先级必须是 0–100 整数",
    );
    check(
      a.motion === "in-place",
      path + ".motion",
      "wimipet/1 不支持 root-motion，不能自动降级",
      "unsupported-capability",
    );
    check(
      kinds.includes(a.contactKind),
      path + ".contactKind",
      "必须明确接触类型 feet/body/top-grip/side-grip",
    );
    if (!Array.isArray(a.frames)) {
      check(false, path + ".frames", "缺少帧序列");
      continue;
    }
    check(
      a.frames.length >= 1 && a.frames.length <= 128,
      path + ".frames",
      "单动作需要 1–128 帧",
    );
    let first;
    for (const [i, f] of a.frames.entries()) {
      const fp = `${path}.frames[${i}]`;
      const size = dimensions.get(f?.assetId);
      check(!!size, fp + ".assetId", "帧资源引用缺失");
      check(
        integer(f?.durationMs, 16, 10000),
        fp + ".durationMs",
        "帧时长必须是 16–10000 整数毫秒",
      );
      if (size) {
        first ??= size;
        check(
          size[0] === first[0] && size[1] === first[1],
          fp,
          "同一动作帧尺寸不一致",
        );
        check(
          object(f?.contact) &&
            finite(f.contact.x, 0, size[0]) &&
            finite(f.contact.y, 0, size[1]),
          fp + ".contact",
          "逐帧接触点必须是画布范围内像素坐标",
        );
      }
    }
  }
  const idle = pkg.actions.find((a) => a?.actionId === "idle");
  if (requireIdle)
    check(
      idle &&
        idle.loop === true &&
        idle.contactKind === "feet" &&
        Array.isArray(idle.allowedStates) &&
        ["standing", "falling", "dragging"].every((s) =>
          idle.allowedStates.includes(s),
        ),
      "actions.idle",
      "必须有 feet 循环 idle，允许 standing/falling/dragging；不自动扩展悬挂能力",
    );
  return errors;
}
export async function assertPackage(pkg, { signal } = {}) {
  const snapshot = clone(pkg),
    errors = validate(snapshot);
  if (errors.length) throw new StudioError(errors);
  for (const [id, a] of Object.entries(snapshot.assets)) {
    signal?.throwIfAborted();
    try {
      const image = await decodePNG("data:image/png;base64," + a.dataBase64, {
        signal,
      });
      if (image.width !== a.width || image.height !== a.height)
        throw Error("解码尺寸与声明不符");
    } catch (e) {
      if (signal?.aborted) throw e;
      throw new StudioError([
        {
          ...failure("invalid-input", "PNG 真实解码失败：" + e.message),
          path: `assets.${id}`,
        },
      ]);
    }
  }
  return snapshot;
}
export async function parsePackage(text, options) {
  if (
    typeof text !== "string" ||
    new TextEncoder().encode(text).length > LIMITS.maxPackageBytes
  )
    throw new StudioError([
      failure("invalid-input", "包 UTF-8 大小超过 32MiB"),
    ]);
  let pkg;
  try {
    pkg = JSON.parse(text);
  } catch {
    throw new StudioError([failure("invalid-input", "角色包 JSON 无效")]);
  }
  return assertPackage(pkg, options);
}
// 映射保留逐帧像素接触点和作者明确提供的身体语义。旧草稿没有这些字段时
// 不做猜测，toPackage 会产生明确校验错误；参考图只留创作草稿，不交付未确认图。
export function fromPackage(pkg) {
  return {
    format: DRAFT_VERSION,
    packageId: pkg.packageId,
    confirmedAssetIds: Object.keys(pkg.assets),
    revision: pkg.revision,
    character: {
      id: pkg.packageId,
      name: pkg.character.name,
      persona: pkg.character.persona,
      appearance: pkg.character.appearance,
      ...(pkg.character.avatarAssetId
        ? { avatar: pkg.character.avatarAssetId }
        : {}),
    },
    assets: Object.fromEntries(
      Object.entries(pkg.assets).map(([id, a]) => [
        id,
        "data:image/png;base64," + a.dataBase64,
      ]),
    ),
    actions: pkg.actions.map((a) => ({
      id: a.actionId,
      name: a.name,
      meaning: a.meaning,
      loop: a.loop,
      motion: a.motion,
      stride: 0,
      allowedStates: clone(a.allowedStates),
      priority: a.priority,
      contactKind: a.contactKind,
      contacts: [],
      frames: a.frames.map((f) => ({
        asset: f.assetId,
        durationMs: f.durationMs,
        contact: clone(f.contact),
      })),
    })),
  };
}
export function toPackage(draft) {
  const movement = draft.actions.find(
    (a) => a.motion === "in-place" && a.stride !== undefined && a.stride !== 0,
  );
  if (movement)
    throw new StudioError([
      {
        ...failure(
          "unsupported-capability",
          "创作步幅不为零，运行配置不接收位移信息；请明确修改草稿，不能静默丢弃",
        ),
        path: `actions.${movement.id}.stride`,
      },
    ]);
  const used = new Set([
    ...(draft.confirmedAssetIds ?? []),
    ...draft.actions.flatMap((a) => a.frames.map((f) => f.asset)),
  ]);
  if (draft.character.avatar) used.add(draft.character.avatar);
  return {
    protocol: VERSION,
    packageId: draft.packageId ?? draft.character.id,
    revision: draft.revision,
    character: {
      name: draft.character.name,
      persona: draft.character.persona,
      appearance: draft.character.appearance,
      ...(draft.character.avatar
        ? { avatarAssetId: draft.character.avatar }
        : {}),
    },
    assets: Object.fromEntries(
      [...used]
        .filter((id) => Object.hasOwn(draft.assets, id))
        .map((id) => {
          const data = draft.assets[id];
          let size = [0, 0];
          try {
            size = pngSize(data);
          } catch {}
          return [
            id,
            {
              mime: "image/png",
              dataBase64:
                typeof data === "string" ? (data.split(",")[1] ?? "") : "",
              width: size[0],
              height: size[1],
            },
          ];
        }),
    ),
    actions: draft.actions.map((a) => ({
      actionId: a.id,
      name: a.name,
      meaning: a.meaning,
      loop: a.loop,
      allowedStates: clone(a.allowedStates),
      priority: a.priority,
      motion: a.motion,
      contactKind: a.contactKind,
      frames: a.frames.map((f) => ({
        assetId: f.asset,
        durationMs: f.durationMs,
        contact: clone(f.contact),
      })),
    })),
  };
}
export async function confirmDraft(project, options) {
  const editRevision = project.editRevision ?? 0;
  const snapshot = clone(project.draft),
    signature = JSON.stringify(snapshot),
    next = toPackage(snapshot);
  next.revision = project.confirmed
    ? project.confirmed.revision + 1
    : Math.max(1, snapshot.revision ?? 1);
  const confirmed = await assertPackage(next, options);
  options?.signal?.throwIfAborted();
  if (
    JSON.stringify(project.draft) !== signature ||
    (project.editRevision ?? 0) !== editRevision
  )
    throw new StudioError([
      failure("stale", "校验期间草稿发生变化，请重新确认"),
    ]);
  project.confirmed = confirmed;
  project.draft.revision = confirmed.revision;
  project.draft.confirmedAssetIds = Object.keys(confirmed.assets);
  return clone(confirmed);
}
export async function assertCandidate(candidate, { signal } = {}) {
  const probe = {
    protocol: VERSION,
    packageId: "candidate-check",
    revision: 1,
    character: { name: "候选检查", persona: "", appearance: "" },
    assets: candidate?.assets,
    actions: [candidate?.action],
  };
  const errors = validate(probe, { requireIdle: false });
  if (errors.length) throw new StudioError(errors);
  for (const [id, a] of Object.entries(probe.assets)) {
    try {
      await decodePNG("data:image/png;base64," + a.dataBase64, { signal });
    } catch (e) {
      if (signal?.aborted) throw e;
      throw new StudioError([
        {
          ...failure("invalid-input", "候选 PNG 真实解码失败"),
          path: `assets.${id}`,
        },
      ]);
    }
  }
  return {
    protocol: VERSION,
    action: clone(candidate.action),
    assets: clone(candidate.assets),
  };
}
export async function exportPackage(project, options) {
  if (!project.confirmed)
    throw new StudioError([failure("not-found", "尚无已确认运行版本")]);
  return assertPackage(project.confirmed, options);
}
export function serializePackage(pkg) {
  const errors = validate(pkg);
  if (errors.length) throw new StudioError(errors);
  const pretty = JSON.stringify(pkg, null, 2) + "\n";
  if (new TextEncoder().encode(pretty).length <= LIMITS.maxPackageBytes)
    return pretty;
  // 仅调整 JSON 空白，不丢字段或资源；保证真正输出的 UTF-8 字节预算。
  const compact = JSON.stringify(pkg);
  if (new TextEncoder().encode(compact).length > LIMITS.maxPackageBytes)
    throw new StudioError([failure("invalid-input", "序列化角色包超过 32MiB")]);
  return compact;
}
export async function acceptCandidate(
  project,
  candidate,
  { signature, signal } = {},
) {
  const editRevision = project.editRevision ?? 0;
  const before = JSON.stringify(project.draft);
  if (signature !== undefined && signature !== before)
    throw new StudioError([failure("stale", "候选所属草稿已变化，请重新生成")]);
  const next = clone(project.draft);
  for (const [id, data] of Object.entries(candidate.assets)) {
    if (Object.hasOwn(next.assets, id) && next.assets[id] !== data)
      throw new StudioError([failure("conflict", "候选资源与草稿冲突")]);
    try {
      await decodePNG(data, { signal });
    } catch (e) {
      if (signal?.aborted) throw e;
      throw new StudioError([failure("invalid-input", "候选 PNG 无法解码")]);
    }
  }
  signal?.throwIfAborted();
  if (JSON.stringify(project.draft) !== before)
    throw new StudioError([failure("stale", "采用期间草稿发生变化")]);
  const { acceptCandidate: acceptDraft } = await import("./draft-core.mjs");
  signal?.throwIfAborted();
  if (
    JSON.stringify(project.draft) !== before ||
    (project.editRevision ?? 0) !== editRevision
  )
    throw new StudioError([failure("stale", "采用期间草稿版本已变化")]);
  const transaction = { draft: next };
  acceptDraft(transaction, candidate);
  project.draft = transaction.draft;
  project.editRevision = editRevision + 1;
}
