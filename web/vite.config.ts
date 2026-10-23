import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const ROOT = resolve(__dirname, "..");

/** 개발 서버에서 POST /api/validate를 Python 검증기(ai_studio.n17_editor_api)로 넘긴다.
 * 연결 규칙의 최종 판단은 Python 한 곳에서만 한다. */
function validateApi(): Plugin {
  return {
    name: "ai-studio-validate",
    configureServer(server) {
      server.middlewares.use("/api/validate", (req, res) => {
        if (req.method !== "POST") {
          res.statusCode = 405;
          res.end();
          return;
        }
        const child = spawn(resolve(ROOT, ".venv/bin/python"), ["-m", "ai_studio.n17_editor_api"], { cwd: ROOT });
        let out = "";
        let err = "";
        child.stdout.on("data", (chunk) => { out += chunk; });
        child.stderr.on("data", (chunk) => { err += chunk; });
        child.on("close", (code) => {
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          if (code === 0) {
            res.end(out);
          } else {
            res.statusCode = 500;
            res.end(JSON.stringify({ ok: false, reason: err.trim().split("\n").pop() ?? "검증 실패", complete: false, warnings: [] }));
          }
        });
        req.pipe(child.stdin);
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), validateApi()],
  server: { port: 5173 },
});
