const { app, BrowserWindow, dialog, shell } = require("electron");
const path = require("path");
const { spawn, spawnSync } = require("child_process");
const http = require("http");
const net = require("net");
const fs = require("fs");
const crypto = require("crypto");

// ── Configuration ──────────────────────────────────────────────────────────
const GRADIO_HOST = "127.0.0.1";

let mainWindow = null;
let pythonProcess = null;
let isQuitting = false;

// ── Utilities ──────────────────────────────────────────────────────────────

/** Find a free port */
function findFreePort(startPort) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", () => {
      // try next port
      findFreePort(startPort + 1).then(resolve).catch(reject);
    });
    server.listen(startPort, GRADIO_HOST, () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

/** Poll the Gradio server until it responds */
function waitForServer(url, timeoutMs = 90000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    function poll() {
      if (Date.now() - start > timeoutMs) {
        return reject(new Error("Server did not start within timeout"));
      }
      http
        .get(url, (res) => {
          if (res.statusCode >= 200 && res.statusCode < 500) {
            resolve();
          } else {
            setTimeout(poll, 800);
          }
        })
        .on("error", () => setTimeout(poll, 800));
    }
    poll();
  });
}

/** Resolve the resources directory whether packaged or in dev */
function getResourcesPath() {
  return app.isPackaged ? process.resourcesPath : __dirname;
}

/** Sync mutable Python app files out of the signed app bundle. */
function syncRuntimePath() {
  if (!app.isPackaged) return __dirname;

  const resPath = getResourcesPath();
  const runtimePath = path.join(app.getPath("userData"), "runtime");
  fs.mkdirSync(runtimePath, { recursive: true });

  for (const file of ["app.py", "desktop_app.py", "requirements.txt", "config.example.yaml"]) {
    const source = path.join(resPath, file);
    if (fs.existsSync(source)) {
      fs.copyFileSync(source, path.join(runtimePath, file));
    }
  }

  // Only seed config.yaml on first run — never overwrite user-saved settings.
  const destConfig = path.join(runtimePath, "config.yaml");
  if (!fs.existsSync(destConfig)) {
    const srcConfig = path.join(resPath, "config.yaml");
    const srcExample = path.join(resPath, "config.example.yaml");
    const seed = fs.existsSync(srcConfig) ? srcConfig : fs.existsSync(srcExample) ? srcExample : null;
    if (seed) fs.copyFileSync(seed, destConfig);
  }

  const sourcePipeline = path.join(resPath, "pipeline");
  const targetPipeline = path.join(runtimePath, "pipeline");
  if (fs.existsSync(sourcePipeline)) {
    fs.rmSync(targetPipeline, { recursive: true, force: true });
    fs.cpSync(sourcePipeline, targetPipeline, { recursive: true });
  }

  fs.mkdirSync(path.join(runtimePath, "outputs"), { recursive: true });
  return runtimePath;
}

/** Find the best Python command to use. */
function findPythonCommand() {
  const devVenv = path.join(__dirname, ".venv-build", "bin", "python3");
  if (!app.isPackaged && fs.existsSync(devVenv)) {
    return { command: devVenv, args: [], label: devVenv };
  }

  if (fs.existsSync("/usr/bin/python3")) {
    return {
      command: "/usr/bin/arch",
      args: ["-arm64", "/usr/bin/python3"],
      label: "/usr/bin/arch -arm64 /usr/bin/python3",
    };
  }

  // System fallback
  for (const bin of ["python3", "python"]) {
    try {
      const r = spawnSync(bin, ["--version"], { timeout: 3000 });
      if (r.status === 0) return { command: bin, args: [], label: bin };
    } catch (_) {}
  }
  return { command: "python3", args: [], label: "python3" };
}

// ── Dep Management (venv + pip install) ──────────────────────────────────────

function getVenvDir() {
  return path.join(app.getPath("userData"), "venv");
}

