import http from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve, extname, sep } from "node:path";
import {
  assertCandidate,
  getCapabilities,
  failure,
  StudioError,
} from "./src/core.mjs";
const root = fileURLToPath(new URL(".", import.meta.url));
const endpoint = process.env.STUDIO_GENERATOR_URL;
const port = Number(process.env.PORT ?? 4312);
const sourceInstanceId = crypto.randomUUID();
const server = http.createServer(async (req, res) => {
  const send = (status, data) => {
    if (typeof data?.error === "string")
      data = {
        ...data,
        protocol: "wimipet/1",
        error: failure(
          status === 403
            ? "invalid-input"
            : status === 503 || status === 502
              ? "unavailable"
              : status === 404
                ? "not-found"
                : "invalid-input",
          data.error,
          status === 502,
        ),
      };
    res.writeHead(status, {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    });
    res.end(JSON.stringify(data));
  };
  try {
    if (req.url === "/api/config" && req.method === "GET")
      return send(200, {
        realConfigured: !!endpoint,
        mode: "适配器协议；真实模型效果未验证",
        ...getCapabilities(),
        sourceInstanceId,
      });
    if (req.url === "/favicon.ico") {
      res.writeHead(204);
      res.end();
      return;
    }
    if (req.url === "/api/generate" && req.method === "POST") {
      if (
        req.headers.origin !== `http://127.0.0.1:${server.address().port}` &&
        req.headers.origin !== `http://localhost:${server.address().port}`
      )
        return send(403, { error: "来源不允许" });
      if (!endpoint) return send(503, { error: "未配置真实生成适配器" });
      if (!endpoint.startsWith("https://"))
        return send(503, { error: "生成适配器必须使用 HTTPS" });
      const chunks = [];
      let received = 0;
      for await (const part of req) {
        received += part.length;
        if (received > 16_000_000) {
          send(413, { error: "输入过大" });
          return;
        }
        chunks.push(part);
      }
      const body = Buffer.concat(chunks).toString("utf8");
      const input = JSON.parse(body);
      if (typeof input.description !== "string" || !input.description.trim())
        return send(400, { error: "缺少动作描述" });
      const controller = new AbortController();
      res.on("close", () => controller.abort());
      const timer = setTimeout(
        () =>
          controller.abort(new DOMException("生成请求超时", "TimeoutError")),
        60000,
      );
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(process.env.STUDIO_GENERATOR_KEY
              ? { Authorization: `Bearer ${process.env.STUDIO_GENERATOR_KEY}` }
              : {}),
          },
          body: JSON.stringify(input),
          signal: controller.signal,
          redirect: "error",
        });
        if (!response.ok)
          return send(502, { error: `生成服务返回 HTTP ${response.status}` });
        let raw = "";
        const decoder = new TextDecoder();
        for await (const part of response.body) {
          raw += decoder.decode(part, { stream: true });
          if (raw.length > 32_000_000) throw Error("oversize");
        }
        raw += decoder.decode();
        const candidate = JSON.parse(raw);
        await assertCandidate(candidate, { signal: controller.signal });
        return send(200, {
          protocol: "wimipet/1",
          action: candidate.action,
          assets: candidate.assets,
          source: "real-adapter-unreviewed",
        });
      } catch (e) {
        if (!res.destroyed)
          send(502, {
            error:
              controller.signal.reason?.name === "TimeoutError"
                ? failure("timeout", "生成超时；已确认版本未改变", true)
                : e instanceof StudioError
                  ? e.toJSON()
                  : failure(
                      "unavailable",
                      "生成失败或返回资源不符合协议；已确认版本未改变",
                      true,
                    ),
          });
      } finally {
        clearTimeout(timer);
      }
      return;
    }
    if (req.method !== "GET") return send(405, { error: "方法不支持" });
    const path = resolve(
      root,
      "." +
        decodeURIComponent(
          new URL(req.url, "http://localhost").pathname === "/"
            ? "/demo/index.html"
            : new URL(req.url, "http://localhost").pathname,
        ),
    );
    if (
      !["demo", "src", "fixtures"].some((dir) =>
        path.startsWith(resolve(root, dir) + sep),
      )
    )
      return send(404, { error: "未找到" });
    const data = await readFile(path);
    res.writeHead(200, {
      "Content-Type":
        {
          ".html": "text/html; charset=utf-8",
          ".css": "text/css",
          ".mjs": "text/javascript",
          ".json": "application/json",
          ".png": "image/png",
        }[extname(path)] || "application/octet-stream",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy":
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; connect-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    });
    res.end(data);
  } catch {
    send(404, { error: "资源不存在或请求无效" });
  }
});
server.listen(port, "127.0.0.1", () =>
  console.log(
    `角色制作台 http://127.0.0.1:${server.address().port} · v0.2.0 · wimipet/1 · ${sourceInstanceId} · ${endpoint ? "真实适配器未评审" : "模拟模式"} · Ctrl+C 停止`,
  ),
);
for (const event of ["SIGINT", "SIGTERM"])
  process.on(event, () => {
    server.close();
    server.closeAllConnections();
  });
