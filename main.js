const { app, BrowserWindow, dialog, shell } = require("electron");
const path = require("path");
const { spawn, spawnSync } = require("child_process");
const http = require("http");
const net = require("net");
const fs = require("fs");

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

// ── Python Backend ──────────────────────────────────────────────────────────

async function startPythonBackend() {
  const resPath = syncRuntimePath();
  const pythonCmd = findPythonCommand();
  console.log(`Using Python: ${pythonCmd.label}`);

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
  const port = await startPythonBackend();
  const serverUrl = `http://${GRADIO_HOST}:${port}`;

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

  // Show a loading page while Gradio starts
  mainWindow.loadURL(`data:text/html,<html><body style="background:#1e1e2e;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;font-family:sans-serif;color:#cdd6f4"><div style="text-align:center"><div style="font-size:2em;margin-bottom:16px">AI 翻译配音</div><div style="color:#a6adc8">正在启动后端服务，请稍候…</div></div></body></html>`);
  mainWindow.show();

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
