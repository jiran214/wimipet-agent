// 唯一规范来源：根 AGENTS.md。这里只实现 D 消费/发送的子集。
export const PROTOCOL = 'wimipet/1';
export const VERSION = '0.2.0';
export const LIMITS = Object.freeze({ textCodePoints: 4000, attachmentCount: 1, attachmentBytes: 2 * 1048576, attachmentMimes: Object.freeze(['image/png', 'image/jpeg']) });
export const CAPABILITIES = Object.freeze(['message-view', 'user-input', 'observation-policy', 'resource-bridge', 'cancel-request', 'studio-entry']);
export const declaration = Object.freeze({ protocol: PROTOCOL, module: 'D', implementationVersion: VERSION, capabilities: CAPABILITIES, limits: LIMITS });
const messages = {
  'invalid-input': '输入不符合协商限制', 'unsupported-version': '协议版本不支持', 'unsupported-capability': '宿主缺少必需能力',
  'not-found': '目标角色或资源不存在', conflict: '请求或版本冲突', stale: '请求或会话已过期', cancelled: '请求已取消',
  timeout: '请求已超时', unavailable: '宿主暂不可用', internal: '宿主处理失败'
};
export class ContractError extends Error {
  constructor(code, message = messages[code], retryable = ['timeout', 'unavailable', 'internal'].includes(code)) {
    super(message || messages.internal); this.code = code in messages ? code : 'internal'; this.retryable = retryable;
  }
  toJSON() { return { code: this.code, message: messages[this.code], retryable: this.retryable }; }
}
export const fail = (code, message) => { throw new ContractError(code, message); };
export const publicError = error => error instanceof ContractError ? error.toJSON() : new ContractError('internal').toJSON();
export const nonempty = value => typeof value === 'string' && value.trim().length > 0;
export function negotiate(peer, required = CAPABILITIES) {
  if (peer?.protocol !== PROTOCOL) fail('unsupported-version');
  if (!nonempty(peer.module) || !nonempty(peer.implementationVersion) || !Array.isArray(peer.capabilities)) fail('invalid-input');
  if (required.some(capability => !peer.capabilities.includes(capability))) fail('unsupported-capability');
  const limits = peer.limits;
  if (!limits || !['textCodePoints', 'attachmentCount', 'attachmentBytes'].every(key => Number.isSafeInteger(limits[key]) && limits[key] > 0) || !Array.isArray(limits.attachmentMimes)) fail('invalid-input');
  const agreed = Object.fromEntries(['textCodePoints', 'attachmentCount', 'attachmentBytes'].map(key => [key, Math.min(LIMITS[key], limits[key])]));
  agreed.attachmentMimes = LIMITS.attachmentMimes.filter(mime => limits.attachmentMimes.includes(mime));
  if (!agreed.attachmentMimes.length) fail('unsupported-capability');
  return Object.freeze(agreed);
}
export function validateImage(file, limits = LIMITS) {
  if (!limits.attachmentMimes.includes(file.type)) fail('invalid-input', '仅支持协商的 PNG/JPEG 图片');
  if (!Number.isSafeInteger(file.size) || file.size <= 0 || file.size > limits.attachmentBytes) fail('invalid-input', `图片须大于 0 且不超过 ${limits.attachmentBytes} 字节（基线 2MiB）`);
}
export function validateAttachment(attachment, limits = LIMITS) {
  if (!attachment || !nonempty(attachment.attachmentId) || !nonempty(attachment.name) || !nonempty(attachment.contentRef) || /^(?:blob:|https?:|file:|data:|[a-z]:[\\/]|[\\/])/i.test(attachment.contentRef)) fail('invalid-input');
  validateImage({ type: attachment.mime, size: attachment.sizeBytes }, limits);
}
export function validateInput(input, roles, limits = LIMITS) {
  if (!input || !['message', 'story-event'].includes(input.kind) || typeof input.text !== 'string' || [...input.text].length > limits.textCodePoints || !Array.isArray(input.attachments) || input.attachments.length > limits.attachmentCount || (!input.text.trim() && !input.attachments.length)) fail('invalid-input');
  if (input.recipient !== 'all' && !roles.some(role => role.instanceId === input.recipient && role.interactive)) fail('not-found');
  input.attachments.forEach(attachment => validateAttachment(attachment, limits));
}
export function validatePolicy(policy) {
  if (!policy || !Number.isSafeInteger(policy.policyRevision) || policy.policyRevision < 1 || typeof policy.paused !== 'boolean' || !['active-window', 'desktop'].includes(policy.scope) || !Number.isSafeInteger(policy.cooldownMs) || policy.cooldownMs < 0 || policy.cooldownMs > 300000 || !Array.isArray(policy.excludedRegions)) fail('invalid-input');
  for (const rect of policy.excludedRegions) if (!rect || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(rect[key])) || rect.width < 0 || rect.height < 0) fail('invalid-input');
}
export function validateEnvelope(request, sessionId, now = Date.now()) {
  if (request?.protocol !== PROTOCOL) fail('unsupported-version');
  if (!nonempty(request.requestId) || !nonempty(request.sessionId) || !nonempty(request.type) || !request.payload || !Number.isSafeInteger(request.expiresAtMs)) fail('invalid-input');
  if (request.sessionId !== sessionId || request.expiresAtMs <= now) fail('stale');
}
export function summarizeCharacterPackage(pkg, instanceId) {
  if (pkg?.protocol !== PROTOCOL) fail('unsupported-version');
  if (!nonempty(instanceId) || instanceId === 'all' || !nonempty(pkg.packageId) || !Number.isSafeInteger(pkg.revision) || pkg.revision < 1 || !nonempty(pkg.character?.name)) fail('invalid-input');
  const avatarAssetId = pkg.character.avatarAssetId;
  if (avatarAssetId && !pkg.assets?.[avatarAssetId]) fail('not-found');
  return { instanceId, packageId: pkg.packageId, revision: pkg.revision, name: pkg.character.name, ...(avatarAssetId ? { avatarRef: `${pkg.packageId}@${pkg.revision}/${avatarAssetId}` } : {}), interactive: true };
}

// 接收方工具：有效期内不提前逐出去重记录；重复内容返回同一个结果。
export function createRequestLedger({ capacity = 128, epochNow = Date.now, monoNow = () => performance.now() } = {}) {
  const entries = new Map();
  const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
  return {
    execute(request, sessionId, handler) {
      const receivedEpoch = epochNow();
      try { validateEnvelope(request, sessionId, receivedEpoch); } catch (error) { return Promise.reject(error); }
      const now = monoNow();
      for (const [key, entry] of entries) if (entry.deadline <= now) entries.delete(key);
      const key = JSON.stringify([request.sessionId, request.requestId]);
      const signature = JSON.stringify(canonical({ type: request.type, payload: request.payload }));
      const old = entries.get(key);
      if (old) return old.signature === signature ? old.promise : Promise.reject(new ContractError('conflict'));
      if (entries.size >= capacity) return Promise.reject(new ContractError('unavailable'));
      const promise = Promise.resolve().then(handler);
      entries.set(key, { signature, promise, deadline: now + request.expiresAtMs - receivedEpoch });
      return promise;
    },
    clear() { entries.clear(); }
  };
}
