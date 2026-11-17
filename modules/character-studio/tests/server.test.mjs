import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
test("独立服务提供界面、同源配置和固定包；拒绝未配置生图与私有文件", async () => {
  const port = 14312,
    base = `http://127.0.0.1:${port}`;
  const server = spawn(process.execPath, ["server.mjs"], {
    cwd: new URL("..", import.meta.url),
    env: {
      ...process.env,
      PORT: String(port),
      STUDIO_GENERATOR_URL: "",
      STUDIO_GENERATOR_KEY: "",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error("启动超时")), 5000);
      server.stdout.once("data", () => {
        clearTimeout(timer);
        resolve();
      });
      server.once("exit", () => {
        clearTimeout(timer);
        reject(Error("服务提前退出"));
      });
    });
    const html = await fetch(base).then((r) => r.text());
    assert.match(html, /角色制作台/);
    const config = await fetch(`${base}/api/config`).then((r) => r.json());
    assert.equal(config.realConfigured, false);
    assert.ok(!JSON.stringify(config).includes("KEY"));
    assert.equal((await fetch(`${base}/server.mjs`)).status, 404);
    assert.equal(
      (
        await fetch(`${base}/api/generate`, {
          method: "POST",
          headers: { Origin: "https://evil.example" },
          body: "{}",
        })
      ).status,
      403,
    );
    assert.equal(
      (
        await fetch(`${base}/api/generate`, {
          method: "POST",
          headers: { Origin: base },
          body: "{}",
        })
      ).status,
      503,
    );
    const pkg = await fetch(`${base}/fixtures/sample.character.json`).then(
      (r) => r.json(),
    );
    assert.equal(pkg.character.name, "苔苔");
  } finally {
    server.kill();
    await new Promise((resolve) => server.once("exit", resolve));
  }
});
