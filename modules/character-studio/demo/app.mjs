import {
  VERSION,
  clone,
  validate,
  parsePackage,
  fakeProvider,
  GenerationTask,
  pngSize,
} from "/src/draft-core.mjs";
import {
  fromPackage,
  toPackage,
  parsePackage as parseRuntime,
  confirmDraft,
  exportPackage,
  serializePackage,
  acceptCandidate,
  getCapabilities,
  validate as validateRuntime,
  StudioError,
} from "/src/core.mjs";
import { StudioSession } from "/src/session.mjs";
const lifecycle = new StudioSession();
import { decodePNG } from "/src/png.mjs";
const $ = (id) => document.getElementById(id);
const storageKey = "wimipet.character-studio.v2";
const sampleRuntime = await parseRuntime(
  await fetch("/fixtures/common-v1.character.json").then((r) => r.text()),
);
const sample = fromPackage(sampleRuntime);
const config = await fetch("/api/config").then((r) => r.json());
let state = { projects: [], selected: null },
  task = null,
  candidate = null,
  actionIndex = 0,
  frame = 0,
  playing = true,
  elapsed = 0,
  last = 0,
  animation,
  storageWarning = false,
  storageLocked = false,
  frameEditing = false;
const records = [];
$("provider").onchange = () => {
  $("mode-banner").textContent =
    $("provider").value === "real"
      ? "真实适配器模式 · 视觉质量未评审"
      : "固定素材体验 · 不调用模型";
};
const sizes = new Map();
function sizeOf(data) {
  if (!sizes.has(data)) sizes.set(data, pngSize(data));
  return sizes.get(data);
}
const uid = () => crypto.randomUUID();
const project = () => state.projects.find((p) => p.key === state.selected);
const currentAction = () =>
  candidate?.action ?? project()?.draft.actions[actionIndex];
