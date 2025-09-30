const API_BASE = '';

function qs(id){ return document.getElementById(id); }

let currentEvt = null;
let generationState = {
  running: false,
  attempt: 0,
  maxAttempts: 3,
  lastPayload: null,
  originalPayload: null,
  retryDelayMs: 1000,
  totalImages: 1,
  imageIndex: 0
};

async function init() {
  log("Initializing UI...");
  bindButtons();
  await refreshModelList();
}

function bindButtons(){
  qs('ensure-btn').addEventListener('click', onEnsure);
  qs('generate-btn').addEventListener('click', onGenerate);
  qs('preview-btn').addEventListener('click', onPreview);
  qs('clear-cache-btn').addEventListener('click', onClearCache);
}

async function refreshModelList(){
  try {
    const res = await fetch('/api/models');
    log(`[NET] GET /api/models -> ${res.status}`);
    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch(e){ log("[WARN] /api/models response not JSON"); }
    if (!res.ok) {
      log(`[ERROR] /api/models returned ${res.status}: ${text}`);
      return;
    }
    if (!data || !data.supported_models) {
      log("[ERROR] Could not fetch model list: invalid response");
      console.warn("api/models response:", data);
      return;
    }
    populateModels(data.supported_models, data.cached_models || []);
    qs('server-mode').textContent = data.server_mode || 'unknown';
    log("[OK] Model list refreshed");
  } catch (e) {
    log("[ERROR] Could not fetch model list: " + (e && e.message ? e.message : e));
  }
}

function populateModels(supported, cached) {
  const sel = qs('model-select'); sel.innerHTML = '';
  cached = Array.isArray(cached) ? cached : [];
  const seen = new Set();
  cached.forEach(m => { const opt = document.createElement('option'); opt.value = m; opt.textContent = m + ' (cached)'; sel.appendChild(opt); seen.add(m); });
  supported.forEach(m => { if (!seen.has(m)) { const opt = document.createElement('option'); opt.value = m; opt.textContent = m; sel.appendChild(opt); }});
  if (sel.children.length === 0) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = '(no models available)';
    sel.appendChild(opt);
  }
}

function setProgress(p) {
  const pr = qs('progress'); pr.value = p;
  qs('status').textContent = p >= 100 ? 'Done' : `Progress: ${p}%`;
}

function log(s) {
  const pre = qs('log');
  pre.textContent += (new Date()).toLocaleTimeString() + " " + s + "\n";
  pre.scrollTop = pre.scrollHeight;
  console.log(s);
}

async function onEnsure() {
  const model = qs('model-select').value;
  const token = qs('hf-token').value || null;
  setProgress(0); log(`[UI] Ensuring model ${model} (skip if cached)...`);
  try {
    const res = await fetch('/api/download', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ model_id: model, token })});
    log(`[NET] POST /api/download -> ${res.status}`);
    let data;
    try { data = await res.json(); } catch(e){ data = { ok:false, msg: `non-json ${res.status}`} }
    if (res.ok && data.ok) { setProgress(100); log("[UI] Ensure finished: " + (data.msg||'OK')); }
    else { setProgress(0); log("[UI] Ensure failed: " + (data.msg || JSON.stringify(data))); }
    await refreshModelList();
  } catch (e) {
    log("[ERROR] Ensure failed: " + e);
  }
}

function sse_connect(payload) {
  const q = encodeURIComponent(JSON.stringify(payload));
  const url = `/api/generate/stream?payload=${q}`;
  log(`[SSE] Opening EventSource -> ${url}`);
  try {
    const es = new EventSource(url);
    return es;
  } catch (e) {
    log("[ERROR] EventSource construction failed: " + e);
    return null;
  }
}

// Prompt helpers
function askNumber(promptText, defaultVal){
  const v = window.prompt(promptText, String(defaultVal));
  if (v === null) return null; // user cancelled
  const n = parseInt(v, 10);
  return Number.isFinite(n) && n >= 0 ? n : defaultVal;
}

