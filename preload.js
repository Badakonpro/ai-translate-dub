const { contextBridge, ipcRenderer } = require("electron");

// Expose a minimal, safe API to the renderer (Gradio UI).
// The Gradio web UI doesn't need much, but we provide basic
// app info and window controls for future use.

contextBridge.exposeInMainWorld("electronApp", {
  platform: process.platform,
  version: process.env.npm_package_version || "1.0.0",

  // Allow the page to know it's running inside Electron
  isElectron: true,

  // Notify main process when page has fully loaded
  onReady: (callback) => {
    window.addEventListener("DOMContentLoaded", callback);
  },
});
