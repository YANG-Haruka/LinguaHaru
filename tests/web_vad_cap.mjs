// Run the REAL vad-worklet.js on synthetic audio:
//  - continuous speech hitting the length cap must be cut IN a quiet dip (or,
//    with none, after waiting up to 1.5x the cap), keep capturing, and lose or
//    repeat no audio across the seam;
//  - 'silence' (held lines may translate) comes only once a pause reaches the
//    full hang, never for a short final-mode cut the speaker talks through.
//
// usage: node tests/web_vad_cap.mjs webapp/static/vad-worklet.js  (driven by tests/test_live_stream_race.py)
import fs from "node:fs";
import vm from "node:vm";

const SR = 16000, FRAME = 128, CAP_MS = 3000;
const code = fs.readFileSync(process.argv[2], "utf8");

function make(opts) {
  const posted = [];
  const ctx = { sampleRate: SR, currentTime: 0, Float32Array, Float64Array, Math,
                registerProcessor: (_n, cls) => { ctx.VAD = cls; } };
  ctx.AudioWorkletProcessor = class { constructor() { this.port = { postMessage: (m) => posted.push(m) }; } };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  const p = new ctx.VAD({ processorOptions: { prerollMs: 500, onMs: 90, hangMs: 900, minSegMs: 280,
                                              maxSegMs: 15000, onAbs: 0.006, offAbs: 0.004, ...opts } });
  p.port.onmessage({ data: { type: "mode", mode: "open" } });
  const feed = (sec, amp) => {
    for (let f = 0; f < sec * SR / FRAME; f++) {
      p.process([[new Float32Array(FRAME).map(() => amp * (Math.random() * 2 - 1))]]);
      ctx.currentTime += FRAME / SR;
    }
  };
  const count = (type) => posted.filter((m) => m.type === type).length;
  return { feed, count };
}

function run(dip, seconds) {
  const posted = [];
  const ctx = { sampleRate: SR, currentTime: 0, Float32Array, Float64Array, Math,
                registerProcessor: (_n, cls) => { ctx.VAD = cls; } };
  ctx.AudioWorkletProcessor = class { constructor() { this.port = { postMessage: (m) => posted.push(m) }; } };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  const p = new ctx.VAD({ processorOptions: { prerollMs: 500, onMs: 90, hangMs: 900, minSegMs: 280,
                                              maxSegMs: CAP_MS, onAbs: 0.006, offAbs: 0.004 } });
  p.port.onmessage({ data: { type: "mode", mode: "open" } });
  let seed = 1;
  const rand = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648) * 2 - 1;
  const fed = [];
  for (let f = 0; f < seconds * SR / FRAME; f++) {
    const buf = new Float32Array(FRAME);
    for (let i = 0; i < FRAME; i++) {
      const n = f * FRAME + i;
      buf[i] = (dip && n >= dip[0] && n < dip[1] ? 0.001 : 0.3) * rand();
      fed.push(buf[i]);
    }
    p.process([[buf]]);
    ctx.currentTime += FRAME / SR;
  }
  const segs = posted.filter((m) => m.type === "segment");
  const head = segs.length ? new Float32Array(segs[0].pcm) : new Float32Array(0);
  const fails = [];
  if (segs.length !== 1) fails.push(`expected 1 segment, got ${segs.length}`);
  if (!p.capturing) fails.push("stopped capturing (speech goes on)");
  const joined = [...head, ...p._snapshotSeg()];
  if (joined.length !== fed.length || joined.some((v, i) => Math.fround(fed[i]) !== v))
    fails.push(`seam lost/repeated audio: ${joined.length} vs ${fed.length} samples`);
  return { fails, cut: head.length };
}

const fails = [];
// 1) a 100 ms pause 2.0 s in: the cut lands inside it.
const a = run([2.0 * SR, 2.1 * SR], 4.0);
fails.push(...a.fails.map((f) => "dip: " + f));
if (a.cut < 2.0 * SR || a.cut > 2.1 * SR) fails.push(`dip: cut at ${a.cut}, not in the pause`);
// 2) no pause at all: it waits for one up to 1.5x the cap, then cuts anyway.
const b = run(null, 5.0);
fails.push(...b.fails.map((f) => "no dip: " + f));
if (b.cut < (CAP_MS / 1000) * SR) fails.push(`no dip: cut at ${b.cut}, before the cap`);
console.log(fails.length ? "RESULT: FAIL\n" + fails.join("\n") : "RESULT: OK");
