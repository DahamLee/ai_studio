import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const ROOT = resolve(__dirname, "..");

/** 개발 서버에서 POST 요청 본문을 Python 진입점(ai_studio.n17_editor_api)에 넘긴다.
 * 연결 규칙과 실행의 최종 판단은 Python 한 곳에서만 한다. */
function pythonRoute(path: string, args: string[]): Plugin {
  return {
    name: `ai-studio${path.replaceAll("/", "-")}`,
    configureServer(server) {
      server.middlewares.use(path, (req, res) => {
        if (req.method !== "POST") {
          res.statusCode = 405;
          res.end();
          return;
        }
        const child = spawn(resolve(ROOT, ".venv/bin/python"), ["-m", "ai_studio.n17_editor_api", ...args], { cwd: ROOT });
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
            res.end(JSON.stringify({ ok: false, reason: err.trim().split("\n").pop() ?? "Python 실행 실패", complete: false, warnings: [] }));
          }
        });
        req.pipe(child.stdin);
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), pythonRoute("/api/validate", []), pythonRoute("/api/backtest", ["backtest"])],
  server: { port: 5173 },
});
