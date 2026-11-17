"""wimipet/1 只读角色包校验和实际 PNG 解码；没有文件路径/网络资源。"""
import base64
import hashlib
import json
import math
from pathlib import Path
import struct
import zlib

from .model import Action, Frame, Package, PackageError

LIMITS = dict(packageBytes=32 * 1048576, assetBytes=2 * 1048576, assets=256,
              frameWidth=512, frameHeight=512, actions=64, framesPerAction=128)
STATES = {"standing", "lying", "clinging", "hanging", "falling", "dragging"}
KINDS = {"feet", "body", "top-grip", "side-grip"}


class ProtocolError(ValueError):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable

    def json(self):
        return dict(code=self.code, message=self.message, retryable=self.retryable)


def integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def identity(value, reserved=False):
    return isinstance(value, str) and bool(value.strip()) and (not reserved or value != "all")


def decode_png(data, *, max_dimension=512, max_pixels=512 * 512):
    """校验 CRC、压缩流和每条滤波扫描线，重建像素；支持标准 PNG 含 Adam7。

    所有合法 PNG 色型/位深均校验；返回真实 IHDR 尺寸，不信任声明值。
    """
    try:
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("不是 PNG")
        cursor, header, compressed, ended, palette = 8, None, bytearray(), False, False
        idat_seen, idat_ended = False, False
        while cursor < len(data):
            if cursor + 12 > len(data):
                raise ValueError("PNG 块截断")
            size = struct.unpack_from(">I", data, cursor)[0]
            kind = data[cursor + 4:cursor + 8]
            end = cursor + 12 + size
            if end > len(data):
                raise ValueError("PNG 块越界")
            payload = data[cursor + 8:cursor + 8 + size]
            if zlib.crc32(kind + payload) & 0xffffffff != struct.unpack_from(">I", data, end - 4)[0]:
                raise ValueError("PNG CRC 错误")
            if header is None and kind != b"IHDR":
                raise ValueError("IHDR 必须在首块")
            if kind == b"IHDR":
                if header is not None or size != 13:
                    raise ValueError("IHDR 无效")
                header = struct.unpack(">IIBBBBB", payload)
            elif kind == b"PLTE":
                if idat_seen or not size or size % 3 or size > 768:
                    raise ValueError("调色板无效")
                palette = True
            elif kind == b"IDAT":
                if idat_ended:
                    raise ValueError("IDAT 不连续")
                idat_seen = True
                compressed.extend(payload)
            elif kind == b"IEND":
                if size or end != len(data):
                    raise ValueError("IEND 或尾部无效")
                ended = True
                break
            elif not kind[0] & 32:
                raise ValueError("未知 PNG 必需块")
            if idat_seen and kind != b"IDAT":
                idat_ended = True
            cursor = end
        if not ended or not header or not compressed:
            raise ValueError("PNG 不完整")
        w, h, depth, color, compression, filtering, interlace = header
        valid = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
        if not (1 <= w <= max_dimension and 1 <= h <= max_dimension and w * h <= max_pixels and color in valid and depth in valid[color]
                and compression == filtering == 0 and interlace in (0, 1) and (color != 3 or palette)):
            raise ValueError("PNG 尺寸或编码无效")
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
        bits = channels * depth
        passes = [(0, 0, 1, 1)] if not interlace else [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
        layouts = []
        for x, y, dx, dy in passes:
            pw, ph = max(0, (w - x + dx - 1) // dx), max(0, (h - y + dy - 1) // dy)
            if pw and ph:
                layouts.append(((pw * bits + 7) // 8, ph))
        expected = sum((stride + 1) * rows for stride, rows in layouts)
        dec = zlib.decompressobj()
        raw = dec.decompress(compressed, expected + 1)
        if len(raw) != expected or not dec.eof or dec.unused_data or dec.unconsumed_tail:
            raise ValueError("PNG 解压长度或流结尾无效")
        cursor, bpp = 0, max(1, (bits + 7) // 8)
        for stride, rows in layouts:
            previous = bytearray(stride)
            for _ in range(rows):
                mode = raw[cursor]
                row = bytearray(raw[cursor + 1:cursor + 1 + stride])
                cursor += stride + 1
                if mode > 4:
                    raise ValueError("PNG 滤波器无效")
                if mode == 0:
                    previous = row
                    continue
                for i in range(stride):
                    a, b, c = (row[i - bpp] if i >= bpp else 0), previous[i], (previous[i - bpp] if i >= bpp else 0)
                    if mode == 1:
                        row[i] = (row[i] + a) & 255
                    elif mode == 2:
                        row[i] = (row[i] + b) & 255
                    elif mode == 3:
                        row[i] = (row[i] + (a + b) // 2) & 255
                    elif mode == 4:
                        p = a + b - c
                        predictor = min((a, b, c), key=lambda v: abs(p - v))
                        row[i] = (row[i] + predictor) & 255
                previous = row
        return w, h
    except (ValueError, struct.error, zlib.error, IndexError) as exc:
        raise ProtocolError("invalid-input", f"PNG 实际解码失败：{exc}") from exc


def load_package(raw, decoder=decode_png):
    try:
        if not isinstance(raw, dict) or raw.get("protocol") != "wimipet/1":
            raise ProtocolError("unsupported-version", "只接受 wimipet/1 统一角色包；旧格式须显式迁移")
        if len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > LIMITS["packageBytes"]:
            raise ValueError("包超过 32MiB")
        if not identity(raw["packageId"]) or not integer(raw["revision"], 1, 2**53 - 1):
            raise ValueError("packageId/revision 无效")
        character = raw["character"]
        if not identity(character["name"]) or not isinstance(character["persona"], str) or len(character["persona"]) > 20000 or not isinstance(character["appearance"], str):
            raise ValueError("角色信息无效，人设不截断")
        if raw.get("requiredCapabilities"):
            raise ProtocolError("unsupported-capability", "角色包声明了未支持的必需能力")
        if not isinstance(raw["assets"], dict) or not 1 <= len(raw["assets"]) <= 256:
            raise ValueError("资源数量须为 1–256")
        assets = {}
        for asset_id, item in raw["assets"].items():
            if not identity(asset_id) or item["mime"] != "image/png":
                raise ValueError("只接受内嵌 PNG 资源")
            encoded = item["dataBase64"]
            if not isinstance(encoded, str) or len(encoded) > (2 * 1048576 + 2) // 3 * 4:
                raise ValueError("资源超过 2MiB")
            data = base64.b64decode(encoded, validate=True)
            if len(data) > 2 * 1048576:
                raise ValueError("资源超过 2MiB")
            try:
                width, height = decoder(data)
            except ProtocolError:
                raise
            except Exception as exc:
                raise ProtocolError("invalid-input", "宿主 PNG 解码失败") from exc
            if (not integer(item["width"], 1, 512) or not integer(item["height"], 1, 512)
                    or (width, height) != (item["width"], item["height"])):
                raise ValueError("声明尺寸与实际解码尺寸不符")
            assets[asset_id] = (data, width, height)
        if character.get("avatarAssetId") is not None and character["avatarAssetId"] not in assets:
            raise ValueError("头像资源不存在")
        if not isinstance(raw["actions"], list) or not 1 <= len(raw["actions"]) <= 64:
            raise ValueError("动作数量须为 1–64")
        actions = {}
        for item in raw["actions"]:
            action_id = item["actionId"]
            if not identity(action_id) or action_id in actions:
                raise ValueError("动作标识无效或重复")
            if item["motion"] != "in-place":
                raise ProtocolError("unsupported-capability", "不支持 root-motion，不能丢弃位移")
            states = item["allowedStates"]
            if (not isinstance(states, list) or not states or not all(isinstance(s, str) for s in states) or not set(states) <= STATES
                    or type(item["loop"]) is not bool or not integer(item["priority"], 0, 100) or item["contactKind"] not in KINDS
                    or not all(isinstance(item[k], str) for k in ("name", "meaning"))):
                raise ValueError("动作元数据无效")
            if not isinstance(item["frames"], list) or not 1 <= len(item["frames"]) <= 128:
                raise ValueError("单动作帧数须为 1–128")
            frames = []
            for f in item["frames"]:
                data, width, height = assets[f["assetId"]]
                x, y = f["contact"]["x"], f["contact"]["y"]
                if (not integer(f["durationMs"], 16, 10000) or not all(type(v) in (float, int) and math.isfinite(v) for v in (x, y))
                        or not 0 <= x <= width or not 0 <= y <= height):
                    raise ValueError("帧时长或像素接触点无效")
                if frames and (width, height) != (frames[0].width, frames[0].height):
                    raise ValueError("同动作画布尺寸必须一致")
                frames.append(Frame(Path("embedded") / (hashlib.sha256(data).hexdigest() + ".png"), f["durationMs"], (x, y), width, height, data))
            actions[action_id] = Action(tuple(frames), item["loop"], tuple(states), item["priority"], item["contactKind"], item["meaning"], item["name"])
        idle = actions.get("idle")
        if not idle or not idle.loop or idle.contact_kind != "feet" or not {"standing", "falling", "dragging"} <= set(idle.states):
            raise ValueError("需要覆盖 standing/falling/dragging 的 feet 循环 idle")
        return Package(raw["packageId"], str(raw["revision"]), character["name"], actions, raw["revision"], dict(character))
    except ProtocolError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ProtocolError("invalid-input", f"角色包校验失败：{type(exc).__name__}") from exc
