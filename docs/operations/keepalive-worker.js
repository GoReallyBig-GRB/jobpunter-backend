// Cloudflare Worker "jobpunter-keepalive" (deployed 2026-11-12; reference copy, not built or run by this repository).
// Crons: */10 * * * * (keep-alive) and */15 * * * * (worker-cycle trigger, added 2026-11-14). Bindings: KV (namespace afa7c10692bb493a9ccf0208e87ac57b), BROWSER (Browser Rendering),
// WORKER_TRIGGER_SECRET (Worker secret binding; its value lives only in Cloudflare and Render, never in this repository). No public URL.
// Pings /api/v1/health; after 3 failed attempts (502/503/504/network) it wakes the Render Free service with a headless browser,
// at most once per 30 minutes and 4 times per UTC day (Browser Rendering Free allows 10 browser-minutes per day).
const URL_ = "https://jobpunter-api.onrender.com/api/v1/health";
const UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36 jobpunter-keepalive";
const RING = 288;
const WAKE_COOLDOWN_MS = 30 * 60 * 1000;
const WAKE_MAX_PER_DAY = 4;
const WAKE_BUDGET_MS = 110000;
const WAKE_STATUSES = [0, 502, 503, 504];
const FAKE = "https://fake.host";

async function once() {
  const t = Date.now(); let status = 0; let error = null;
  const ac = new AbortController(); const to = setTimeout(() => ac.abort(), 60000);
  try { const r = await fetch(URL_, { signal: ac.signal, headers: { "user-agent": UA, "accept": "text/html,application/json;q=0.9,*/*;q=0.8" } }); status = r.status; await r.arrayBuffer(); }
  catch (e) { error = String((e && e.name) || e); }
  clearTimeout(to);
  return { at_utc: new Date(t).toISOString(), status, error, seconds: Math.round((Date.now() - t) / 100) / 10 };
}

// Minimal Chrome DevTools Protocol client over the Browser Rendering binding (no library).
async function browserWake(env, started) {
  const acq = await env.BROWSER.fetch(FAKE + "/v1/devtools/browser?keep_alive=120000", { method: "POST" });
  const acqText = await acq.text();
  if (acq.status !== 200) throw new Error("acquire " + acq.status + " " + acqText.slice(0, 120));
  const sessionId = JSON.parse(acqText).sessionId;
  const res = await env.BROWSER.fetch(FAKE + "/v1/devtools/browser/" + sessionId, { headers: { Upgrade: "websocket" } });
  const ws = res.webSocket;
  if (!ws) throw new Error("no websocket " + res.status);
  ws.accept();
  let id = 0; const pending = new Map();
  ws.addEventListener("message", (ev) => { try { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } } catch (e) {} });
  const send = (method, params, sid) => new Promise((resolve, reject) => {
    const i = ++id;
    const t = setTimeout(() => { pending.delete(i); reject(new Error("cdp timeout " + method)); }, 25000);
    pending.set(i, (m) => { clearTimeout(t); resolve(m); });
    ws.send(JSON.stringify(Object.assign({ id: i, method, params: params || {} }, sid ? { sessionId: sid } : {})));
  });
  let ok = false; let detail = null;
  try {
    const tgt = await send("Target.createTarget", { url: "about:blank" });
    const targetId = tgt.result && tgt.result.targetId;
    const att = await send("Target.attachToTarget", { targetId, flatten: true });
    const sid = att.result && att.result.sessionId;
    await send("Page.enable", {}, sid);
    await send("Page.navigate", { url: URL_ }, sid);
    while (!ok && Date.now() - started < WAKE_BUDGET_MS) {
      await new Promise((r) => setTimeout(r, 5000));
      try {
        const ev = await send("Runtime.evaluate", { expression: "document.body ? document.body.innerText : ''", returnByValue: true }, sid);
        const text = String((ev.result && ev.result.result && ev.result.result.value) || "");
        if (text.includes('"status":"ok"') || text.includes('"status": "ok"')) ok = true;
      } catch (e) { detail = String((e && e.message) || e).slice(0, 120); }
    }
  } finally {
    try { await send("Browser.close"); } catch (e) {}
    try { ws.close(); } catch (e) {}
  }
  return { ok, detail };
}

