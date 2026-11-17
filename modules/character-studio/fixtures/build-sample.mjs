// 原创固定素材 v1：用确定性像素绘制生成透明 PNG；无外部下载或模型。
import { deflateSync } from "node:zlib";
import { writeFile } from "node:fs/promises";
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
    len = Buffer.alloc(4),
    sum = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  sum.writeUInt32BE(crc(Buffer.concat([name, data])));
  return Buffer.concat([len, name, data, sum]);
}
const assets = {};
for (let frame = 0; frame < 4; frame++) {
  const w = 160,
    h = 160,
    pixels = Buffer.alloc((w * 4 + 1) * h),
    dy = [0, -3, 0, 2][frame];
  const ellipse = (x, y, cx, cy, rx, ry) =>
    (x - cx) ** 2 / rx ** 2 + (y - cy) ** 2 / ry ** 2 <= 1;
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const yy = y - dy;
      let color;
      if (
        ellipse(x, yy, 80, 91, 46, 46) ||
        ellipse(x, yy, 48, 44, 15, 28) ||
        ellipse(x, yy, 111, 43, 15, 29)
      )
        color = [60, 86, 73, 255];
      if (
        ellipse(x, yy, 80, 91, 42, 42) ||
        ellipse(x, yy, 48, 45, 11, 24) ||
        ellipse(x, yy, 111, 44, 11, 25)
      )
        color = [235, 204, 145, 255];
      if (ellipse(x, yy, 48, 40, 5, 15) || ellipse(x, yy, 111, 39, 5, 15))
        color = [215, 146, 126, 255];
      if (ellipse(x, yy, 80, 113, 23, 19)) color = [249, 230, 195, 255];
      if (
        ellipse(x, yy, 62, 86, 3, frame === 2 ? 1 : 5) ||
        ellipse(x, yy, 98, 86, 3, frame === 2 ? 1 : 5) ||
        ellipse(x, yy, 80, 98, 4, 3)
      )
        color = [60, 86, 73, 255];
      if (ellipse(x, yy, 49, 98, 7, 4) || ellipse(x, yy, 110, 98, 7, 4))
        color = [220, 153, 128, 255];
      if (ellipse(x, yy, 58, 132, 15, 6) || ellipse(x, yy, 101, 132, 15, 6))
        color = [60, 86, 73, 255];
      if (color) pixels.set(color, y * (w * 4 + 1) + 1 + x * 4);
    }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(w, 0);
  header.writeUInt32BE(h, 4);
  header[8] = 8;
  header[9] = 6;
  const png = Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(pixels)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
  assets[`sample_${frame}`] = `data:image/png;base64,${png.toString("base64")}`;
  await writeFile(new URL(`./idle-${frame + 1}.png`, import.meta.url), png);
}
const sample = {
  format: "character-studio/0.1",
  revision: 1,
  character: {
    id: "sample-momo",
    name: "苔苔",
    persona:
      "住在窗口边缘的小小观察家。语气温和，喜欢用植物比喻事物。是否知道自己是 AI，由创作者自由决定。",
    appearance: "苔绿轮廓、麦芽色身体、长耳朵；原创固定样例 v1。",
    avatar: "sample_0",
    reference: "sample_0",
  },
  actions: [
    {
      id: "idle",
      name: "轻轻呼吸",
      meaning: "安静陪伴、等待回应",
      loop: true,
      motion: "in-place",
      stride: 0,
      contacts: [{ name: "feet", x: 0.5, y: 0.86 }],
      frames: Object.keys(assets).map((asset) => ({ asset, durationMs: 220 })),
    },
  ],
  assets,
};
await writeFile(
  new URL("./sample.character.json", import.meta.url),
  JSON.stringify(sample, null, 2) + "\n",
);
