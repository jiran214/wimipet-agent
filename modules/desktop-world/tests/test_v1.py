import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from desktop_world.api import DesktopWorldAPI
from desktop_world.model import Rect, Surface
from desktop_world.package_v1 import load_package, ProtocolError
from desktop_world.capture import png

MODULE = Path(__file__).resolve().parents[1]
COMMON = MODULE / "fixtures" / "v1" / "character-package.json"


class V1Tests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(COMMON.read_text(encoding="utf-8"))
        self.now = 1780000000000
        self.mono = 0
        self.serial = 0
        self.api = DesktopWorldAPI(epoch_ms=lambda: self.now, monotonic=lambda: self.mono)

    def tearDown(self):
        self.api.close()

    def envelope(self, kind, payload, request_id=None, ttl=10000):
        self.serial += 1
        return dict(protocol="wimipet/1", sessionId=self.api.session_id, requestId=request_id or f"r{self.serial}",
                    expiresAtMs=self.now + ttl, type=kind, payload=payload)

    def send(self, kind, payload, request_id=None, ttl=10000):
        return self.api.request(self.envelope(kind, payload, request_id, ttl))

    def load(self, instance="one"):
        result = self.send("LoadCharacter", dict(instanceId=instance, package=self.raw))
        self.assertNotIn("error", result)
        return result

    def command(self, action="wave", request="wave", enqueue=False):
        return self.send("ActionCommand", dict(instanceId="one", actionId=action, enqueue=enqueue), request)

    def statuses(self, events, request):
        return [e["payload"]["status"] for e in events if e["type"] == "ActionResult" and e["requestId"] == request]

    def policy(self, revision=1, paused=False, **extra):
        return self.send("ApplyObservationPolicy", dict(policyRevision=revision, paused=paused, scope="desktop", cooldownMs=0, excludedRegions=[], **extra))

    def test_shared_fixture_is_byte_identical_and_actually_decoded(self):
        shared = MODULE.parents[1] / "docs" / "contracts" / "fixtures" / "v1" / "character-package.json"
        self.assertEqual(hashlib.sha256(COMMON.read_bytes()).digest(), hashlib.sha256(shared.read_bytes()).digest())
        result = self.load()
        self.assertEqual(result["payload"]["packageId"], "contract-sample-moss")
        self.assertEqual(result["payload"]["revision"], 1)
        self.assertEqual([a["actionId"] for a in result["payload"]["supportedActions"]], ["idle", "wave"])
        self.assertEqual(result["payload"]["supportedPostures"], [])
        self.assertEqual(self.api.world.pets["one"].frame.width, 160)
        self.assertTrue(self.api.handshake()["capabilities"]["actualPngDecode"])

    def test_same_package_multiple_instances_and_atomic_revision_failure(self):
        self.load()
        self.load("two")
        self.api.world.pets["two"].x = 400
        self.assertNotEqual(self.api.position("one")["x"], self.api.position("two")["x"])
        bad = copy.deepcopy(self.raw)
        bad["revision"] = 2
        bad["assets"]["sample_0"]["width"] = 20
        result = self.send("LoadCharacter", dict(instanceId="one", package=bad))
        self.assertEqual(result["error"]["code"], "invalid-input")
        self.assertEqual(self.api.world.pets["one"].package.revision, 1)
        self.assertEqual(self.api.world.pets["two"].package.id, self.api.world.pets["one"].package.id)
        self.assertEqual(self.send("LoadCharacter", dict(instanceId="all", package=self.raw))["error"]["code"], "invalid-input")

    def test_missing_postures_reject_without_changing_body(self):
        self.load()
        self.api.world.set_surface(Surface("w", Rect(100, 200, 400, 100)))
        result = self.send("AttachCharacter", dict(instanceId="one", surfaceId="w", posture="hanging"))
        self.assertEqual(result["error"]["code"], "unsupported-capability")
        self.assertEqual(self.api.world.pets["one"].state, "standing")
        self.assertFalse(self.api.world.pets["one"].attachment)

    def test_action_full_lifecycle_and_no_early_callback(self):
        self.load()
        received = []
        cancel = self.api.subscribe(received.append)
        first = self.command(request="first")
        self.assertEqual(first["payload"]["status"], "accepted")
        self.assertEqual(self.command(request="second", enqueue=True)["payload"]["status"], "queued")
        self.assertFalse(received)
        events = self.api.tick(700)
        self.assertEqual(self.statuses(events, "first"), ["accepted", "completed"])
        self.assertEqual(self.statuses(events, "second"), ["queued", "accepted"])
        self.api.world.drag_start("one")
        self.assertEqual(self.statuses(self.api.poll(), "second"), ["interrupted"])
        self.api.world.dock("one")
        self.assertEqual(self.command("unknown", "bad")["payload"]["status"], "rejected")
        self.command(request="failure")
        self.api.world.fail_action("one", "failure", "固定渲染故障")
        self.assertEqual(self.statuses(self.api.poll(), "failure"), ["accepted", "failed"])
        before = len(received)
        cancel()
        cancel()
        self.api.world.dock("one")
        self.api.poll()
        self.assertEqual(len(received), before)

    def test_loop_cancel_and_queued_cancel_do_not_report_completion(self):
        self.load()
        self.command("idle", "loop")
        self.command(request="queued", enqueue=True)
        self.send("CancelRequest", dict(requestId="queued"))
        self.send("CancelRequest", dict(requestId="loop"))
        events = self.api.tick(5000)
        self.assertEqual(self.statuses(events, "queued"), ["queued", "interrupted"])
        self.assertEqual(self.statuses(events, "loop"), ["accepted", "interrupted"])

    def test_dedup_conflict_deadline_and_capacity(self):
        self.load()
        envelope = self.envelope("ActionCommand", dict(instanceId="one", actionId="wave", enqueue=False), "dup")
        one = self.api.request(envelope)
        seq = self.api.source_seq
        self.assertEqual(self.api.request(envelope), one)
        self.assertEqual(self.api.source_seq, seq)
        other = copy.deepcopy(envelope)
        other["payload"]["actionId"] = "idle"
        self.assertEqual(self.api.request(other)["error"]["code"], "conflict")
        self.api._max_requests = len(self.api._records)
        self.assertEqual(self.command(request="full")["error"]["code"], "unavailable")
        self.now += 10001
        self.mono += 10.001
        self.assertEqual(self.api.request(envelope)["error"]["code"], "stale")

    def test_timeout_uses_monotonic_not_later_epoch_jumps(self):
        self.load()
        self.send("ActionCommand", dict(instanceId="one", actionId="idle", enqueue=False), "timeout", ttl=100)
        self.now += 1000000
        self.api.poll()
        self.assertEqual(self.api.world.pets["one"].request_id, "timeout")
        self.mono += 0.101
        events = self.api.tick(10)
        result = next(e for e in events if e["type"] == "ActionResult" and e["requestId"] == "timeout")
        self.assertEqual(result["payload"]["status"], "failed")
        self.assertEqual(result["payload"]["reasonCode"], "timeout")

    def test_positions_are_image_origin_not_world_contact_and_epoch_envelopes(self):
        self.load()
        self.api._display = lambda x, y: dict(originX=-1920, originY=-100, displayId="left", scaleFactor=1.5)
        position = self.api.getPositions()["positions"][0]
        pet = self.api.world.pets["one"]
        self.assertEqual(position["x"] + position["anchor"]["x"], pet.x)
        self.assertEqual(position["y"] + position["anchor"]["y"], pet.y)
        self.assertEqual(position["originX"], -1920)
        self.assertEqual(position["scaleFactor"], 1.5)
        events = self.api.poll()
        self.assertTrue(any(e["type"] == "Position" and e["payload"]["scaleFactor"] == 1.5 for e in events))
        for event in events:
            self.assertEqual(event["atMs"], self.now)
            self.assertEqual(event["source"], "A")
            self.assertTrue(event["sourceInstanceId"])
            self.assertIn("eventId", event)
        sequences = [e["sourceSeq"] for e in events]
        self.assertEqual(sequences, sorted(set(sequences)))

    def test_invalid_package_limits_and_png_crc_are_rejected(self):
        cases = []
        for mutation in ("crc", "dimensions", "duration", "contact", "motion", "persona", "duplicate", "canvas", "schema"):
            raw = copy.deepcopy(self.raw)
            if mutation == "crc":
                data = bytearray(base64.b64decode(raw["assets"]["sample_0"]["dataBase64"]))
                data[-5] ^= 1
                raw["assets"]["sample_0"]["dataBase64"] = base64.b64encode(data).decode()
            elif mutation == "dimensions":
                raw["assets"]["sample_0"]["width"] = 161
            elif mutation == "duration":
                raw["actions"][0]["frames"][0]["durationMs"] = 16.5
            elif mutation == "contact":
                raw["actions"][0]["frames"][0]["contact"]["x"] = 161
            elif mutation == "motion":
                raw["actions"][0]["motion"] = "root-motion"
            elif mutation == "persona":
                raw["character"]["persona"] = "🙂" * 20001
            elif mutation == "duplicate":
                raw["actions"][1]["actionId"] = "idle"
            elif mutation == "canvas":
                raw["actions"][0]["frames"][0]["assetId"] = "wave_0"
            else:
                raw["protocol"] = "other/2"
            with self.subTest(mutation=mutation), self.assertRaises(ProtocolError):
                load_package(raw)

    def prepare_capture(self, backend, scope=None, native=False):
        self.api._backend = backend
        self.api._resolve_scope = scope or (lambda scope: Rect(0, 0, 100, 80))
        if native:
            self.api.source = "native"
        self.policy()

    def capture(self, request="capture"):
        return self.send("CaptureRequest", dict(policyRevision=self.api._policy["policyRevision"], sceneId="scene-one", scope="desktop"), request)

    def finish_workers(self):
        for job in list(self.api._workers):
            job["thread"].join(timeout=3)
            self.assertFalse(job["thread"].is_alive(), "工作线程未在状态屏障后结束")
        return self.api.poll()

    def test_capture_success_reference_and_release(self):
        data = png(100, 80, bytes((1, 2, 3)) * 8000)
        self.prepare_capture(lambda rect, cancel: data)
        self.assertEqual(self.capture()["type"], "CaptureReceipt")
        events = self.finish_workers()
        result = next(e for e in events if e["type"] == "CaptureResult")
        self.assertEqual(result["payload"]["capturedAtMs"], self.now)
        ref = result["payload"]["contentRef"]
        self.assertEqual(self.api.readResource(ref), data)
        self.api.releaseResource(ref)
        with self.assertRaises(ProtocolError):
            self.api.readResource(ref)
        old = self.envelope("CaptureRequest", dict(policyRevision=1, sceneId="old-policy", scope="desktop"))
        self.api.request(old)
        self.finish_workers()
        self.policy(2, paused=True)
        self.assertEqual(self.api.request(old)["error"]["code"], "stale")

    def test_slow_capture_does_not_block_body_and_policy_discards_inflight(self):
        self.load()
        entered, release = threading.Event(), threading.Event()
        def backend(rect, cancel):
            entered.set()
            self.assertTrue(release.wait(timeout=3))
            return b"late"
        self.prepare_capture(backend)
        self.capture()
        self.assertTrue(entered.wait(timeout=3))
        self.command(request="while-capture")
        events = self.api.tick(700)  # worker 仍在等待；身体可完成动作。
        self.assertEqual(self.statuses(events, "while-capture"), ["accepted", "completed"])
        self.assertFalse(release.is_set())
        self.policy(2, paused=True)
        self.policy(3, paused=False)
        release.set()
        events = self.finish_workers()
        self.assertFalse(any(e["type"] == "CaptureResult" for e in events))
        self.assertTrue(any(e["type"] == "CaptureError" and e["payload"]["error"]["code"] == "stale" for e in events))
        self.assertFalse(self.api._resources)

    def test_capture_cancel_timeout_failure_and_reset_late(self):
        def cooperative(rect, cancel):
            cancel.wait(timeout=3)
            return b"ignored"
        self.prepare_capture(cooperative)
        self.capture("cancel-me")
        self.send("CancelRequest", dict(requestId="cancel-me"))
        events = self.finish_workers()
        self.assertTrue(any(e["type"] == "CaptureError" and e["payload"]["error"]["code"] == "cancelled" for e in events))
        self.assertEqual(self.capture("cancel-me")["type"], "CaptureError")
        self.capture("time-me")
        self.mono += 11
        events = self.api.poll()
        self.assertTrue(any(e["type"] == "CaptureError" and e["payload"]["error"]["code"] == "timeout" for e in events))
        self.finish_workers()
        def broken(rect, cancel):
            raise OSError("私有错误不得外泄")
        self.api._backend = broken
        self.capture("broken")
        events = self.finish_workers()
        self.assertTrue(any(e["type"] == "CaptureError" for e in events))
        self.assertNotIn("私有错误", json.dumps(events, ensure_ascii=False))
        self.api._backend = cooperative
        self.capture("reset-late")
        old = self.envelope("ActionCommand", dict(instanceId="one", actionId="wave", enqueue=False))
        session = self.api.session_id
        self.api.reset()
        self.assertNotEqual(session, self.api.session_id)
        self.assertEqual(self.api.request(old)["error"]["code"], "stale")
        self.assertFalse(any(e["type"] == "CaptureResult" for e in self.finish_workers()))

    def test_pause_range_exclusion_scope_and_revision_before_backend(self):
        calls = []
        self.prepare_capture(lambda rect, cancel: calls.append(rect) or b"x")
        self.policy(2, paused=True)
        self.assertIn("error", self.capture("paused"))
        self.policy(3)
        self.api.authorize(Rect(0, 0, 20, 20))
        self.assertIn("error", self.capture("range"))
        self.api.authorize(Rect(0, 0, 100, 80))
        policy = dict(policyRevision=4, paused=False, scope="desktop", cooldownMs=0,
                      excludedRegions=[dict(x=1, y=1, width=1, height=1)])
        self.send("ApplyObservationPolicy", policy)
        self.assertIn("error", self.capture("excluded"))
        self.assertEqual(self.send("ApplyObservationPolicy", policy)["error"]["code"], "stale")
        wrong = self.send("CaptureRequest", dict(policyRevision=1, sceneId="scene", scope="desktop"))
        self.assertEqual(wrong["error"]["code"], "stale")
        self.assertFalse(calls)

    def test_host_exclusion_unknown_and_revoked_are_unavailable(self):
        ready = set()
        self.api._target_ready = lambda target: target in ready
        self.prepare_capture(lambda rect, cancel: png(100, 80, bytes((5, 6, 7)) * 8000), native=True)
        self.assertEqual(self.capture("no-target")["error"]["code"], "unavailable")
        result = self.send("RegisterHostTarget", dict(targetId="D-panel"))
        self.assertEqual(result["error"]["code"], "unavailable")
        self.assertIn("error", self.capture("not-ready"))
        ready.add("D-panel")
        self.send("RegisterHostTarget", dict(targetId="D-panel"))
        self.capture("revoked")
        for job in self.api._workers:
            job["thread"].join(timeout=3)
        ready.clear()
        events = self.api.poll()
        self.assertFalse(any(e["type"] == "CaptureResult" for e in events))
        self.assertTrue(any(e["type"] == "CaptureError" and e["payload"]["error"]["code"] == "unavailable" for e in events))
        def unavailable_target(target):
            raise OSError("平台内部错误")
        self.api._target_ready = unavailable_target
        self.assertEqual(self.send("RegisterHostTarget", dict(targetId="another-host"))["error"]["code"], "unavailable")

    def test_active_window_resolved_at_request_and_cooldown_enforced(self):
        scope_calls = []
        self.prepare_capture(lambda rect, cancel: png(rect.width, rect.height, b"\0" * (rect.width * rect.height * 3)),
                             scope=lambda scope: scope_calls.append(scope) or Rect(20, 30, 40, 50))
        policy = dict(policyRevision=2, paused=False, scope="active-window", cooldownMs=1000, excludedRegions=[])
        self.send("ApplyObservationPolicy", policy)
        payload = dict(policyRevision=2, sceneId="active-scene", scope="active-window")
        self.assertEqual(self.send("CaptureRequest", payload)["type"], "CaptureReceipt")
        events = self.finish_workers()
        actual = next(e["payload"] for e in events if e["type"] == "CaptureResult")
        self.assertEqual(actual["rect"], dict(x=20, y=30, width=40, height=50))
        self.assertEqual(scope_calls, ["active-window"])
        self.assertEqual(self.send("CaptureRequest", payload)["error"]["code"], "unavailable")
        self.mono += 1
        self.assertNotIn("error", self.send("CaptureRequest", payload))
        self.finish_workers()

    def test_bad_capture_bytes_never_become_resource(self):
        self.prepare_capture(lambda rect, cancel: b"corrupt-png")
        self.capture()
        events = self.finish_workers()
        self.assertFalse(any(e["type"] == "CaptureResult" for e in events))
        self.assertTrue(any(e["type"] == "CaptureError" for e in events))
        self.assertFalse(self.api._resources)

    def test_unknown_version_and_decoder_fault_are_structured_and_atomic(self):
        envelope = self.envelope("LoadCharacter", dict(instanceId="one", package=self.raw))
        envelope["protocol"] = "wimipet/2"
        self.assertEqual(self.api.request(envelope)["error"]["code"], "unsupported-version")
        self.load()
        def broken_decoder(data):
            raise RuntimeError("内部解码器私有信息")
        self.api._decoder = broken_decoder
        response = self.send("LoadCharacter", dict(instanceId="one", package=self.raw))
        self.assertEqual(response["error"]["code"], "invalid-input")
        self.assertNotIn("私有信息", json.dumps(response, ensure_ascii=False))
        self.assertIn("one", self.api.world.pets)
        unsupported = self.envelope("ActionCommand", dict(instanceId="one", actionId="wave", enqueue=False))
        unsupported["requiredCapabilities"] = ["icons"]
        self.assertEqual(self.api.request(unsupported)["error"]["code"], "unsupported-capability")


if __name__ == "__main__":
    unittest.main()
