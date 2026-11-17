"""可重复生成项目自绘的固定像素样例；不是模型生成或外部角色资产。"""
import json
from pathlib import Path
import struct
import zlib

ROOT = Path(__file__).resolve().parent


def image(color, pose, frame):
    pixels = bytearray(64 * 64 * 4)

    def ellipse(cx, cy, rx, ry, fill):
        for y in range(64):
            for x in range(64):
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1:
                    pixels[(y * 64 + x) * 4:(y * 64 + x) * 4 + 4] = bytes((*fill, 255))

    dark = (40, 68, 67)
    if pose == "lying":
        ellipse(32, 34, 27, 14, color)
        ellipse(12, 24, 5, 10, color)
        ellipse(23, 24, 5, 10, color)
        ellipse(14, 34, 2, 2, dark)
        ellipse(25, 34, 2, 2, dark)
        ellipse(18, 47, 7, 2, dark)
        ellipse(45, 47, 7, 2, dark)
        contact = [32, 48]
    elif pose == "hanging":
        ellipse(32, 37, 20, 23, color)
        ellipse(18, 17, 5, 13, color)
        ellipse(46, 17, 5, 13, color)
        ellipse(18, 6, 7, 3, dark)
        ellipse(46, 6, 7, 3, dark)
        ellipse(24, 34, 2, 3, dark)
        ellipse(40, 34, 2, 3, dark)
        contact = [32, 6]
    elif pose == "clinging":
        ellipse(34, 34, 22, 23, color)
        ellipse(18, 15, 6, 11, color)
        ellipse(43, 15, 6, 11, color)
        ellipse(13, 24, 10, 4, dark)
        ellipse(13, 43, 10, 4, dark)
        ellipse(26, 30, 2, 3, dark)
        ellipse(40, 30, 2, 3, dark)
        contact = [6, 32]
    else:
        squash = 1 if frame % 2 else 0
        ellipse(32, 36 + squash, 22, 23 - squash, color)
        ellipse(18, 15 + squash, 6, 12, color)
        ellipse(45, 15 + squash, 6, 12, color)
        ellipse(22, 33 + squash, 2, 1 if frame == 3 else 3, dark)
        ellipse(41, 33 + squash, 2, 1 if frame == 3 else 3, dark)
        ellipse(31, 42, 3, 2, dark)
        ellipse(20, 58, 8, 2, dark)
        ellipse(44, 58, 8, 2, dark)
        if pose == "wave":
            ellipse(54, 26 - (frame % 3) * 6, 6, 9, color)
        contact = [32, 60]

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    rows = b"".join(b"\0" + pixels[y * 256:(y + 1) * 256] for y in range(64))
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 6, 0, 0, 0))
    return data + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""), contact


def main():
    for key, name, color in (("moss", "苔苔", (128, 183, 138)), ("sky", "云云", (133, 187, 220)), ("peach", "桃桃", (237, 170, 149))):
        folder = ROOT / key
        folder.mkdir(exist_ok=True)
        actions = {}
        for action in ("idle", "wave", "lying", "hanging", "clinging"):
            frames = []
            for index in range(5 if action == "wave" else 4):
                data, contact = image(color, action, index)
                filename = f"{action}-{index}.png"
                (folder / filename).write_bytes(data)
                frames.append(dict(file=filename, durationMs=140 if action == "wave" else 220, contact=contact))
            states = ["standing", "lying", "clinging", "hanging", "falling", "dragging"] if action == "idle" else (["standing"] if action == "wave" else [action])
            contact_kind = {"lying": "body", "hanging": "top-grip", "clinging": "side-grip"}.get(action, "feet")
            actions[action] = dict(loop=action != "wave", priority=10 if action == "wave" else 0, states=states, contactKind=contact_kind, frames=frames)
        (folder / "character.json").write_text(json.dumps(dict(schema="A-display/1", id=f"sample-{key}", version="1.0.0", name=name, actions=actions), ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
