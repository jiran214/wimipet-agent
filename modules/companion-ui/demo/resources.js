import { ContractError, LIMITS, validateImage } from '../src/protocol.js';

// 本地资源桥替身：仅管理字节和生命周期，不理解对话，也不上传图片。
export function createDemoResourceBridge() {
  const entries = new Map(), jobs = new Map();
  let serial = 0, closed = false;
  const cleanup = (ref, entry) => {
    if (!entry.draftHeld && !entry.submitted && !entry.inFlight.size) { URL.revokeObjectURL(entry.url); entries.delete(ref); }
  };
  return {
    async prepare(file, context) {
      if (closed) throw new ContractError('cancelled');
      validateImage(file, LIMITS);
      const job = { cancelled: false }; jobs.set(context.requestId, job);
      try {
        const bytes = new Uint8Array(await file.arrayBuffer());
        const png = [137,80,78,71,13,10,26,10].every((value, index) => bytes[index] === value);
        const jpeg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
        if ((file.type === 'image/png' && !png) || (file.type === 'image/jpeg' && !jpeg)) throw new ContractError('invalid-input');
        if (typeof createImageBitmap === 'function') { const bitmap = await createImageBitmap(file); bitmap.close(); }
        if (job.cancelled || closed) throw new ContractError('cancelled');
        if (Date.now() >= context.expiresAtMs) throw new ContractError('stale');
        const contentRef = `demo-resource-${++serial}`;
        entries.set(contentRef, { bytes, url: URL.createObjectURL(file), sessionId: context.sessionId, submitted: false, draftHeld: true, inFlight: new Set() });
        return { attachmentId: `attachment-${serial}`, name: file.name, mime: file.type, sizeBytes: file.size, contentRef };
      } catch (error) { throw error instanceof ContractError ? error : new ContractError('invalid-input'); }
      finally { jobs.delete(context.requestId); }
    },
    cancelPreparation({ requestId }) { const job = jobs.get(requestId); if (job) job.cancelled = true; },
    registerAvatar(ref, asset, sessionId) {
      if (entries.has(ref)) entries.set(`${ref}#retired-${++serial}`, entries.get(ref));
      const bytes = Uint8Array.from(atob(asset.dataBase64), character => character.charCodeAt(0));
      entries.set(ref, { bytes, url: URL.createObjectURL(new Blob([bytes], { type: asset.mime })), sessionId, submitted: true, draftHeld: false, inFlight: new Set() });
    },
    preview(ref) { return entries.get(ref)?.url; },
    resolve(ref, sessionId) { const entry = entries.get(ref); if (!entry || entry.sessionId !== sessionId) throw new ContractError('not-found'); return entry.bytes.slice(); },
    retainInFlight(ref, sessionId, requestId) { const entry = entries.get(ref); if (!entry || entry.sessionId !== sessionId) throw new ContractError('not-found'); entry.inFlight.add(requestId); },
    finishRequest(requestId, accepted = false) { for (const [ref, entry] of entries) if (entry.inFlight.delete(requestId)) { entry.submitted ||= accepted; cleanup(ref, entry); } },
    retainSubmitted(ref, sessionId) { const entry = entries.get(ref); if (!entry || entry.sessionId !== sessionId) throw new ContractError('not-found'); entry.submitted = true; entry.draftHeld = false; },
    releaseDraft(ref) { const entry = entries.get(ref); if (entry) { entry.draftHeld = false; cleanup(ref, entry); } },
    releaseSession(sessionId) { for (const [ref, entry] of entries) if (entry.sessionId === sessionId) { URL.revokeObjectURL(entry.url); entries.delete(ref); } },
    stats: () => ({ resources: entries.size, preparing: jobs.size }),
    dispose() { closed = true; for (const job of jobs.values()) job.cancelled = true; for (const entry of entries.values()) URL.revokeObjectURL(entry.url); entries.clear(); }
  };
}