const currentAssets = () => candidate?.assets ?? project()?.draft.assets;
function notice(message, error = false) {
  $("notice").textContent =
    message +
    (storageWarning
      ? "\n本地存储不可用：当前更改仅在内存中，请确认并导出备份。"
      : "");
  $("notice").classList.toggle("error", error || storageWarning);
}
function record(step, actual = "完成", error) {
  records.push({
    time: new Date().toISOString(),
    step,
    actual,
    mode: $("provider").value,
    revision: project()?.confirmed?.revision ?? 0,
    sessionId: lifecycle.sessionId,
    packageId: project()?.draft.packageId ?? project()?.draft.character.id,
    ...(error ? { error } : {}),
  });
}
function persist() {
  if (storageLocked) return;
  try {
    localStorage.setItem(storageKey, JSON.stringify(state));
    storageWarning = false;
  } catch {
    storageWarning = true;
  }
}
function addProject(pkg, confirmed = true) {
  const p = {
    key: uid(),
    draft: clone(pkg),
    confirmed: confirmed
      ? clone(typeof confirmed === "object" ? confirmed : toPackage(pkg))
      : null,
    editRevision: 0,
  };
  state.projects.push(p);
  state.selected = p.key;
  persist();
  return p;
}
function cancelPreview() {
  lifecycle.reset();
  task?.cancel();
  candidate = null;
  actionIndex = 0;
  frame = 0;
  elapsed = 0;
  sizes.clear();
}
function saveFields() {
  const p = project();
  if (!p) return;
  const before = JSON.stringify(p.draft.character);
  p.draft.character.name = $("name").value;
  p.draft.character.persona = $("persona").value;
  p.draft.character.appearance = $("appearance").value;
  if (JSON.stringify(p.draft.character) !== before)
    p.editRevision = (p.editRevision ?? 0) + 1;
  persist();
}
function options(select, items, value) {
  select.replaceChildren(
    ...items.map(([id, label]) => {
      const o = document.createElement("option");
      o.value = id;
      o.textContent = label;
      return o;
    }),
  );
  select.value = value;
}
function render() {
  const p = project();
  options(
    $("projects"),
    state.projects.map((p) => [
      p.key,
      `${p.draft.character.name || "未命名"} · ${p.key.slice(0, 6)}`,
    ]),
    state.selected,
  );
  const c = p.draft.character;
  for (const [id, value] of Object.entries({
    "character-id": c.id,
    name: c.name,
    persona: c.persona,
    appearance: c.appearance,
  }))
    $(id).value = value;
  $("revision").textContent =
    `${p.confirmed ? `已确认 v${p.confirmed.revision}` : "尚未确认"} / 草稿独立保存`;
  const reference = p.draft.assets[c.reference] || p.draft.assets[c.avatar];
  $("reference").hidden = !reference;
  if (reference) $("reference").src = reference;
  $("reference").alt = c.reference
    ? "角色参考图"
    : "头像预览（尚未设置参考图）";
  $("export").disabled = !p.confirmed;
  renderActions();
}
function renderActions() {
  const actions = project().draft.actions;
  options(
    $("actions"),
    actions.map((a, i) => [String(i), `${a.name} / ${a.id}`]),
    String(actionIndex),
  );
  $("accept").disabled = !candidate;
  $("discard").disabled = !candidate;
  $("candidate-label").textContent = candidate ? "待采用候选" : "草稿";
  $("source").textContent = candidate
    ? candidate.source
    : "草稿资产 · 来源见体验记录";
  renderAction();
}
function renderAction() {
  frameEditing = false;
  const a = currentAction();
  frame = 0;
  elapsed = 0;
  $("frames").replaceChildren();
  $("preview").hidden = !a;
  $("contact").hidden = !a;
  for (const id of ["adjust", "play", "replay", "scrub"]) $(id).disabled = !a;
  if (!a) {
    $("frame-label").textContent = "尚无动作，请导入固定样例";
    return;
  }
  for (const [id, value] of Object.entries({
    "action-id": a.id,
    "action-name": a.name,
    meaning: a.meaning,
    loop: String(a.loop),
    motion: a.motion,
    stride: a.stride,
    contacts: JSON.stringify(a.contacts),
    "allowed-states": (a.allowedStates ?? []).join(","),
    priority: a.priority ?? 0,
    "contact-kind": a.contactKind ?? "",
  }))
    $(id).value = value;
  $("scrub").max = a.frames.length - 1;
  a.frames.forEach((f, i) => {
    const b = document.createElement("button");
    b.title = `第 ${i + 1} 帧`;
    b.setAttribute("aria-label", `预览第 ${i + 1} 帧`);
    const img = new Image();
    img.src = currentAssets()[f.asset];
    img.alt = `${i + 1}`;
    b.append(img);
    b.onclick = () => {
      frameEditing = false;
      playing = false;
      frame = i;
      elapsed = 0;
      paint();
    };
    $("frames").append(b);
  });
  paint();
}
function paint() {
  const a = currentAction();
  if (!a) return;
  frame = Math.min(frame, a.frames.length - 1);
  const f = a.frames[frame];
  $("preview").src = currentAssets()[f.asset];
  if (!frameEditing) $("duration").value = f.durationMs;
  $("scrub").value = frame;
  $("frame-label").textContent = `${frame + 1} / ${a.frames.length} 帧`;
  $("play").textContent = playing ? "暂停" : "播放";
  [...$("frames").children].forEach((b, i) =>
    b.classList.toggle("selected", i === frame),
  );
  if (!frameEditing) $("contacts").value = JSON.stringify(f.contact ?? null);
  const p = f.contact;
  $("contact").hidden = !p;
  if (p) {
    const size = $("preview").getBoundingClientRect();
    const dims = sizeOf(currentAssets()[f.asset]);
    const scale = Math.min(size.width / dims[0], size.height / dims[1]);
    $("contact").style.left = `calc(50% + ${(p.x - dims[0] / 2) * scale}px)`;
    $("contact").style.top = `calc(50% + ${(p.y - dims[1] / 2) * scale}px)`;
  }
  const dims = sizeOf(currentAssets()[f.asset]);
  document.querySelector(".stage-caption").textContent =
    `${dims[0]} × ${dims[1]} · 橙点：当前帧像素接触点`;
}
function tick(time) {
  const delta = Math.min(time - (last || time), 100);
  last = time;
  const a = currentAction();
  if (playing && a) {
    elapsed += delta * Number($("speed").value);
    if (elapsed >= a.frames[frame].durationMs) {
      elapsed -= a.frames[frame].durationMs;
      if (frame === a.frames.length - 1) {
        if (a.loop) frame = 0;
        else playing = false;
      } else frame++;
      paint();
    }
  }
  animation = requestAnimationFrame(tick);
}
const guard = (fn) => async () => {
  try {
    await fn();
  } catch (e) {
    notice(e.message, true);
    record(
      "操作失败",
      e.message.slice(0, 400),
      e instanceof StudioError
        ? e.toJSON()
        : {
            code: "invalid-input",
            message: e.message.slice(0, 400),
            retryable: false,
          },
    );
  }
};
function resetSample() {
  cancelPreview();
  const pkg = clone(sample);
  // 恢复为新创作项目，明确生成新的包身份；导入与确认则保留原 packageId。
  pkg.packageId = pkg.character.id = `sample-${uid()}`;
  addProject(pkg);
  render();
  record("恢复样例副本");
  notice("已创建固定样例副本。原有项目和确认资产均保留。");
}
try {
  const cached = JSON.parse(localStorage.getItem(storageKey) || "null");
  if (
    cached?.projects?.length &&
    cached.projects.some((p) => p.key === cached.selected)
  ) {
    for (const p of cached.projects) {
      if (p.confirmed) await parseRuntime(JSON.stringify(p.confirmed));
      if (
        !p.draft?.character ||
        !p.draft?.assets ||
        !Array.isArray(p.draft.actions)
      )
        throw Error("invalid");
    }
    state = cached;
  }
} catch {
  storageLocked = true;
  storageWarning = true;
}
if (!state.projects.length) addProject(sample);
$("config").textContent = config.realConfigured
  ? "已配置真实适配器；生成结果仍需人工视觉评审。"
  : "真实适配器未配置。可完整体验两种模拟模式。";
