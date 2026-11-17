import { pngSize } from "./draft-core.mjs";

// PNG 解码：CRC 由 pngSize 校验；这里实际解压 IDAT、重建扫描线，支持标准
// 位深/颜色类型与 Adam7。浏览器预览另使用原生图片解码器进行显示。
export async function decodePNG(data, { signal } = {}) {
  const [width, height] = pngSize(data);
  const bytes = Uint8Array.from(atob(data.split(",")[1]), (c) =>
    c.charCodeAt(0),
  );
  const view = new DataView(bytes.buffer),
    depth = bytes[24],
    color = bytes[25],
    interlace = bytes[28];
  const channels = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[color];
  const depths = {
    0: [1, 2, 4, 8, 16],
    2: [8, 16],
    3: [1, 2, 4, 8],
    4: [8, 16],
    6: [8, 16],
  }[color];
  if (
    !channels ||
    !depths.includes(depth) ||
    bytes[26] !== 0 ||
    bytes[27] !== 0 ||
    interlace > 1
  )
    throw Error("PNG 颜色、位深、压缩或隔行模式无效");
  const idat = [];
  let palette = 0,
    sawData = false,
    endedData = false;
  for (let offset = 8; offset < bytes.length;) {
    const length = view.getUint32(offset),
      type = String.fromCharCode(...bytes.slice(offset + 4, offset + 8));
    if (type === "IHDR" && offset !== 8) throw Error("PNG 重复 IHDR");
    if (type === "PLTE") {
      if (sawData || palette || length === 0 || length % 3 || length > 768)
        throw Error("PNG 调色板无效");
      palette = length / 3;
    }
    if (type === "IDAT") {
      if (endedData) throw Error("PNG IDAT 必须连续");
      idat.push(bytes.slice(offset + 8, offset + 8 + length));
      sawData = true;
    } else if (sawData) endedData = true;
    if (
      !["IHDR", "IDAT", "PLTE", "IEND"].includes(type) &&
      type[0] === type[0].toUpperCase()
    )
      throw Error("PNG 含未知关键块");
    offset += length + 12;
  }
  if (color === 3 && (!palette || palette > 2 ** depth))
    throw Error("PNG 索引色缺少有效调色板");
  const passes = interlace
    ? [
        [0, 0, 8, 8],
        [4, 0, 8, 8],
        [0, 4, 4, 8],
        [2, 0, 4, 4],
        [0, 2, 2, 4],
        [1, 0, 2, 2],
        [0, 1, 1, 2],
      ]
    : [[0, 0, 1, 1]];
  const layouts = passes
    .map(([x, y, dx, dy]) => ({
      w: Math.max(0, Math.ceil((width - x) / dx)),
      h: Math.max(0, Math.ceil((height - y) / dy)),
    }))
    .filter((p) => p.w && p.h);
  const expected = layouts.reduce(
    (sum, p) => sum + p.h * (1 + Math.ceil((p.w * channels * depth) / 8)),
    0,
  );
  const compressed = new Uint8Array(idat.reduce((sum, p) => sum + p.length, 0));
  let at = 0;
  for (const part of idat) {
    compressed.set(part, at);
    at += part.length;
  }
  signal?.throwIfAborted();
  const stream = new Blob([compressed])
    .stream()
    .pipeThrough(new DecompressionStream("deflate"));
  const reader = stream.getReader(),
    raw = new Uint8Array(expected);
  let used = 0;
  const abort = () => {
    reader.cancel().catch(() => {});
  };
  signal?.addEventListener("abort", abort, { once: true });
  try {
    while (true) {
      signal?.throwIfAborted();
      const { value, done } = await reader.read();
      if (done) break;
      if (used + value.length > expected) throw Error("PNG 解码数据超出画布");
      raw.set(value, used);
      used += value.length;
    }
  } finally {
    await reader.cancel().catch(() => {});
    signal?.removeEventListener("abort", abort);
    reader.releaseLock();
  }
  signal?.throwIfAborted();
  if (used !== expected) throw Error("PNG 解码数据被截断");
  let cursor = 0;
  const reconstructed = [];
  const bpp = Math.max(1, Math.ceil((channels * depth) / 8));
  for (const { w, h } of layouts) {
    const rowBytes = Math.ceil((w * channels * depth) / 8);
    let previous = new Uint8Array(rowBytes);
    for (let row = 0; row < h; row++) {
      const filter = raw[cursor++];
      if (filter > 4) throw Error("PNG 扫描线滤波类型无效");
      const output = new Uint8Array(rowBytes);
      for (let x = 0; x < rowBytes; x++) {
        const left = x >= bpp ? output[x - bpp] : 0,
          up = previous[x],
          upperLeft = x >= bpp ? previous[x - bpp] : 0;
        const prediction = left + up - upperLeft,
          pa = Math.abs(prediction - left),
          pb = Math.abs(prediction - up),
          pc = Math.abs(prediction - upperLeft);
        const paeth = pa <= pb && pa <= pc ? left : pb <= pc ? up : upperLeft;
        output[x] =
          (raw[cursor++] +
            [0, left, up, Math.floor((left + up) / 2), paeth][filter]) &
          255;
      }
      if (color === 3)
        for (let x = 0; x < w; x++) {
          const bit = x * depth,
            index =
              (output[Math.floor(bit / 8)] >>> (8 - depth - (bit % 8))) &
              ((1 << depth) - 1);
          if (index >= palette) throw Error("PNG 像素索引越过调色板");
        }
      reconstructed.push(output);
      previous = output;
    }
  }
  return { width, height, depth, color, scanlines: reconstructed };
}