async function wake(env) {
  const now = Date.now();
  const day = new Date(now).toISOString().slice(0, 10);
  const lastWake = parseInt((await env.KV.get("ka:wake:last")) || "0", 10);
  const todayCount = parseInt((await env.KV.get("ka:wake:count:" + day)) || "0", 10);
  if (now - lastWake < WAKE_COOLDOWN_MS) return { skipped: "cooldown" };
  if (todayCount >= WAKE_MAX_PER_DAY) return { skipped: "daily_cap" };
  // Record the attempt BEFORE launching so a crash or timeout still counts against the budget.
  await env.KV.put("ka:wake:last", String(now));
  await env.KV.put("ka:wake:count:" + day, String(todayCount + 1), { expirationTtl: 172800 });
  const started = Date.now();
  let ok = false; let detail = null;
  try { const r = await browserWake(env, started); ok = r.ok; detail = r.detail; }
  catch (e) { detail = String((e && e.message) || e).slice(0, 200); }
  const rec = { at_utc: new Date(started).toISOString(), ok, seconds: Math.round((Date.now() - started) / 100) / 10, detail };
  await env.KV.put("ka:wake:log:" + rec.at_utc, JSON.stringify(rec), { expirationTtl: 604800 });
  await env.KV.put("ka:wake:lastresult", JSON.stringify(rec));
  return rec;
}

async function run(env) {
  let res = await once();
  let attempts = 1;
  while (res.status !== 200 && attempts < 3) { await new Promise((r) => setTimeout(r, 20000)); res = await once(); attempts++; }
  res.attempts = attempts;
  const n = parseInt((await env.KV.get("ka:count")) || "0", 10);
  const fails = res.status === 200 ? 0 : parseInt((await env.KV.get("ka:fails")) || "0", 10) + 1;
  await env.KV.put("ka:h:" + String(n % RING).padStart(3, "0"), JSON.stringify(res));
  await env.KV.put("ka:count", String(n + 1));
  await env.KV.put("ka:last", JSON.stringify(res));
  await env.KV.put("ka:fails", String(fails));
  if (res.status === 200) { await env.KV.put("ka:last_ok", res.at_utc); return; }
  console.error("keepalive failure", JSON.stringify(res), "consecutive_failed_runs=" + fails);
  if (WAKE_STATUSES.includes(res.status)) {
    const w = await wake(env);
    console.error("wake attempt", JSON.stringify(w));
  }
}
const TRIGGER_URL = "https://jobpunter-api.onrender.com/api/v1/internal/worker-cycle";
async function trigger(env) {
  const headers = { "X-Worker-Trigger-Secret": env.WORKER_TRIGGER_SECRET, "user-agent": UA };
  const rec = { at_utc: new Date().toISOString(), start_status: 0, final: null };
  try {
    const r = await fetch(TRIGGER_URL, { method: "POST", headers, signal: AbortSignal.timeout(60000) });
    rec.start_status = r.status; await r.arrayBuffer();
    if (r.status === 202) {
      for (let i = 0; i < 24; i++) {
        await new Promise((res) => setTimeout(res, 15000));
        const g = await fetch(TRIGGER_URL, { headers, signal: AbortSignal.timeout(30000) });
        const st = await g.json();
        if (st.state === "FINISHED") { rec.final = st; break; }
      }
    }
  } catch (e) { rec.error = String((e && e.name) || e); }
  await env.KV.put("tr:last", JSON.stringify(rec));
  await env.KV.put("tr:h:" + rec.at_utc, JSON.stringify(rec), { expirationTtl: 604800 });
}
export default { async scheduled(event, env, ctx) { ctx.waitUntil(event.cron === "*/15 * * * *" ? trigger(env) : run(env)); } };