async function onGenerate() {
  if (generationState.running) {
    log("[WARN] Generation already running. Please wait or reload the page.");
    return;
  }
  const payloadBase = {
    prompt: qs('prompt').value,
    model: qs('model-select').value,
    filename: qs('filename').value || 'output.png',
    width: parseInt(qs('width').value,10) || 1024,
    height: parseInt(qs('height').value,10) || 1024,
    steps: parseInt(qs('steps').value,10) || 30,
    guidance: parseFloat(qs('cfg').value) || 7.5,
    device: qs('device-select').value,
    precision: qs('precision-select').value,
    scheduler: qs('scheduler-select').value
  };
  if (!payloadBase.prompt || !payloadBase.prompt.trim()) { log("[UI] Prompt empty - enter something."); return; }

  // ask user how many images and how many attempts (0 => infinite attempts)
  const numImages = askNumber("How many images do you want to try to generate? (1 = one image)", 1);
  if (numImages === null) { log("[UI] Generation cancelled by user."); return; }
  const maxAtt = askNumber("How many attempts per image? (0 = unlimited)", 3);
  if (maxAtt === null) { log("[UI] Generation cancelled by user."); return; }

  generationState.running = true;
  generationState.attempt = 0;
  generationState.maxAttempts = (maxAtt === 0) ? Number.POSITIVE_INFINITY : Math.max(1, maxAtt);
  generationState.originalPayload = JSON.parse(JSON.stringify(payloadBase));
  generationState.lastPayload = JSON.parse(JSON.stringify(payloadBase));
  generationState.totalImages = Math.max(1, numImages);

  log(`[UI] User requested ${generationState.totalImages} image(s), maxAttempts per image = ${ (generationState.maxAttempts === Infinity ? 'infinite' : generationState.maxAttempts) }`);
  disableControls(true);
  setProgress(0);

  // generate images sequentially
  for (let idx = 1; idx <= generationState.totalImages; ++idx) {
    generationState.imageIndex = idx;
    // build filename per image (insert index before extension)
    const baseName = payloadBase.filename || 'output.png';
    const dot = baseName.lastIndexOf('.');
    let fn = baseName;
    if (dot > 0) {
      fn = baseName.slice(0,dot) + `_${idx}` + baseName.slice(dot);
    } else {
      fn = baseName + `_${idx}.png`;
    }
    const payload = JSON.parse(JSON.stringify(payloadBase));
    payload.filename = fn;
    log(`[UI] Starting generation for image ${idx}/${generationState.totalImages} -> filename=${fn}`);

    const res = await startGenerationStreamForImage(payload, generationState.maxAttempts);

    if (res && res.ok) {
      log(`[UI] Image ${idx} generated: ${res.path}`);
    } else {
      log(`[UI] Image ${idx} FAILED after attempts: ${res && res.msg ? res.msg : 'unknown'}`);
    }

    // small delay between images
    await new Promise(r => setTimeout(r, 400));
  }

  generationState.running = false;
  disableControls(false);
  setProgress(0);
  log("[UI] All requested image attempts finished.");
}

function disableControls(disable){
  qs('generate-btn').disabled = disable;
  qs('ensure-btn').disabled = disable;
  qs('clear-cache-btn').disabled = disable;
  qs('preview-btn').disabled = disable;
}

function deepCopy(obj){ return JSON.parse(JSON.stringify(obj)); }

