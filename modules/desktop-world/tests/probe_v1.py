"""真实 A 消费者探针；读统一 JSON 文件，不导入生产者实现，不改写样例。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from desktop_world.api import DesktopWorldAPI


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("package", nargs="?", type=Path, default=Path(__file__).resolve().parents[1] / "fixtures/v1/character-package.json")
    parser.add_argument("--output", type=Path, help="可选：将本次消费者证据写为 UTF-8 JSON")
    args = parser.parse_args()
    encoded = args.package.read_bytes()
    package = json.loads(encoded.decode("utf-8"))
    api = DesktopWorldAPI()
    def request(kind, payload):
        return api.request(dict(protocol="wimipet/1", sessionId=api.session_id, requestId=str(uuid.uuid4()),
                                expiresAtMs=time.time_ns() // 1000000 + 60000, type=kind, payload=payload))
    try:
        loads = [request("LoadCharacter", dict(instanceId=instance, package=package)) for instance in ("instance-one", "instance-two")]
        if any("error" in item for item in loads):
            print(json.dumps(dict(handshake=api.handshake(), results=loads), ensure_ascii=False, indent=2))
            raise SystemExit(1)
        action = request("ActionCommand", dict(instanceId="instance-two", actionId="wave", enqueue=False))
        events = api.tick(10000)
        completed = [e for e in events if e["type"] == "ActionResult" and e["requestId"] == action["requestId"]]
        api.world.drag_start("instance-two")
        api.world.drag_move("instance-two", 350, api.world.bounds.bottom)
        api.world.drag_end("instance-two")
        api.tick(1)
        report = dict(sha256=hashlib.sha256(encoded).hexdigest(), handshake=api.handshake(), loads=loads,
                      wave=completed, positions=api.getPositions(),
                      evidence="A 实际包消费者；不是 B 新导出或 A/B/C/D 全链路验收")
        encoded_report = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(encoded_report + "\n", encoding="utf-8", newline="\n")
        print(encoded_report)
    finally:
        api.close()


if __name__ == "__main__":
    main()
