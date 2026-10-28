// Run the REAL web live-caption code (extracted verbatim from app.js) against a
// fake STT server with controllable latency, reproducing the utterance-boundary
// race: utterance 1's FINAL is slow, utterance 2 starts (speechstart) and sends
// partials before that final returns.
//
// usage: node tests/web_live_race.mjs webapp/static/app.js  (driven by tests/test_live_stream_race.py)
import fs from "node:fs";

const src = fs.readFileSync(process.argv[2], "utf8");
const start = src.indexOf('let liveCommittedText = "";');
const end = src.indexOf("// Finalize one sentence: show the source line now");
if (start < 0 || end < 0) throw new Error("region markers not found");
let region = src.slice(start, end)
  .replace("async function recognizeInt16(", "async function __orig_recognizeInt16(")
  // test-only: pass the request id straight through (the real code wraps it in a Float32Array)
  .replaceAll("downsamplePCM16(new Float32Array(m.pcm), m.sampleRate)", "m.pcm");

// ---- virtual clock -----------------------------------------------------------
let now = 0;
const timers = [];
const at = (t, fn) => timers.push({ t, fn, seq: timers.length });
async function runClock() {
  while (timers.length) {
    timers.sort((a, b) => a.t - b.t || a.seq - b.seq);
    const { t, fn } = timers.shift();
    now = t;
    fn();
    for (let i = 0; i < 20; i++) await Promise.resolve();   // drain microtasks
  }
}

// ---- scenario: hypothesis per request id --------------------------------------
const HYP = {
  "u1p1": "今天天气很好。",
  "u1p2": "今天天气很好。我们去公园",
  "u1p3": "今天天气很好。我们去公园吧。明天",
  "u1p4": "今天天气很好。我们去公园吧。明天见",
  "u1F":  "今天天气很好。我们去公园吧。明天见。",
  "u2p1": "好的。我们",
  "u2p2": "好的。我们出发吧",
  "u2F":  "好的。我们出发吧。",
};
const LAT = { partial: 200, final: 900 };   // a long final decodes slowly
const commits = [];

const harness = `
  let pipInterim = "";
  let liveRunning = true;
  const BOOT = { config: {} };
  const _label = (a) => a;
  const setLiveStatus = () => {};
  const updatePipCaption = () => {};
  const liveListenMsg = () => "";
  const downsamplePCM16 = (x) => x;
  const commitLiveSentence = (s) => { s = (s || "").trim(); if (s && WORDLIKE.test(s)) __commits.push([__now(), s]); };
  const liveFlushHold = () => Promise.resolve();   // translation side: not under test here
  async function recognizeInt16(id, final) {
    return new Promise((res) => __at(__now() + (final ? __LAT.final : __LAT.partial),
      () => res({ source: __HYP[id], detected: "zh" })));
  }
  ${region}
  return { onVadMessage };
`;
const mod = new Function("__commits", "__now", "__at", "__LAT", "__HYP", harness)(
  commits, () => now, at, LAT, HYP);
const send = (m) => mod.onVadMessage({ data: m });
const partial = (id) => send({ type: "partial", pcm: id, sampleRate: 16000 });
const segment = (id) => send({ type: "segment", pcm: id, sampleRate: 16000 });

// utterance 1: continuous speech, partials every 360 ms, then it ends
at(0, () => { send({ type: "speechstart" }); partial("u1p1"); });
at(360, () => partial("u1p2"));
at(720, () => partial("u1p3"));
at(1080, () => partial("u1p4"));
at(1300, () => segment("u1F"));
// utterance 2 starts 100 ms later — before u1's slow final has returned
at(1400, () => send({ type: "speechstart" }));
at(1500, () => partial("u2p1"));
at(1860, () => partial("u2p2"));
at(2100, () => segment("u2F"));

await runClock();
const texts = commits.map(([, s]) => s);
const dup = texts.filter((s, i) => texts.indexOf(s) !== i);
for (const [t, s] of commits) console.log(`  ${String(t).padStart(5)}ms  ${s}`);
console.log(`commits=${texts.length}  duplicated=${JSON.stringify(dup)}`);
const expect = ["今天天气很好。", "我们去公园吧。", "明天见。", "好的。", "我们出发吧。"];
console.log(JSON.stringify(texts) === JSON.stringify(expect) ? "RESULT: CLEAN" : "RESULT: WRONG");