/** Build a simple ASCII progress bar, e.g. "[████░░░░░░]" */
function buildBar(current, total, width) {
  const filled = Math.round((current / Math.max(total, 1)) * width);
  return "[" + "█".repeat(filled) + "░".repeat(width - filled) + "]";
}

function updateLoadingStatus(win, msg, detail) {
  if (!win || win.isDestroyed()) return;
  try {
    const js = `(function(){
      var s=document.getElementById('load-status'); if(s && ${JSON.stringify(msg)} !== undefined) s.innerText=${JSON.stringify(msg ?? "")};
      var d=document.getElementById('load-detail'); if(d && ${JSON.stringify(detail)} !== undefined) d.innerText=${JSON.stringify(detail ?? "")};
    })()`;
    win.webContents.executeJavaScript(js);
  } catch (_) {}
}

/** Spawn a command, stream output to console, resolve/reject on exit. */
function runCmd(command, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const proc = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"], ...opts });
    proc.stdout.on("data", (d) => console.log(`[setup] ${d.toString().trim()}`));
    let stderr = "";
    proc.stderr.on("data", (d) => {
      const line = d.toString().trim();
      if (line) console.log(`[setup] ${line}`);
      stderr += d.toString();
    });
    proc.on("error", reject);
    proc.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(stderr.slice(-2000) || `exit code ${code}`));
    });
  });
}

/**
 * Like runCmd but calls onLine(line) for every non-empty stdout line.
 * stderr is still logged to console only.
 */
function runCmdWithProgress(command, args, onLine, opts = {}) {
  return new Promise((resolve, reject) => {
    const proc = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"], ...opts });
    let buf = "";
    proc.stdout.on("data", (d) => {
      buf += d.toString();
      const lines = buf.split("\n");
      buf = lines.pop();
      for (const line of lines) { if (line.trim()) onLine(line.trim()); }
    });
    let stderr = "";
    proc.stderr.on("data", (d) => {
      const line = d.toString().trim();
      if (line) console.log(`[setup] ${line}`);
      stderr += d.toString();
    });
    proc.on("error", reject);
    proc.on("exit", (code) => {
      if (buf.trim()) onLine(buf.trim());
      if (code === 0) resolve();
      else reject(new Error(stderr.slice(-2000) || `exit code ${code}`));
    });
  });
}

/**
 * Create a venv in userData and pip-install requirements.txt when needed.
 * A SHA-256 hash of requirements.txt is used as a marker so re-install only
 * happens when dependencies change.
 * Returns the venv python3 path, or null if requirements.txt is absent.
 */
async function ensureDeps(basePythonCmd, resPath, win) {
  const reqFile = path.join(resPath, "requirements.txt");
  if (!fs.existsSync(reqFile)) return null;

  const venvDir    = getVenvDir();
  const venvPython = path.join(venvDir, "bin", "python3");
  const venvPip    = path.join(venvDir, "bin", "pip");

  // Create venv if it doesn't exist yet
  if (!fs.existsSync(venvPython)) {
    console.log("Creating Python venv at", venvDir);
    updateLoadingStatus(win, "正在创建 Python 虚拟环境…");
    await runCmd(basePythonCmd.command, [...basePythonCmd.args, "-m", "venv", venvDir]);
    console.log("Venv created.");
  }

  // Only reinstall when requirements.txt content changes
  const reqContent = fs.readFileSync(reqFile);
  const hash = crypto.createHash("sha256").update(reqContent).digest("hex").slice(0, 16);
  const markerFile = path.join(app.getPath("userData"), `deps-${hash}.ok`);

  if (!fs.existsSync(markerFile)) {
    console.log(`Installing Python deps (hash: ${hash})…`);

    // Count non-comment, non-blank lines in requirements.txt as total package estimate
    const reqLines = reqContent.toString().split("\n")
      .filter(l => l.trim() && !l.trim().startsWith("#"));
    const totalPkgs = reqLines.length;
    let collectedCount = 0;

    updateLoadingStatus(win, `正在安装 Python 依赖（0 / ${totalPkgs}）`, "首次运行约需 2–5 分钟，请耐心等待…");

    await runCmdWithProgress(
      venvPip,
      ["install", "--progress-bar", "off", "-r", reqFile],
      (line) => {
        console.log(`[setup] ${line}`);
        if (line.startsWith("Collecting ")) {
          collectedCount++;
          const pkgName = line.replace("Collecting ", "").split(" ")[0];
          const bar = buildBar(collectedCount, totalPkgs, 16);
          updateLoadingStatus(
            win,
            `正在安装依赖  ${bar}  ${collectedCount} / ${totalPkgs}`,
            pkgName
          );
        } else if (line.startsWith("Downloading ")) {
          const pkgName = line.replace("Downloading ", "").split("-")[0];
          updateLoadingStatus(win, undefined, `下载 ${pkgName}…`);
        } else if (line.startsWith("Installing collected")) {
          updateLoadingStatus(win, "正在写入安装文件…", "");
        } else if (line.startsWith("Successfully installed")) {
          updateLoadingStatus(win, "✅ 依赖安装完成！", "");
        }
      }
    );

    fs.writeFileSync(markerFile, new Date().toISOString());
    console.log("Dependencies installed.");
  } else {
    console.log("Dependencies already up-to-date.");
    updateLoadingStatus(win, "✅ 依赖已安装，跳过安装步骤", "");
  }

  return venvPython;
}

