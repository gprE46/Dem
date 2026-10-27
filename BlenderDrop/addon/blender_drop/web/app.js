/* Blender Drop - mobile page (vanilla JS, no dependencies).
 *
 * Flow per file:  XHR POST /upload (real progress) -> 202 {job}
 *                 poll GET /status?job=<id> until IMPORTED / ERROR
 * Connection:     poll GET /status every few seconds (server info + history)
 */
(function () {
  "use strict";

  // ---------------------------------------------------------------- i18n
  var STRINGS = {
    en: {
      subtitle: "Send assets to Blender",
      connecting: "Connecting…",
      connected: "Connected ✓",
      disconnected: "Blender not reachable",
      badToken: "Invalid link",
      noToken: "Link without token",
      select: "Select File",
      hint: "GLB, FBX, OBJ, USD, STL, SVG, images, .blend or .zip",
      recent: "Recent uploads",
      empty: "Nothing sent yet",
      footer: "Keep this page open and the screen on until the upload finishes.",
      uploading: "Uploading…",
      uploaded: "Uploaded ✓",
      queued: "Waiting for Blender…",
      importing: "Importing…",
      imported: "Imported successfully ✓",
      failed: "Import failed",
      uploadFailed: "Upload failed",
      fileOf: "{i} of {n}",
      tooLarge: "File too large. Maximum size is {max}.",
      unsupported: "'{ext}' files can't be imported by this Blender.",
      noExt: "The file has no extension, Blender can't know its type.",
      connLost: "Connection lost. Check that the phone and the computer are on the same Wi-Fi and try again.",
      serverOff: "Blender Drop is not running. Press Start Server in Blender.",
      tokenHelp: "Blender was restarted or the link is old. Scan the QR code in Blender again.",
      noTokenHelp: "Open the exact link shown in Blender (Copy URL or Show QR).",
      lostAfterUpload: "The file was uploaded but the connection was lost. Check Blender.",
      emptyFile: "The file is empty.",
      diskFull: "Not enough free disk space on the computer.",
      info: "Blender {v} · {scene}"
    },
    es: {
      subtitle: "Envía assets a Blender",
      connecting: "Conectando…",
      connected: "Conectado ✓",
      disconnected: "Blender no responde",
      badToken: "Enlace no válido",
      noToken: "Enlace sin token",
      select: "Seleccionar archivo",
      hint: "GLB, FBX, OBJ, USD, STL, SVG, imágenes, .blend o .zip",
      recent: "Envíos recientes",
      empty: "Todavía no has enviado nada",
      footer: "Mantén esta página abierta y la pantalla encendida hasta que termine el envío.",
      uploading: "Subiendo…",
      uploaded: "Subido ✓",
      queued: "Esperando a Blender…",
      importing: "Importando…",
      imported: "Importado correctamente ✓",
      failed: "Error al importar",
      uploadFailed: "Error al subir",
      fileOf: "{i} de {n}",
      tooLarge: "Archivo demasiado grande. El máximo es {max}.",
      unsupported: "Este Blender no puede importar archivos '{ext}'.",
      noExt: "El archivo no tiene extensión; Blender no sabe qué tipo es.",
      connLost: "Se perdió la conexión. Comprueba que el móvil y el ordenador están en la misma Wi-Fi y vuelve a intentarlo.",
      serverOff: "Blender Drop no está activo. Pulsa Start Server en Blender.",
      tokenHelp: "Blender se reinició o el enlace es antiguo. Vuelve a escanear el QR en Blender.",
      noTokenHelp: "Abre exactamente el enlace que muestra Blender (Copy URL o Show QR).",
      lostAfterUpload: "El archivo se subió, pero se perdió la conexión. Revisa Blender.",
      emptyFile: "El archivo está vacío.",
      diskFull: "No hay espacio libre suficiente en el ordenador.",
      info: "Blender {v} · {scene}"
    }
  };
  var lang = (navigator.language || "en").toLowerCase().indexOf("es") === 0 ? "es" : "en";
  document.documentElement.lang = lang;

  function t(key, vars) {
    var s = (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key] || key;
    if (vars) {
      Object.keys(vars).forEach(function (k) { s = s.split("{" + k + "}").join(vars[k]); });
    }
    return s;
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-i18n]"), function (el) {
    el.textContent = t(el.getAttribute("data-i18n"));
  });

  // ---------------------------------------------------------------- dom
  var $ = function (id) { return document.getElementById(id); };
  var els = {
    conn: $("connection"), connText: $("connText"), connInfo: $("connInfo"),
    picker: $("picker"), input: $("fileInput"),
    card: $("current"), name: $("curName"), count: $("curCount"),
    fill: $("curFill"), state: $("curState"), message: $("curMessage"),
    list: $("recentList")
  };

  var token = new URLSearchParams(location.search).get("token") || "";
  var serverInfo = null;
  var uploadQueue = [];
  var batchTotal = 0;
  var batchIndex = 0;
  var busy = false;
  var statusTimer = null;
  var failures = 0;

  function withToken(path) {
    return path + (path.indexOf("?") >= 0 ? "&" : "?") + "token=" + encodeURIComponent(token);
  }

  function humanSize(bytes) {
    var units = ["B", "KB", "MB", "GB"];
    var i = 0;
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
    return (i === 0 ? bytes : bytes.toFixed(1)) + " " + units[i];
  }

  function extOf(name) {
    var m = /\.([^.\/\\]+)$/.exec(name || "");
    return m ? "." + m[1].toLowerCase() : "";
  }

  function getJSON(path, timeoutMs) {
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs || 6000) : null;
    return fetch(withToken(path), { cache: "no-store", signal: controller ? controller.signal : undefined })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (body) {
          if (timer) clearTimeout(timer);
          if (!res.ok) {
            var err = new Error(body.message || ("HTTP " + res.status));
            err.code = body.error || "http_" + res.status;
            err.status = res.status;
            throw err;
          }
          return body;
        });
      }, function (e) {
        if (timer) clearTimeout(timer);
        var err = new Error("network");
        err.code = "network";
        throw err;
      });
  }

  // ---------------------------------------------------------------- connection
  function setConnection(kind, text, info) {
    els.conn.className = "connection is-" + kind;
    els.connText.textContent = text;
    els.connInfo.textContent = info || "";
  }

  function friendlyError(code, fallback) {
    switch (code) {
      case "invalid_token": return t("tokenHelp");
      case "missing_token": return t("noTokenHelp");
      case "connection_lost": case "network": return t("connLost");
      case "server_stopped": return t("serverOff");
      case "empty_file": return t("emptyFile");
      case "disk_full": return t("diskFull");
      default: return fallback || t("connLost");
    }
  }

  function refreshStatus() {
    if (!token) {
      setConnection("error", t("noToken"), t("noTokenHelp"));
      els.picker.classList.add("disabled");
      return;
    }
    getJSON("/status").then(function (data) {
      failures = 0;
      serverInfo = data;
      var info = data.blender ? t("info", { v: data.blender, scene: data.scene || "" }) : "";
      setConnection("connected", t("connected"), info);
      els.picker.classList.remove("disabled");
      renderRecent(data.jobs || []);
    }, function (err) {
      failures++;
      if (err.code === "invalid_token" || err.code === "missing_token") {
        setConnection("error", t("badToken"), friendlyError(err.code));
        els.picker.classList.add("disabled");
      } else if (failures >= 2) {
        setConnection("error", t("disconnected"), t("serverOff"));
      }
    });
  }

  function scheduleStatus() {
    clearInterval(statusTimer);
    statusTimer = setInterval(function () {
      if (!document.hidden) refreshStatus();
    }, 3000);
  }

  // ---------------------------------------------------------------- recent list
  function renderRecent(jobs) {
    els.list.textContent = "";
    if (!jobs.length) {
      var li = document.createElement("li");
      li.className = "empty";
      li.textContent = t("empty");
      els.list.appendChild(li);
      return;
    }
    jobs.slice(0, 12).forEach(function (job) {
      var li = document.createElement("li");
      var icon = document.createElement("span");
      var name = document.createElement("span");
      var meta = document.createElement("span");
      icon.className = "icon";
      name.className = "name";
      meta.className = "meta";
      if (job.state === "IMPORTED") { li.className = "ok"; icon.textContent = "✓"; meta.textContent = humanSize(job.size); }
      else if (job.state === "ERROR") { li.className = "err"; icon.textContent = "✗"; meta.textContent = t("failed"); }
      else { li.className = "busy"; icon.textContent = "•"; meta.textContent = stateText(job.state); }
      name.textContent = job.filename;
      if (job.state === "ERROR" && job.message) li.title = job.message;
      li.appendChild(icon); li.appendChild(name); li.appendChild(meta);
      els.list.appendChild(li);
    });
  }

  function stateText(state) {
    return {
      UPLOADING: t("uploading"), UPLOADED: t("uploaded"), QUEUED: t("queued"),
      IMPORTING: t("importing"), IMPORTED: t("imported"), ERROR: t("failed")
    }[state] || state;
  }

  // ---------------------------------------------------------------- current card
  function showCard(file) {
    els.card.classList.remove("hidden");
    els.name.textContent = file.name;
    els.count.textContent = batchTotal > 1 ? t("fileOf", { i: batchIndex, n: batchTotal }) : humanSize(file.size);
    setProgress(0, "");
    setState(t("uploading"), "", "");
  }

  function setProgress(pct, cls) {
    els.fill.className = "fill" + (cls ? " " + cls : "");
    els.fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
  }

  function setState(text, cls, message) {
    els.state.className = "cur-state" + (cls ? " " + cls : "");
    els.state.textContent = text;
    els.message.textContent = message || "";
  }

  // ---------------------------------------------------------------- upload
  function validate(file) {
    var ext = extOf(file.name);
    if (!ext) return t("noExt");
    if (serverInfo) {
      if (serverInfo.extensions && serverInfo.extensions.indexOf(ext) < 0) return t("unsupported", { ext: ext });
      if (serverInfo.max_upload_bytes && file.size > serverInfo.max_upload_bytes) {
        return t("tooLarge", { max: humanSize(serverInfo.max_upload_bytes) });
      }
    }
    if (file.size === 0) return t("emptyFile");
    return null;
  }

  function upload(file) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("POST", withToken("/upload"));
      xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable) {
          var pct = Math.floor((e.loaded / e.total) * 100);
          setProgress(pct, "");
          setState(t("uploading") + " " + pct + "%", "", humanSize(e.loaded) + " / " + humanSize(e.total));
        } else {
          setProgress(0, "indeterminate");
        }
      };
      xhr.onload = function () {
        var body = {};
        try { body = JSON.parse(xhr.responseText || "{}"); } catch (e) { /* ignore */ }
        if (xhr.status === 202 && body.job) {
          resolve(body.job);
        } else {
          reject({ code: body.error || "http_" + xhr.status, message: body.message });
        }
      };
      xhr.onerror = function () { reject({ code: "connection_lost" }); };
      xhr.onabort = function () { reject({ code: "connection_lost" }); };
      var form = new FormData();
      form.append("file", file, file.name);
      xhr.send(form);
    });
  }

  function waitForImport(jobId) {
    return new Promise(function (resolve, reject) {
      var misses = 0;
      function poll() {
        getJSON("/status?job=" + encodeURIComponent(jobId), 5000).then(function (data) {
          misses = 0;
          var job = data.job;
          if (job.state === "IMPORTED") return resolve(job);
          if (job.state === "ERROR") return reject({ code: job.error_code, message: job.message, imported: true });
          if (job.state === "IMPORTING") { setProgress(100, "indeterminate"); setState(t("importing"), "", ""); }
          else if (job.state === "QUEUED") { setProgress(100, "indeterminate"); setState(t("queued"), "", ""); }
          setTimeout(poll, 400);
        }, function (err) {
          misses++;
          if (err.code === "unknown_job" || err.code === "invalid_token" || misses > 15) {
            return reject({ code: "lost_after_upload", message: t("lostAfterUpload"), imported: true });
          }
          setTimeout(poll, 1000);
        });
      }
      poll();
    });
  }

  function processQueue() {
    if (busy) return;
    var file = uploadQueue.shift();
    if (!file) {
      els.picker.classList.remove("disabled");
      batchTotal = 0;
      batchIndex = 0;
      refreshStatus();
      return;
    }
    busy = true;
    batchIndex++;
    showCard(file);
    var problem = validate(file);
    if (problem) {
      setProgress(100, "err");
      setState(t("uploadFailed"), "err", problem);
      busy = false;
      return setTimeout(processQueue, 1200);
    }
    upload(file).then(function (job) {
      setProgress(100, "ok");
      setState(t("uploaded"), "ok", "");
      refreshStatus();
      return waitForImport(job.id);
    }).then(function (job) {
      setProgress(100, "ok");
      setState(t("imported"), "ok", job.details || "");
    }).catch(function (err) {
      setProgress(100, "err");
      var title = err.imported ? t("failed") : t("uploadFailed");
      var msg = err.code === "lost_after_upload" ? err.message : friendlyError(err.code, err.message);
      setState(title, "err", msg);
    }).then(function () {
      busy = false;
      refreshStatus();
      setTimeout(processQueue, 600);
    });
  }

  els.input.addEventListener("change", function () {
    var files = Array.prototype.slice.call(els.input.files || []);
    els.input.value = "";       // allow choosing the same file again
    if (!files.length) return;
    uploadQueue = uploadQueue.concat(files);
    batchTotal += files.length;
    processQueue();
  });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) refreshStatus();
  });
  window.addEventListener("pageshow", refreshStatus);

  refreshStatus();
  scheduleStatus();
})();
