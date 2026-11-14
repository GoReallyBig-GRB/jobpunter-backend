// Cloudflare Worker "jobpunter-keepalive" (reference copy, not built or run by this repository; synced to the deployed code 2026-11-14).
// Crons: */10 * * * * (keep-alive) and */15 * * * * (worker-cycle trigger). Bindings: KV (namespace afa7c10692bb493a9ccf0208e87ac57b), BROWSER (Browser Rendering),
// WORKER_TRIGGER_SECRET (Worker secret binding; its value lives only in Cloudflare and Render, never in this repository). No public URL.
// Keep-alive: pings /api/v1/health; after 3 failed attempts (502/503/504/network) it wakes the Render Free service with a headless browser,
// at most once per 30 minutes and 4 times per UTC day (Browser Rendering Free allows 10 browser-minutes per day).
// KV budget (free tier: 1,000 writes/day): healthy runs write about once an hour (~48/day); the trigger writes about once an hour or when a cycle is not fully OK.
const URL_ = "https://jobpunter-api.onrender.com/api/v1/health";
const UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36 jobpunter-keepalive";
const RING = 288;
const WAKE_COOLDOWN_MS = 30 * 60 * 1000;
const WAKE_MAX_PER_DAY = 4;
const WAKE_BUDGET_MS = 110000;
const WAKE_STATUSES = [0, 502, 503, 504];
const HEALTHY_WRITE_INTERVAL_MS = 55 * 60 * 1000; // healthy runs write status to KV at most about once an hour
const FAKE = "https://fake.host";

// KV writes must never throw: if the daily write cap is hit, the keepalive and wake logic must still run.
async function kvPut(env, key, value, opts) {
  try { await env.KV.put(key, value, opts); }
  catch (e) { console.error("kv put failed", key, String((e && e.message) || e)); }
}

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
  await kvPut(env, "ka:wake:last", String(now));
  await kvPut(env, "ka:wake:count:" + day, String(todayCount + 1), { expirationTtl: 172800 });
  const started = Date.now();
  let ok = false; let detail = null;
  try { const r = await browserWake(env, started); ok = r.ok; detail = r.detail; }
  catch (e) { detail = String((e && e.message) || e).slice(0, 200); }
  const rec = { at_utc: new Date(started).toISOString(), ok, seconds: Math.round((Date.now() - started) / 100) / 10, detail };
  await kvPut(env, "ka:wake:log:" + rec.at_utc, JSON.stringify(rec), { expirationTtl: 604800 });
  await kvPut(env, "ka:wake:lastresult", JSON.stringify(rec));
  return rec;
}

async function run(env) {
  let res = await once();
  let attempts = 1;
  while (res.status !== 200 && attempts < 3) { await new Promise((r) => setTimeout(r, 20000)); res = await once(); attempts++; }
  res.attempts = attempts;

  if (res.status === 200) {
    // Healthy path: mostly reads. Writes at most about once an hour.
    const [failsRaw, lastOk] = await Promise.all([env.KV.get("ka:fails"), env.KV.get("ka:last_ok")]);
    if (parseInt(failsRaw || "0", 10) > 0) await kvPut(env, "ka:fails", "0"); // clear only if needed
    const age = lastOk ? Date.now() - Date.parse(lastOk) : Infinity;
    if (!(age < HEALTHY_WRITE_INTERVAL_MS)) { // a missing or unparseable value also triggers a write
      await kvPut(env, "ka:last", JSON.stringify(res));
      await kvPut(env, "ka:last_ok", res.at_utc);
    }
    return;
  }

  // Failure path: same records as before, but the wake runs before any KV write and KV errors cannot block it.
  const n = parseInt((await env.KV.get("ka:count")) || "0", 10);
  const fails = parseInt((await env.KV.get("ka:fails")) || "0", 10) + 1;
  console.error("keepalive failure", JSON.stringify(res), "consecutive_failed_runs=" + fails);
  if (WAKE_STATUSES.includes(res.status)) {
    const w = await wake(env);
    console.error("wake attempt", JSON.stringify(w));
  }
  await kvPut(env, "ka:h:" + String(n % RING).padStart(3, "0"), JSON.stringify(res));
  await kvPut(env, "ka:count", String(n + 1));
  await kvPut(env, "ka:last", JSON.stringify(res));
  await kvPut(env, "ka:fails", String(fails));
}
const TRIGGER_URL = "https://jobpunter-api.onrender.com/api/v1/internal/worker-cycle";
// Calls the protected worker-cycle route (one bounded cycle of the light workers). KV is written about once an hour, or when a cycle is not fully OK; every run is logged.
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
  const notOk = rec.error || (rec.start_status !== 202 && rec.start_status !== 409) || (rec.final && rec.final.results.some((x) => x.status !== "OK"));
  console.log("worker-cycle", JSON.stringify(rec));
  if (notOk || new Date().getUTCMinutes() < 15) await kvPut(env, "tr:last", JSON.stringify(rec));
}
export default { async scheduled(event, env, ctx) { ctx.waitUntil(event.cron === "*/15 * * * *" ? trigger(env) : run(env)); } };