// ── Python Backend ──────────────────────────────────────────────────────────

async function startPythonBackend(win) {
  const resPath = syncRuntimePath();
  const basePythonCmd = findPythonCommand();
  console.log(`Base Python: ${basePythonCmd.label}`);

  // Ensure venv + pip deps are ready (first run / requirements change only)
  let pythonCmd;
  try {
    const venvPython = await ensureDeps(basePythonCmd, resPath, win);
    pythonCmd = venvPython
      ? { command: venvPython, args: [], label: venvPython }
      : basePythonCmd;
  } catch (err) {
    console.error("Dep install failed:", err.message);
    pythonCmd = basePythonCmd;
    updateLoadingStatus(win, `⚠️ 依赖安装失败，尝试直接启动…`);
  }

  console.log(`Using Python: ${pythonCmd.label}`);
  updateLoadingStatus(win, "正在启动后端服务，请稍候…");

  const port = await findFreePort(7860);
  console.log(`Starting Gradio on port ${port}`);

  const env = Object.assign({}, process.env, {
    GRADIO_ANALYTICS_ENABLED: "False",
    HF_HUB_DISABLE_TELEMETRY: "1",
    GRADIO_SERVER_NAME: GRADIO_HOST,
    GRADIO_SERVER_PORT: String(port),    // Pass resPath via env variable — avoids string interpolation into Python
    // source code which could allow path characters to break the script.
    APP_RUNTIME_PATH: resPath,
    WHISPER_CACHE_DIR: path.join(app.getPath("userData"), "whisper-models"),
    // Prepend bundled ffmpeg dir first, then common Homebrew / system paths.
    // macOS GUI apps launched from Finder/DMG have a stripped PATH that does
    // not include /opt/homebrew/bin, so we add it explicitly here.
    PATH: [
      path.join(resPath, "ffmpeg"), // bundled ffmpeg (optional, future use)
      "/opt/homebrew/bin",          // Homebrew on Apple Silicon
      "/usr/local/bin",             // Homebrew on Intel macOS / manually installed
      "/usr/bin",
      "/bin",
      process.env.PATH || "",
    ].filter(Boolean).join(":"),
    DYLD_LIBRARY_PATH: path.join(resPath, "lib") + (process.env.DYLD_LIBRARY_PATH ? ":" + process.env.DYLD_LIBRARY_PATH : ""),
  });
  delete env.ALL_PROXY;
  delete env.all_proxy;

  // Inline launcher: use APP_RUNTIME_PATH env var (set above) so the path is
  // never interpolated into Python source code, preventing any injection risk.
  const launchScript = `
import os, sys
sys.path.insert(0, os.environ["APP_RUNTIME_PATH"])
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
for k in ("ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)

from app import build_ui
from pipeline.config import ensure_user_config, load_config

ensure_user_config()
cfg = load_config()
demo = build_ui()
demo.queue().launch(
    server_name="${GRADIO_HOST}",
    server_port=${port},
    share=False,
    inbrowser=False,
    prevent_thread_lock=False,
    show_error=True,
    show_api=False,
)
`;

  pythonProcess = spawn(pythonCmd.command, [...pythonCmd.args, "-c", launchScript], {
    cwd: resPath,
    env: env,
    stdio: ["ignore", "pipe", "pipe"],
  });

  pythonProcess.stdout.on("data", (d) => console.log(`[Python] ${d.toString().trim()}`));
  pythonProcess.stderr.on("data", (d) => console.log(`[Python] ${d.toString().trim()}`));

  pythonProcess.on("error", (err) => {
    console.error("Failed to start Python:", err.message);
    dialog.showErrorBox(
      "启动失败",
      `无法启动 Python 后端:\n${err.message}\n\n请确认 Python3 及依赖已安装。`
    );
  });

  pythonProcess.on("exit", (code) => {
    console.log(`Python process exited with code ${code}`);
    pythonProcess = null;
    if (!isQuitting && mainWindow) {
      mainWindow.webContents.executeJavaScript(
        `document.body.innerHTML = '<div style="padding:40px;text-align:center;font-family:sans-serif;"><h2>后端服务已停止</h2><p>请重新启动应用。</p></div>';`
      );
    }
  });

  return port;
}

