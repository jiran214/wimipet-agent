import copy
import json
import shutil
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from desktop_world.model import World, Rect, Surface, Package, PackageError
from desktop_world.capture import CaptureGate

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "moss" / "character.json"


class BodyTests(unittest.TestCase):
    def setUp(self):
        self.package = Package.load(FIXTURE)
        self.world = World()
        self.world.add("a", self.package, 200, 620)
        self.world.set_surface(Surface("w", Rect(100, 300, 400, 200)))

    def tearDown(self):
        self.world.dispose()

    def results(self, request):
        return [e["status"] for e in self.world.events if e["type"] == "action-result" and e["requestId"] == request]

    def test_collection_not_limited_to_two_and_removal(self):
        for index in range(12):
            self.world.add(str(index), self.package, 80 + index * 30, 620)
        self.world.remove("4")
        self.assertEqual(len(self.world.pets), 12)
        self.assertNotIn("4", self.world.pets)

    def test_action_completion_is_not_acceptance(self):
        self.assertTrue(self.world.command("a", "wave", "r"))
        self.assertEqual(self.results("r"), ["accepted"])
        self.world.tick(699)
        self.assertEqual(self.results("r"), ["accepted"])
        self.world.tick(1)
        self.assertEqual(self.results("r"), ["accepted", "completed"])

    def test_unknown_action_and_illegal_hanging_action(self):
        self.world.command("a", "unknown", "bad")
        self.world.attach("a", "w", "hanging")
        self.world.command("a", "wave", "hands")
        self.assertEqual(self.results("bad"), ["rejected"])
        self.assertEqual(self.results("hands"), ["rejected"])
        self.assertEqual(self.world.pets["a"].state, "hanging")
        self.world.command("a", "idle", "wrong-contact")
        self.assertEqual(self.results("wrong-contact"), ["rejected"])

    def test_drag_interrupts_active_and_queued_and_falls(self):
        self.world.command("a", "wave", "r1")
        self.world.command("a", "wave", "r2", enqueue=True)
        self.world.drag_start("a")
        self.assertEqual(self.results("r1"), ["accepted", "interrupted"])
        self.assertEqual(self.results("r2"), ["queued", "interrupted"])
        self.world.drag_move("a", -100, 180)
        self.assertEqual(self.world.pets["a"].x, 32)
        self.world.drag_end("a")
        self.world.tick(2000)
        self.assertEqual(self.world.pets["a"].state, "standing")
        self.assertEqual(self.world.pets["a"].y, 620)

    def test_queue_starts_only_after_complete(self):
        self.world.command("a", "wave", "first")
        self.world.command("a", "wave", "second", enqueue=True)
        self.world.tick(700)
        self.assertEqual(self.results("first"), ["accepted", "completed"])
        self.assertEqual(self.results("second"), ["queued", "accepted"])
        self.world.tick(700)
        self.assertEqual(self.results("second")[-1], "completed")

    def test_priority_rejects_lower_and_equal_interrupts(self):
        self.world.command("a", "wave", "first")
        self.assertFalse(self.world.command("a", "idle", "low"))
        self.world.command("a", "wave", "equal")
        self.assertEqual(self.results("low"), ["rejected"])
        self.assertEqual(self.results("first")[-1], "interrupted")

    def test_host_failure_terminates_request_without_false_completion(self):
        self.world.command("a", "wave", "current")
        self.world.command("a", "wave", "queued", enqueue=True)
        self.assertFalse(self.world.fail_action("a", "stale", "旧请求错误"))
        self.assertTrue(self.world.fail_action("a", "current", "固定宿主故障"))
        self.world.tick(2000)
        self.assertEqual(self.results("current"), ["accepted", "failed"])
        self.assertEqual(self.results("queued"), ["queued", "interrupted"])

    def test_contact_stable_through_frames_move_resize(self):
        self.world.attach("a", "w", "hanging")
        pet = self.world.pets["a"]
        initial = pet.x, pet.y
        self.world.tick(500)
        self.assertEqual((pet.x, pet.y), initial)
        frame = pet.frame
        self.assertEqual((pet.origin[0] + frame.contact[0], pet.origin[1] + frame.contact[1]), initial)
        self.world.set_surface(Surface("w", Rect(250, 180, 80, 300)))
        self.assertEqual((pet.x, pet.y), (330, 180))
        self.world.set_surface(Surface("w", Rect(-500, 180, 80, 300)))
        self.assertEqual(pet.x, -420)
        self.assertFalse(pet.visible)

    def test_occlusion_minimize_and_close(self):
        self.world.attach("a", "w", "lying")
        surface = Surface("w", Rect(100, 300, 400, 200), occluded=True)
        self.world.set_surface(surface)
        self.assertFalse(self.world.pets["a"].visible)
        surface.occluded = False
        self.world.set_surface(surface)
        self.assertTrue(self.world.pets["a"].visible)
        surface.minimized = True
        self.world.set_surface(surface)
        self.assertIsNone(self.world.pets["a"].attachment)
        self.assertEqual(self.world.pets["a"].state, "falling")
        surface.minimized = False
        self.world.set_surface(surface)
        self.world.attach("a", "w", "clinging")
        self.world.remove_surface("w")
        self.assertIsNone(self.world.pets["a"].attachment)

    def test_subscriptions_are_isolated_and_disposed(self):
        events = []
        def consumer(event):
            event["type"] = "mutated"
            events.append(event)
        cancel = self.world.subscribe(consumer)
        self.world.dock("a")
        self.assertNotEqual(self.world.events[-1]["type"], "mutated")
        cancel()
        cancel()
        before = len(events)
        self.world.dock("a")
        self.assertEqual(len(events), before)
        self.world.dispose()
        self.world.dispose()
        self.assertFalse(self.world.pets or self.world.surfaces or self.world.listeners)
        with self.assertRaises(RuntimeError):
            self.world.tick(16)

    def test_snapshot_does_not_expose_mutable_attachment(self):
        self.world.attach("a", "w")
        snapshot = self.world.snapshot("a")
        snapshot["attachment"]["surfaceId"] = "other"
        self.assertEqual(self.world.pets["a"].attachment["surfaceId"], "w")

    def test_changed_work_area_keeps_docked_feet_on_taskbar(self):
        self.world.set_bounds(Rect(-200, 0, 800, 500))
        pet = self.world.pets["a"]
        self.assertEqual(pet.y, 500)
        self.world.attach("a", "w", "hanging")
        before = pet.x, pet.y
        self.world.set_bounds(Rect(0, 0, 1000, 700))
        self.assertEqual((pet.x, pet.y), before)