$("config").textContent +=
  ` ${config.protocol} / B ${config.implementationVersion} / 实例 ${config.sourceInstanceId.slice(0, 8)}`;
$("save").onclick = guard(() => {
  saveFields();
  render();
  record("保存角色草稿");
  notice("角色草稿已保存，已确认版本未改变。");
});
$("new").onclick = guard(() => {
  saveFields();
  cancelPreview();
  addProject(
    {
      format: VERSION,
      revision: 1,
      character: {
        id: `character-${uid()}`,
        name: "新角色",
        persona: "",
        appearance: "",
      },
      assets: {},
      actions: [],
    },
    false,
  );
  render();
  record("新建角色");
  notice("已创建独立角色。编辑人设后导入固定动作或自己的 PNG 序列。");
});
$("restore").onclick = guard(() => {
  saveFields();
  resetSample();
});
$("projects").onchange = guard(() => {
  saveFields();
  cancelPreview();
  state.selected = $("projects").value;
  persist();
  render();
  record("切换角色");
  notice("已切换独立项目。未采用候选已放弃。");
});
$("actions").onchange = () => {
  candidate = null;
  actionIndex = Number($("actions").value);
  renderActions();
  record("切换动作预览");
};
$("play").onclick = () => {
  if (frameEditing) {
    notice("请先应用当前帧调整，或选择另一帧放弃当前输入。");
    return;
  }
  playing = !playing;
  paint();
};
$("replay").onclick = () => {
  frameEditing = false;
  frame = 0;
  elapsed = 0;
  playing = true;
  paint();
  record("从头回放");
};
$("scrub").oninput = () => {
  frameEditing = false;
  playing = false;
  frame = Number($("scrub").value);
  elapsed = 0;
  paint();
};
$("duration").onfocus = () => {
  playing = false;
  frameEditing = true;
  $("play").textContent = "播放";
};
$("contacts").onfocus = () => {
  playing = false;
  frameEditing = true;
  $("play").textContent = "播放";
};
$("sample").onclick = () => {
  task?.discard();
  candidate = {
    action: clone(sample.actions[0]),
    assets: clone(sample.assets),
    source: "fixed-fixture-v1",
  };
  renderActions();
  record("导入固定动作候选");
  notice("固定动作已进入候选。可逐帧调整，然后采用到草稿。");
};
$("adjust").onclick = guard(() => {
  const selectedFrame = frame;
  const a = clone(currentAction());
  if (!a) throw Error("请先导入动作");
  const oldId = a.id;
  a.id = $("action-id").value;
  a.name = $("action-name").value;
  a.meaning = $("meaning").value;
  a.loop = $("loop").value === "true";
  a.motion = $("motion").value;
  a.stride = Number($("stride").value);
  a.frames[frame].contact = JSON.parse($("contacts").value);
  a.allowedStates = $("allowed-states")
    .value.split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  a.priority = Number($("priority").value);
  a.contactKind = $("contact-kind").value || undefined;
  a.frames[frame].durationMs = Number($("duration").value);
  const pkg = clone(project().draft);
  pkg.character.name = $("name").value || "待命名";
  pkg.assets = { ...pkg.assets, ...currentAssets() };
  pkg.actions = candidate
    ? pkg.actions.filter((x) => x.id !== a.id)
    : pkg.actions.filter((x) => x.id !== oldId);
  pkg.actions.push(a);
  const errors = validate(pkg);
  if (errors.length) throw Error(errors.join("\n"));
  if (candidate) candidate.action = a;
  else {
    project().draft.actions[actionIndex] = a;
    project().editRevision = (project().editRevision ?? 0) + 1;
    persist();
  }
  renderActions();
  frame = selectedFrame;
  paint();
  record("调整动作及当前帧");
  notice("调整已应用。当前帧时长已更新；可继续逐帧检查。");
});
$("accept").onclick = guard(async () => {
  if (!candidate) throw Error("没有待采用候选");
  saveFields();
  if (
    candidate.projectKey !== undefined &&
    (candidate.projectKey !== state.selected ||
      candidate.editRevision !== (project().editRevision ?? 0))
  )
    throw new StudioError([
      {
        code: "stale",
        message: "候选所属项目版本已变化，请重新生成",
        retryable: false,
      },
    ]);
  const owner = project(),
    accepted = candidate;
  const response = await lifecycle.execute(
    {
      protocol: "wimipet/1",
      sessionId: lifecycle.sessionId,
      requestId: uid(),
      expiresAtMs: Date.now() + 60000,
      type: "candidate.accept",
      payload: {},
    },
    (_, signal) =>
      acceptCandidate(owner, accepted, {
        signature: accepted.draftSignature,
        signal,
      }),
  );
  if (!response.ok) throw new StudioError([response.error]);
  actionIndex = project().draft.actions.length - 1;
  candidate = null;
  persist();
  render();
  record("采用候选到草稿");
  notice("候选已加入草稿。请确认完整角色版本后导出。");
});
$("discard").onclick = () => {
  candidate = null;
  task?.discard();
  renderActions();
  record("放弃候选");
  notice("已放弃候选；草稿和已确认资产均未被覆盖。");
};
async function decodeAssets(assets) {
  for (const data of Object.values(assets)) {
    await decodePNG(data);
    const image = new Image();
    image.src = data;
    try {
      await image.decode();
    } catch {
      throw Error("PNG 无法解码，请检查图片是否损坏");
    }
  }
}
async function readPNG(file) {
  if (file.size > 6_000_000) throw Error("PNG 超过 6 MB");
  const data = await new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(Error("文件读取失败"));
    r.readAsDataURL(file);
  });
  await decodeAssets({ file: data });
  return data;
}
for (const [input, field] of [
  ["reference-file", "reference"],
  ["avatar-file", "avatar"],
])
  $(input).onchange = guard(async () => {
    const owner = project(),
      sessionId = lifecycle.sessionId;
    const file = $(input).files[0];
    if (!file) return;
    const data = await readPNG(file);
    if (owner !== project() || sessionId !== lifecycle.sessionId)
      throw new StudioError([
        {
          code: "stale",
          message: "图片所属项目已变化，未导入",
          retryable: false,
        },
      ]);
    const id = `image-${uid()}`;
    project().draft.assets[id] = data;
    project().draft.character[field] = id;
    project().editRevision = (project().editRevision ?? 0) + 1;
    persist();
    render();
    record(`导入${field === "avatar" ? "头像" : "参考图"}`);
    notice("图片已加入草稿。确认版本前可继续编辑。");
    $(input).value = "";
  });