function stopPythonBackend() {
  if (pythonProcess) {
    console.log("Stopping Python backend...");
    pythonProcess.kill("SIGTERM");
    setTimeout(() => {
      if (pythonProcess) {
        pythonProcess.kill("SIGKILL");
        pythonProcess = null;
      }
    }, 3000);
  }
}

// ── Window Management ───────────────────────────────────────────────────────

async function createWindow() {
  const loadingHtml = `<html><head><meta charset="utf-8"></head><body style="background:#1e1e2e;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;font-family:'SF Mono','Consolas',monospace;color:#cdd6f4"><div style="text-align:center;max-width:560px;padding:0 20px"><div style="font-size:2em;margin-bottom:24px;font-family:sans-serif">AI 翻译配音</div><div id="load-status" style="color:#89b4fa;font-size:0.95em;letter-spacing:.01em;min-height:1.4em">正在启动…</div><div id="load-detail" style="margin-top:8px;color:#6c7086;font-size:0.78em;min-height:1.2em"></div></div></body></html>`;

  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: "AI 翻译配音",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      nodeIntegration: false,
      contextIsolation: true,
    },
    show: false,
  });

  mainWindow.loadURL(`data:text/html,${encodeURIComponent(loadingHtml)}`);
  mainWindow.show();

  // Wait for loading page DOM to be ready before updating status text
  await new Promise((resolve) => mainWindow.webContents.once("did-finish-load", resolve));

  let port;
  try {
    port = await startPythonBackend(mainWindow);
  } catch (err) {
    dialog.showErrorBox("启动失败", `后端启动失败:\n${err.message}`);
    app.quit();
    return;
  }

  const serverUrl = `http://${GRADIO_HOST}:${port}`;
  try {
    await waitForServer(serverUrl);
    mainWindow.loadURL(serverUrl);
  } catch (err) {
    dialog.showErrorBox("启动失败", `Gradio 后端未能在限定时间内启动:\n${err.message}`);
    app.quit();
    return;
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.on("closed", () => { mainWindow = null; });
}

// ── App Lifecycle ───────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  try {
    await createWindow();
  } catch (err) {
    console.error("Failed to start:", err);
    dialog.showErrorBox("启动失败", `应用启动失败:\n${err.message}`);
    app.quit();
  }
});

app.on("window-all-closed", () => {
  stopPythonBackend();
  app.quit();
});

app.on("before-quit", () => {
  isQuitting = true;
  stopPythonBackend();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