function startGenerationStreamForImage(initialPayload, maxAttempts){
  return new Promise(async (resolve) => {
    let attempt = 0;
    let payload = deepCopy(initialPayload);
    let lastErrorMsg = null;

    while (attempt < maxAttempts) {
      attempt += 1;
      generationState.attempt = attempt; // for UI visibility
      log(`[ATTEMPT] image#${generationState.imageIndex} attempt ${attempt}/${ (maxAttempts === Infinity ? '∞' : maxAttempts) } payload: ${payload.width}x${payload.height} steps=${payload.steps} device=${payload.device}`);

      // open SSE
      const es = sse_connect(payload);
      if (!es) {
        lastErrorMsg = "Could not open SSE";
        log("[ERROR] Could not open EventSource for generation.");
        if (attempt >= maxAttempts) {
          resolve({ok:false, msg:lastErrorMsg});
          return;
        } else {
          // small wait then retry
          await new Promise(r => setTimeout(r, generationState.retryDelayMs));
          continue;
        }
      }

      // closure to handle a single attempt result
      let finished = false;
      const attemptResult = {
        ok: false,
        msg: null,
        path: null
      };

      // set up handlers
      es.onopen = () => log(`[SSE] onopen (image#${generationState.imageIndex} attempt ${attempt}) readyState=${es.readyState}`);
      es.onmessage = (ev) => {
        log(`[SSE RAW] ${ev.data}`);
        let msg;
        try { msg = JSON.parse(ev.data); } catch(e){
          log("[WARN] Could not parse SSE data as JSON: " + e + " raw: " + ev.data);
          return;
        }

        if (msg.type === 'progress') {
          setProgress(msg.value || 0);
        } else if (msg.type === 'log') {
          log("[SERVER] " + (msg.text || ""));
        } else if (msg.type === 'done') {
          finished = true;
          attemptResult.ok = true;
          attemptResult.path = msg.path || null;
          try { es.close(); } catch(e){ }
          log(`[SSE] Received DONE (image#${generationState.imageIndex} attempt ${attempt})`);
          // resolve after a short delay to allow UI flush
          setTimeout(() => resolve({ok:true, path: attemptResult.path}), 50);
        } else if (msg.type === 'error') {
          finished = true;
          attemptResult.ok = false;
          attemptResult.msg = msg.text || "server error";
          lastErrorMsg = attemptResult.msg;
          // include trace in logs
          if (msg.trace) {
            log("[SERVER TRACE START]");
            msg.trace.split('\n').forEach(line => log(line));
            log("[SERVER TRACE END]");
          } else {
            log("[SERVER] Error without trace: " + JSON.stringify(msg));
          }
          try { es.close(); } catch(e){}
        }
      };

      es.onerror = (ev) => {
        log("[SSE ERROR] EventSource error; readyState=" + (es ? es.readyState : 'null') + " ev:" + JSON.stringify(ev));
        // close and let loop decide retry
        try { es.close(); } catch(e){}
        if (!finished) {
          // let the while loop retry
        }
      };

      // wait until attemptResult changed to done or error (poll)
      // We will wait up to a generous timeout (e.g. 300s per attempt) but leave server to send error/done.
      const waitTimeoutMs = 300000; // 5 minutes maximum per attempt
      const startT = Date.now();
      // poll for attempt completion
      while (!finished && (Date.now() - startT) < waitTimeoutMs) {
        // give event loop time to process SSE events
        await new Promise(r => setTimeout(r, 200));
      }

      if (!finished) {
        // attempt timed out
        lastErrorMsg = "Attempt timed out";
        try { es.close(); } catch(e){}
      }

      // if success -> resolve
      if (attemptResult.ok) {
        return; // already resolved inside onmessage done
      }

      // analyze lastErrorMsg / logs to decide fallback
      const traceLower = (lastErrorMsg || "").toLowerCase();
      const isOOM = traceLower.includes("out of memory") || traceLower.includes("cuda out of memory") || traceLower.includes("oom");
      const isPipeLoad = traceLower.includes("failed to load pipeline") || traceLower.includes("file not found") || traceLower.includes("no such file");

      if (isPipeLoad) {
        log("[AUTO] Detected pipeline load error - will not retry (check model cache/HF token).");
        resolve({ok:false, msg:lastErrorMsg});
        return;
      }

      // fallback strategy for next attempt:
      if (isOOM) {
        log("[AUTO] Detected OOM -> will try CPU + reduce size on next attempt.");
        payload.device = "cpu";
        payload.precision = "float32";
        payload.width = Math.max(256, Math.floor(payload.width / 2));
        payload.height = Math.max(256, Math.floor(payload.height / 2));
        payload.steps = Math.max(1, Math.floor(payload.steps / 2));
      } else {
        // generic error -> progressively downscale
        log("[AUTO] Generic server error -> downscaling and retrying.");
        payload.width = Math.max(256, Math.floor(payload.width / 2));
        payload.height = Math.max(256, Math.floor(payload.height / 2));
        payload.steps = Math.max(1, Math.floor(payload.steps / 2));
      }

      // if we've exhausted attempts, give up
      if (attempt >= maxAttempts) {
        log(`[AUTO] Reached max attempts (${maxAttempts}) for this image. Giving up.`);
        resolve({ok:false, msg:lastErrorMsg || "max attempts reached"});
        return;
      }

      // short delay before reattempt
      await new Promise(r => setTimeout(r, generationState.retryDelayMs));
      // continue loop -> next attempt with modified payload
    } // end while

    // fallback if somehow loop exits
    resolve({ok:false, msg:"Exhausted attempts"});
  });
}

function handleDoneMessage(msg){
  try {
    const rawPath = msg.path;
    if (rawPath && rawPath !== "None") {
      const urlStr = String(rawPath);
      let url = null;
      if (urlStr.startsWith('/')) {
        url = urlStr;
      } else {
        const parts = urlStr.split ? urlStr.split('/') : [urlStr];
        const last = parts.length ? parts[parts.length - 1] : urlStr;
        if (last) url = '/generated_images/' + last;
      }
      if (url) {
        try { window.open(url, '_blank'); log("[UI] Opened generated image: " + url); }
        catch (openErr) { log("[WARN] Could not open image tab: " + openErr); }
      } else {
        log("[UI] WARNING: Computed url is empty, cannot open image.");
      }
    } else {
      log("[UI] WARNING: msg.path is empty or 'None', cannot open image.");
    }
  } catch (e) {
    log("[UI] ERROR handling done message: " + e);
  }
}

async function onPreview() {
  try {
    const res = await fetch('/api/preview');
    log(`[NET] GET /api/preview -> ${res.status}`);
    const data = await res.json();
    if (data.ok) { window.open(data.url, '_blank'); log("[UI] Previewing: " + data.url); }
    else { log("[UI] " + (data.msg || JSON.stringify(data))); }
  } catch (e) { log("[ERROR] " + e); }
}

async function onClearCache() {
  if (!confirm("Clear entire model_cache? This will remove all cached models.")) return;
  try {
    const res = await fetch('/api/clear_cache', { method:'POST' });
    log(`[NET] POST /api/clear_cache -> ${res.status}`);
    const data = await res.json();
    log("[INFO] " + (data.msg||"Cleared"));
    const newList = await (await fetch('/api/models')).json();
    if (newList && newList.supported_models) populateModels(newList.supported_models, newList.cached_models || []);
  } catch (e) { log("[ERROR] " + e); }
}

window.addEventListener('load', init);