$("frames-file").onchange = guard(async () => {
  const sessionId = lifecycle.sessionId;
  const files = [...$("frames-file").files].sort((a, b) =>
    a.name.localeCompare(b.name, "zh-CN", { numeric: true }),
  );
  if (!files.length) return;
  if (files.length > 128) throw Error("每个动作最多 128 帧");
  const assets = {},
    frames = [];
  for (const file of files) {
    const id = `frame-${uid()}`;
    assets[id] = await readPNG(file);
    frames.push({ asset: id, durationMs: 180 });
  }
  const action = {
    id: `action-${uid().slice(0, 8)}`,
    name: "导入动作",
    meaning: "请编辑动作表达含义",
    loop: true,
    motion: "in-place",
    stride: 0,
    contacts: [{ name: "feet", x: 0.5, y: 0.9 }],
    frames,
  };
  const errors = validate({
    format: VERSION,
    revision: 1,
    character: { id: "check", name: "检查", persona: "", appearance: "" },
    assets,
    actions: [action],
  });
  if (errors.length) throw Error(errors.join("\n"));
  if (sessionId !== lifecycle.sessionId)
    throw new StudioError([
      {
        code: "stale",
        message: "序列所属项目已变化，未导入",
        retryable: false,
      },
    ]);
  task?.discard();
  candidate = { action, assets, source: "用户导入 PNG" };
  renderActions();
  record("导入用户序列候选");
  notice(`已按文件名排序导入 ${frames.length} 帧。尚未采用。`);
  $("frames-file").value = "";
});
$("generate").onclick = guard(async () => {
  if (task?.state === "processing") throw Error("请等待当前任务或先取消");
  const description = $("prompt").value;
  if (!description.trim()) throw Error("请输入动作描述");
  const mode = $("provider").value;
  let provider;
  if (mode === "real") {
    if (!config.realConfigured) throw Error("真实适配器未配置，详见启动文档");
    provider = async (input, signal) => {
      const r = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
        signal,
      });
      const data = await r.json();
      if (!r.ok) throw new StudioError([data.error]);
      const converted = fromPackage({
        packageId: "generated",
        revision: 1,
        character: { name: "候选", persona: "", appearance: "" },
        assets: data.assets,
        actions: [data.action],
      });
      await decodeAssets(converted.assets);
      return {
        action: converted.actions[0],
        assets: converted.assets,
        source: data.source,
      };
    };
  } else
    provider = fakeProvider(sample, {
      outcome: mode === "failure" ? "failure" : "success",
    });
  candidate = null;
  const ownProject = state.selected;
  const draftAtStart = JSON.stringify(project().draft);
  const draftRevisionAtStart = project().editRevision ?? 0;
  const running = new GenerationTask(provider);
  task = running;
  const action = currentAction();
  const input = {
    id: action?.id || "generated",
    name: action?.name || "生成动作",
    description,
    reference:
      project().draft.assets[project().draft.character.reference] ?? null,
  };
  const pending = running.run(input);
  $("task").textContent = "任务：processing";
  $("generate").disabled = true;
  renderActions();
  record("开始生成候选");
  await pending;
  if (task !== running) return;
  $("generate").disabled = false;
  $("task").textContent = `任务：${running.state}`;
  if (
    state.selected !== ownProject ||
    JSON.stringify(project().draft) !== draftAtStart ||
    (project().editRevision ?? 0) !== draftRevisionAtStart
  ) {
    running.discard();
    $("task").textContent = "任务：stale";
    notice("生成结果已过期：角色或草稿已变化，未进入候选。");
    record("丢弃过期候选");
    return;
  }
  if (running.state === "success") {
    const probe = {
      format: VERSION,
      revision: 1,
      character: { id: "probe", name: "检查", persona: "", appearance: "" },
      actions: [running.candidate.action],
      assets: running.candidate.assets,
    };
    const errors = validate(probe);
    if (errors.length) throw Error(errors.join("\n"));
    candidate = running.candidate;
    candidate.draftSignature = draftAtStart;
    candidate.projectKey = ownProject;
    candidate.editRevision = draftRevisionAtStart;
    renderActions();
    notice(
      mode === "real"
        ? "真实适配器候选已返回，尚未通过人工视觉评审。"
        : "模拟候选已返回，仅复用固定素材，不代表真实生图质量。",
    );
  } else
    notice(
      running.error || "任务已取消；已确认版本未改变。",
      running.state === "failed",
    );
  record(`生成任务 ${running.state}`, running.error || running.state);
});
$("cancel").onclick = () => {
  task?.cancel();
  $("task").textContent = `任务：${task?.state ?? "idle"}`;
  record("取消任务");
};
$("validate").onclick = guard(async () => {
  saveFields();
  const errors = validate(project().draft);
  if (errors.length) throw Error(errors.join("\n"));
  await decodeAssets(project().draft.assets);
  const runtimeErrors = validateRuntime(toPackage(project().draft));
  if (runtimeErrors.length) throw new StudioError(runtimeErrors);
  notice(
    "草稿结构与 PNG 解码检查通过。视觉一致性、裁切、循环和动作切换请人工检查。",
  );
  record("检查草稿");
});
$("confirm").onclick = guard(async () => {
  saveFields();
  if (candidate) throw Error("存在待确认候选，请先采用或放弃");
  await decodeAssets(project().draft.assets);
  const owner = project();
  const response = await lifecycle.execute(
    {
      protocol: "wimipet/1",
      sessionId: lifecycle.sessionId,
      requestId: uid(),
      expiresAtMs: Date.now() + 60000,
      type: "character.confirm",
      payload: {},
    },
    (_, signal) => confirmDraft(owner, { signal }),
  );
  if (!response.ok) throw new StudioError([response.error]);
  const pkg = response.payload;
  persist();
  render();
  notice(`已确认 v${pkg.revision}。后续编辑不会隐式修改此快照。`);
  record("确认完整角色版本");
});
function download(name, data) {
  const url = URL.createObjectURL(
    new Blob(
      [typeof data === "string" ? data : JSON.stringify(data, null, 2)],
      { type: "application/json" },
    ),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("export").onclick = guard(async () => {
  if (!project().confirmed) throw Error("请先确认角色版本");
  const pkg = await exportPackage(project());
  download(
    `${pkg.packageId.replace(/[^a-zA-Z0-9_-]/g, "_")}.v${pkg.revision}.character.json`,
    serializePackage(pkg),
  );
  notice(`已导出确认快照 v${pkg.revision}；当前草稿不进入导出。`);
  record("导出确认快照");
});
$("import").onchange = guard(async () => {
  const file = $("import").files[0];
  if (!file) return;
  if (file.size > 32 * 1024 * 1024) throw Error("角色包超过 32MiB");
  const text = await file.text();
  const raw = JSON.parse(text);
  if (raw.format === "character-studio/0.1") {
    const draft = parsePackage(text);
    await decodeAssets(draft.assets);
    saveFields();
    cancelPreview();
    addProject(draft, false);
    render();
    notice(
      "旧格式仅作为未确认创作草稿导入。请逐帧填写像素接触点、允许状态、接触类型；运行校验通过后才能确认导出。",
    );
    record("显式导入旧草稿");
    return;
  }
  const pkg = await parseRuntime(text);
  saveFields();
  cancelPreview();
  addProject(fromPackage(pkg), pkg);
  render();
  notice("角色包已完整导入为独立项目，原项目保留。");
  record("重新导入角色包");
  $("import").value = "";
});
$("feedback").onclick = () => {
  download("character-studio-experience.json", {
    scenario: "B0002 统一角色包制作闭环",
    appVersion: "0.2.0",
    fixture: "common-v1@a24c406",
    protocol: "wimipet/1",
    capabilities: getCapabilities(),
    expected: "角色编辑→候选预览调整→采用→确认→导出→导入；失败与取消保留快照",
    actual: $("actual").value || "用户尚未填写人工评审结果",
    steps: records,
  });
  notice("已导出体验记录。请在分享前检查你填写的文字。");
};
window.addEventListener("pagehide", () => {
  lifecycle.close();
  task?.cancel();
  cancelAnimationFrame(animation);
});
window.addEventListener("resize", paint);
render();
notice("制作台已就绪。无密钥也可完成固定样例闭环；可先新建自己的角色。");
animation = requestAnimationFrame(tick);