class PackageTests(unittest.TestCase):
    def test_missing_invalid_version_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "character.json"
            for asset in FIXTURE.parent.glob("*.png"):
                shutil.copyfile(asset, Path(directory) / asset.name)
            raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
            for change in ("missing", "schema", "escape", "contact"):
                data = copy.deepcopy(raw)
                if change == "schema":
                    data["schema"] = "unknown"
                elif change == "escape":
                    data["actions"]["idle"]["frames"][0]["file"] = "../escape.png"
                elif change == "contact":
                    data["actions"]["idle"]["frames"][0]["contact"] = [-1, 0]
                elif change == "missing":
                    data["actions"]["idle"]["frames"][0]["file"] = "missing.png"
                manifest.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(PackageError):
                    Package.load(manifest)

    def test_all_bundled_packages(self):
        for key in ("moss", "sky", "peach"):
            package = Package.load(FIXTURE.parent.parent / key / "character.json")
            self.assertEqual(package.version, "1.0.0")
            self.assertEqual(len(package.actions["wave"].frames), 5)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.gate = CaptureGate(Rect(0, 0, 500, 400))
        self.calls = []

    def backend(self, rect):
        self.calls.append(rect)
        return b"sample"

    def test_paused_and_outside_never_invoke_backend(self):
        for rect in (Rect(-1, 0, 10, 10), Rect(450, 350, 100, 100), Rect(0, 0, 0, 5), Rect(0.5, 0, 5, 5)):
            with self.assertRaises(PermissionError):
                self.gate.capture("r", rect, self.backend)
        self.gate.configure(paused=True)
        with self.assertRaises(PermissionError):
            self.gate.capture("paused", Rect(0, 0, 10, 10), self.backend)
        self.assertFalse(self.calls)

    def test_exclusion_refuses_entire_capture(self):
        self.gate.configure(excluded=[Rect(100, 100, 50, 50)])
        with self.assertRaises(PermissionError):
            self.gate.capture("x", Rect(80, 80, 30, 30), self.backend)
        result = self.gate.capture("y", Rect(0, 0, 80, 80), self.backend)
        self.assertFalse(result["stale"])
        self.assertEqual(result["image"], b"sample")
        self.assertEqual(len(self.calls), 1)

    def test_inflight_stale_after_pause_or_policy_change(self):
        ticket = self.gate.begin("r", Rect(0, 0, 20, 20))
        self.gate.configure(paused=True)
        self.gate.configure(paused=False)
        result = self.gate.finish(ticket, b"private")
        self.assertTrue(result["stale"])
        self.assertIsNone(result["image"])
        self.assertFalse(self.gate.pending)

    def test_failed_capture_and_dispose_release_requests(self):
        def broken(_):
            raise OSError("fixture failure")
        with self.assertRaises(OSError):
            self.gate.capture("r", Rect(0, 0, 10, 10), broken)
        self.assertFalse(self.gate.pending)
        self.gate.begin("pending", Rect(0, 0, 10, 10))
        self.gate.dispose()
        self.assertFalse(self.gate.pending)
        with self.assertRaises(PermissionError):
            self.gate.capture("next", Rect(0, 0, 10, 10), self.backend)


if __name__ == "__main__":
    unittest.main()
