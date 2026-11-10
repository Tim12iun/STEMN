// stemn-runner.js: runs inside [node.script] in the Max for Live device.
//
// Starts the installed stemn app as a command line process (the same way the
// app window does it: own process, result and progress files) and reports back:
//   app <0|1> <path>            stemn.app found or not
//   progress <0..1> <note>      while separating
//   done <json>                 {"kick": "/abs/... - kick.wav", ...}
//   error <text>
//   cancelled

const maxApi = require("max-api");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const STEM_ORDER = ["kick", "snare", "hihat", "toms", "ride", "crash"];

function findApp() {
  const candidates = [
    process.env.STEMN_APP, // for tests
    "/Applications/stemn.app/Contents/MacOS/stemn",
    path.join(os.homedir(), "Applications/stemn.app/Contents/MacOS/stemn"),
  ];
  return candidates.filter(Boolean).find((p) => {
    try {
      fs.accessSync(p, fs.constants.X_OK);
      return true;
    } catch (_e) {
      return false;
    }
  });
}

// Max hands over paths like "Macintosh HD:/Users/me/Loop.wav".
function toPosix(p) {
  p = String(p || "").trim();
  const m = p.match(/^([^/:]+):(\/.*)$/);
  if (!m) return p;
  if (fs.existsSync(m[2])) return m[2]; // boot volume
  return path.join("/Volumes", m[1], m[2]);
}

function writable(dir) {
  try {
    fs.accessSync(dir, fs.constants.W_OK);
    return true;
  } catch (_e) {
    return false;
  }
}

let child = null;
let timer = null;
let cancelled = false;

function readProgress(file) {
  try {
    const lines = fs.readFileSync(file, "utf8").split("\n");
    const value = parseFloat(lines[0]);
    if (Number.isNaN(value)) return null;
    return [Math.max(0, Math.min(1, value)), (lines[1] || "").trim()];
  } catch (_e) {
    return null;
  }
}

function cleanup(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch (_e) {}
}

maxApi.addHandler("check", () => {
  const app = findApp();
  maxApi.outlet("app", app ? 1 : 0, app || "");
});

maxApi.addHandler("cancel", () => {
  if (!child) return;
  cancelled = true;
  child.kill("SIGTERM");
});

maxApi.addHandler("separate", (json) => {
  if (child) return maxApi.outlet("error", "Es läuft schon eine Trennung.");
  let req;
  try {
    req = JSON.parse(String(json));
  } catch (_e) {
    return maxApi.outlet("error", "Interner Fehler: Anfrage nicht lesbar.");
  }
  const app = findApp();
  if (!app) return maxApi.outlet("error", "stemn.app fehlt im Programme-Ordner.");
  const input = toPosix(req.input);
  if (!fs.existsSync(input)) return maxApi.outlet("error", "Datei nicht gefunden: " + path.basename(input));

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "stemn-m4l-"));
  const resultFile = path.join(tmp, "result.json");
  const progressFile = path.join(tmp, "progress.txt");
  const args = ["separate", input, "-q", "best", "--result-file", resultFile, "--progress-file", progressFile];
  const stems = (req.stems || []).filter((s) => STEM_ORDER.includes(s));
  if (stems.length && stems.length < STEM_ORDER.length) args.push("-s", stems.join(","));
  // Samples from the Core Library or a read-only drive: stems go to ~/Music/stemn instead.
  if (!writable(path.dirname(input))) {
    const base = path.basename(input, path.extname(input));
    args.push("-o", path.join(os.homedir(), "Music", "stemn", base + " stems"));
  }

  cancelled = false;
  let stderr = "";
  child = spawn(app, args, {
    env: Object.assign({}, process.env, { STEMN_WORK_DIR: tmp }),
    stdio: ["ignore", "ignore", "pipe"],
  });
  child.stderr.on("data", (d) => {
    stderr = (stderr + d.toString()).slice(-4000);
  });
  maxApi.outlet("progress", 0, "Modell wird geladen");
  let last = "";
  timer = setInterval(() => {
    const p = readProgress(progressFile);
    if (!p) return;
    const key = p[0].toFixed(3) + p[1];
    if (key === last) return;
    last = key;
    maxApi.outlet("progress", p[0], p[1]);
  }, 200);

  const finish = (code, signal) => {
    clearInterval(timer);
    timer = null;
    child = null;
    let data = {};
    try {
      data = JSON.parse(fs.readFileSync(resultFile, "utf8"));
    } catch (_e) {}
    cleanup(tmp);
    if (cancelled) return maxApi.outlet("cancelled");
    if (data.error) return maxApi.outlet("error", String(data.error));
    if (code === 0 && data.stems) {
      const files = Object.values(data.stems)[0] || {};
      const ordered = {};
      STEM_ORDER.forEach((s) => {
        if (files[s]) ordered[s] = files[s];
      });
      return maxApi.outlet("done", JSON.stringify(ordered));
    }
    if (signal === "SIGKILL" || code === 137) {
      return maxApi.outlet("error", "Arbeitsspeicher voll. Schließ andere Programme und versuch es noch einmal.");
    }
    const tail = stderr.trim().split("\n").slice(-1)[0] || "";
    maxApi.outlet("error", "Die Trennung ist abgestürzt (Code " + (code === null ? signal : code) + "). " + tail);
  };
  child.on("error", (e) => {
    clearInterval(timer);
    child = null;
    cleanup(tmp);
    maxApi.outlet("error", "stemn ließ sich nicht starten: " + e.message);
  });
  child.on("exit", finish);
});

// Device removed or Live closed: the separation must not keep running.
process.on("exit", () => {
  if (child) child.kill("SIGTERM");
});

maxApi.outlet("ready");
