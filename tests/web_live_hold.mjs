// Run the REAL web translation-hold code (extracted verbatim from app.js; twin
// of Qt _queue_translation / _age_tr_hold / _split_clause_tail): a line that
// stops mid-phrase — including the STT's own "。" after a particle at a segment
// cut — is held and translated with its continuation; a clause end translates
// at once; a held line waits for ONE more segment (no clock), or goes once the
// pause reached the full hang, checked inside the commit chain.
// Further down, the parts of a long session around it: the stamps the two panes
// are paired by, a chain link that throws, the recognition lane (timeout, bounded
// queue), the panes as one node per line, the neural VAD's length cap.
//
// usage: node tests/web_live_hold.mjs webapp/static/app.js  (driven by tests/test_live_stream_race.py)
import fs from "node:fs";
import vm from "node:vm";

const src = fs.readFileSync(process.argv[2], "utf8");
const start = src.indexOf("let liveCommitChain = Promise.resolve();");
const end = src.indexOf("async function _translateLiveLine(");
if (start < 0 || end < 0) throw new Error("region markers not found");
const sent = [], entries = [], apiCalls = [], chainErrors = [];
let lastEntry = null, lastFid = 0, interim = "";
let gate = null;   // an array: final translations wait until their resolver in it is called
let lastAlt = "";  // the second opinion (the preview model's text) sent with the last final translation
let clock = "00:00:00", lastTs = "";   // the wall clock; the stamp of the last final translation
let boom = false;  // the next final translation throws
const provEl = { textContent: "" };
const ctx = { WORDLIKE: /[0-9A-Za-z぀-ヿ㐀-鿿]/, liveTimeStamp: () => clock, liveGen: 0,
              console: { error: (...a) => chainErrors.push(a) },
              // a source line opens its floating-caption pair
              appendLive: (id, text) => {
                if (id !== "live-input") return null;
                const e = { src: text.replace(/^\[[^\]]*\]\s*/, "").trim(), dst: "", ts: text.slice(1, 9) };
                entries.push(e); return e;
              },
              _translateLiveLine: async (ts, s, entry, fid, alt) => {
                if (boom) { boom = false; throw new Error("boom"); }
                sent.push(s); lastEntry = entry; lastFid = fid; lastAlt = alt; lastTs = ts;
                if (gate) await new Promise((res) => gate.push(res));
              },
              api: (_url, opts) => new Promise((res) => apiCalls.push({ ...JSON.parse(opts.body), res })),
              $: (id) => (id === "live-output-prov" ? provEl : { value: "zh" }),
              liveInterimText: () => interim,   // what is still being said (the preview lane)
              liveGlossary: () => "meeting",          // ... with the glossary picked on the page
              updatePipCaption: () => {}, liveContext: () => "", liveLastDetected: "ja", liveSessionTokens: 0,
              liveRunning: true, liveQuiet: false, liveRecogBusy: false, liveRecogQueue: [] };
vm.createContext(ctx);
vm.runInContext(src.slice(start, end) +
  "\nthis.__t = { commitLiveSentence, liveFlushHold, liveAgeHold, liveRequestProvisional, splitClauseTail," +
  " lineEnd, finalShown: _finalShown, setFinal: (v) => { liveFinalMode = livePreviewLane = v; } };", ctx);
const { commitLiveSentence, liveFlushHold, liveAgeHold, liveRequestProvisional, splitClauseTail,
        lineEnd, finalShown, setFinal } = ctx.__t;
const settle = async () => { for (let i = 0; i < 50; i++) await Promise.resolve(); };
const fails = [];
const expect = (what, want) => {
  if (JSON.stringify(sent) !== JSON.stringify(want)) fails.push(`${what}: ${JSON.stringify(sent)}`);
  sent.length = 0;
};
const same = (what, got, want) => {
  if (JSON.stringify(got) !== JSON.stringify(want)) fails.push(`${what}: ${JSON.stringify(got)}`);
};

commitLiveSentence("ちなみにそこってあの。");          // the STT's "。" after a filler
commitLiveSentence("前にいただいた資料の中で。");      // ... and after a particle
await settle();
expect("mid-phrase lines are held", []);
commitLiveSentence("十月十日って書いてありました。");
await settle();
expect("held lines go with the sentence that completes them",
       ["ちなみにそこってあの。前にいただいた資料の中で。十月十日って書いてありました。"]);
commitLiveSentence("それで大丈夫です。");
await settle();
expect("a real sentence end translates alone", ["それで大丈夫です。"]);

commitLiveSentence("では次の議題に");
liveFlushHold("quiet");                 // a short pause: not quiet yet
await settle();
expect("not before the pause reached the full hang", []);
ctx.liveQuiet = true; ctx.liveRecogBusy = true;
liveFlushHold("quiet");
await settle();
expect("not while a recognition result is pending", []);
ctx.liveRecogBusy = false;
commitLiveSentence("ついての");           // still queued on the chain when ...
liveFlushHold("quiet");                  // ... the flush is asked for: must include it
await settle();
expect("the flush sees lines still queued on the commit chain", ["では次の議題についての"]);

ctx.liveQuiet = false;                   // a fast speaker: never a full pause
commitLiveSentence("要件はもう決まっていますけど、えっと。");
await settle();
expect("a trailing filler is not an unfinished line", ["要件はもう決まっていますけど、えっと。"]);
commitLiveSentence("そちらで対応していただいて。");
await settle();
expect("a clause end (te-form) translates at once", ["そちらで対応していただいて。"]);

// Stream mode (SenseVoice) has no segments: a held line waits for at most two more lines.
commitLiveSentence("その秘密保持のレベルと");
commitLiveSentence("ノースリンクスとの");
await settle();
expect("stream mode: two held lines still wait", []);
commitLiveSentence("山川自動車からの");
await settle();
expect("stream mode: the third unfinished line sends them",
       ["その秘密保持のレベルとノースリンクスとの山川自動車からの"]);

// Final mode (Qwen3-ASR): no clock — the continuation is in the next segment,
// however long it takes.
setFinal(true);
commitLiveSentence("山川自動車からコピーに対してのその秘密保持のレベルと、ノースリンクスとの");
liveAgeHold();                           // end of its own segment
await settle();
expect("held through the end of its own segment", []);
commitLiveSentence("機密保持NDAを巻き直しています。");
liveAgeHold();
await settle();
expect("the next segment completes it: one translation",
       ["山川自動車からコピーに対してのその秘密保持のレベルと、ノースリンクスとの機密保持NDAを巻き直しています。"]);
commitLiveSentence("その秘密保持のレベルと");
liveAgeHold();
commitLiveSentence("ノースリンクスとの");
await settle();
expect("still held while the next segment's lines come in", []);
liveAgeHold();
await settle();
expect("a run-on with no clause end in two segments goes after the second",
       ["その秘密保持のレベルとノースリンクスとの"]);
commitLiveSentence("その秘密保持のレベルと");
commitLiveSentence("ノースリンクスとの");
commitLiveSentence("山川自動車からの");          // three unfinished lines of ONE segment
await settle();
expect("final mode: no line limit within a segment", []);
liveFlushHold();
await settle();
expect("stop flushes the rest", ["その秘密保持のレベルとノースリンクスとの山川自動車からの"]);

// One live translation of everything pending — held lines + what is still being
// said — refreshed as speech goes on, one request at a time; the whole
// sentence's translation (paired with its last line) replaces it; a provisional
// that comes back after the lines were sent whole is stale. Twin of Qt
// test_one_live_translation_of_everything_pending.
interim = "機密保持";
commitLiveSentence("山川自動車からコピーに対しての");
liveRequestProvisional();
await settle();
same("asked for held + what is being said", apiCalls.map((c) => c.source), ["山川自動車からコピーに対しての機密保持"]);
interim = "機密保持NDA";                       // a few more chars, and one is out anyway
liveRequestProvisional();
interim = "機密保持NDAを巻き直しています";        // more speech while it's out
liveRequestProvisional();
await settle();
same("one request at a time", apiCalls.length, 1);
apiCalls[0].res({ translated: "来自山川汽车……", tokens: 7 });
await settle();
same("shown on the live line, then asked again for the newer text",
     [provEl.textContent, ctx.pipInterimDst, apiCalls.length, apiCalls[1] && apiCalls[1].source],
     ["来自山川汽车……", "来自山川汽车……", 2, "山川自動車からコピーに対しての機密保持NDAを巻き直しています"]);
interim = "";                                  // its final recognition came in:
commitLiveSentence("機密保持NDAを巻き直しています。");
await settle();
expect("the whole sentence is translated once", ["山川自動車からコピーに対しての機密保持NDAを巻き直しています。"]);
same("paired with its last line", lastEntry === entries[entries.length - 1], true);
apiCalls[1].res({ translated: "late", tokens: 5 });
await settle();
same("a late provisional is dropped; the shown one stays until the final", provEl.textContent, "来自山川汽车……");
finalShown(lastFid);
await settle();
same("cleared once the whole translation is shown", [provEl.textContent, ctx.pipInterimDst], ["", ""]);
same("provisional calls are counted", ctx.liveSessionTokens, 12);
same("the glossary choice rides on the request", apiCalls[0].glossary, "meeting");

// A sentence that goes straight to translation (nothing held) drops the
// provisional request still out for its preview: the late reply used to be
// shown — a second, dim translation under the final one for the whole pause.
// Twin of Qt test_a_sentence_sent_at_once_drops_the_provisional_request_still_out.
apiCalls.length = 0; provEl.textContent = "";
interim = "それで大丈夫です";
liveRequestProvisional();
await settle();
same("asked for the preview", apiCalls.map((c) => c.source), ["それで大丈夫です"]);
interim = "";                                   // the VAD cut it; its final is a whole sentence
commitLiveSentence("それで大丈夫です。");
liveRequestProvisional();
await settle();
expect("sent at once", ["それで大丈夫です。"]);
apiCalls[0].res({ translated: "这样就可以了", tokens: 3 });
await settle();
same("the late provisional reply is not shown", [provEl.textContent, ctx.pipInterimDst], ["", ""]);

// A final translation the chain is awaiting must not hold back the provisional
// one for what is said meanwhile (it did: no refresh for ~1 s per final).
apiCalls.length = 0; gate = [];
commitLiveSentence("要件はもう決まっています。");
await settle();
expect("the final translation is out", ["要件はもう決まっています。"]);
interim = "来週の会議で決めましょう";
liveRequestProvisional();
await settle();
same("asked while the final translation is still out", apiCalls.map((c) => c.source), ["来週の会議で決めましょう"]);
// ... but behind a commit that has not started yet it waits: only then is the hold current
commitLiveSentence("その秘密保持のレベルと");          // queued behind the awaited translation
interim = "来週の会議で決めましょう。それから";
liveRequestProvisional();
await settle();
same("not before the queued commit ran", apiCalls.length, 1);
apiCalls[0].res({ translated: "下周的会议上决定吧", tokens: 3 });
gate.shift()(); gate = null;
await settle();
same("then with the line it holds", apiCalls[apiCalls.length - 1].source,
     "その秘密保持のレベルと来週の会議で決めましょう。それから");
interim = "";
liveFlushHold();
await settle();
sent.length = 0;

// What the preview model heard goes with the lines of its segment; a sentence
// put together from two segments gets both, once each. Twin of Qt
// test_the_preview_models_text_goes_with_the_lines_of_its_segment.
commitLiveSentence("それで大丈夫です。", "それで大丈夫です 資料は昨日送りました");
await settle();
expect("a whole sentence goes at once", ["それで大丈夫です。"]);
same("with its segment's second opinion", lastAlt, "それで大丈夫です 資料は昨日送りました");
commitLiveSentence("その秘密ほじのレベルと", "その秘密保持のレベルと");
commitLiveSentence("ノースリンクスとの機密保持を巻き直しています。", "ノースリンクスとの機密保持を巻き直しています");
await settle();
expect("a sentence from two segments", ["その秘密ほじのレベルとノースリンクスとの機密保持を巻き直しています。"]);
same("gets both second opinions", lastAlt, "その秘密保持のレベルとノースリンクスとの機密保持を巻き直しています");
commitLiveSentence("はい、わかりました。");
await settle();
expect("no preview model", ["はい、わかりました。"]);
same("no second opinion", lastAlt, "");
setFinal(false);

// A segment cut after a clause end: the clause goes now, only the tail waits.
same("split at the last clause end", splitClauseTail(["はい。", "お知らせがあるかもしれないですけど、えっと山川自動車からの。"]),
     ["はい。", "お知らせがあるかもしれないですけど、", "えっと山川自動車からの。"]);
same("a finished line is not split", splitClauseTail(["決まっています、はい。"]), ["決まっています、はい。"]);
same("no clause end inside: not split", splitClauseTail(["山川自動車からコピーに対しての、"]),
     ["山川自動車からコピーに対しての、"]);
same("line ends", ["それで大丈夫です。", "今日追加で来たんですが。", "資料の中で。", "一応ご留意。", "えっとあの。",
                   "the meeting starts at the.", "We can decide it later.", "我觉得这个方案", "那我们明天再聊吧。"].map(lineEnd),
     ["S", "C", "M", "N", "F", "M", "S", "M", "S"]);

// A translation carries the stamp of the LAST source line it covers: the saved
// transcript and the new-terms report pair the two panes by it. Sent at a pause
// (or after the next segment) it was stamped with the time it was SENT — a
// stamp no source line has. Twin of Qt _flush_tr_hold (the last line's row).
clock = "10:00:01"; commitLiveSentence("では次の議題に");
await settle();
clock = "10:00:05"; ctx.liveQuiet = true;       // the pause reached the full hang, four seconds on
liveFlushHold("quiet");
await settle();
expect("sent at the pause", ["では次の議題に"]);
same("... with its source line's stamp", lastTs, "10:00:01");
ctx.liveQuiet = false;
setFinal(true);
clock = "10:00:10"; commitLiveSentence("その秘密保持のレベルと"); liveAgeHold();
await settle();
clock = "10:00:12"; commitLiveSentence("ノースリンクスとの");
await settle();
clock = "10:00:20"; liveAgeHold();              // the second segment's result, eight seconds on
await settle();
expect("sent after the second segment", ["その秘密保持のレベルとノースリンクスとの"]);
same("... with its last line's stamp", [lastTs, lastEntry.ts], ["10:00:12", "10:00:12"]);
clock = "10:00:30"; commitLiveSentence("山川自動車からの");
await settle();
clock = "10:00:31"; commitLiveSentence("機密保持NDAを巻き直しています。");
await settle();
sent.length = 0;
same("completed by a later line: that line's stamp", lastTs, "10:00:31");
setFinal(false);

// One link that throws must not end the chain: it stayed rejected for good, and
// no line was shown or translated again until the page was reloaded.
boom = true;
commitLiveSentence("それで大丈夫です。");
await settle();
commitLiveSentence("はい、わかりました。");
await settle();
expect("the chain goes on after a link that threw", ["はい、わかりました。"]);
same("... and the failure is logged", chainErrors.length, 1);
// What a stopped session left queued is not run in the next one (startLocal
// starts a new chain and a new liveGen).
gate = [];
commitLiveSentence("要件はもう決まっています。");
await settle();
const shownBefore = entries.length;
commitLiveSentence("それで大丈夫です。");           // queued behind that translation
ctx.liveGen++;                                        // stopped and started again
gate.shift()(); gate = null;
await settle();
expect("only what was already out", ["要件はもう決まっています。"]);
same("a line of the session before is not shown in this one", entries.length, shownBefore);

// ---- the recognition lane (dispatchRecognize / recognizeInt16 / runFinal) ----
// A request that never returns must not keep the one lane busy for good, and the
// finals queued behind slow recognition must not hold their audio without limit.
{
  const a = src.indexOf('let liveCommittedText = "";');
  const b = src.indexOf("// Finalize one sentence: show the source line now");
  if (a < 0 || b < 0) throw new Error("region markers not found (recognition lane)");
  const timers = [], calls = [], toasts = [], commits = [];
  const lane = { pipInterim: "", liveRunning: true, BOOT: { config: {} }, _label: (k) => k,
                 setLiveStatus() {}, updatePipCaption() {}, liveListenMsg: () => "",
                 commitLiveSentence: (s) => commits.push(s), liveFlushHold: () => Promise.resolve(),
                 liveAgeHold() {}, liveRequestProvisional() {}, liveFinalMode: false, livePreviewLane: false,
                 int16ToB64: (pcm) => String(pcm[0]), AbortController,
                 _joinHeld: (parts) => parts.map((p) => p.src).join(""),
                 setTimeout: (fn, ms) => timers.push({ fn, ms }), clearTimeout() {},
                 toast: (m) => toasts.push(m),
                 api: (_url, opts) => new Promise((res, rej) => {
                   calls.push({ ...JSON.parse(opts.body), res });
                   if (opts.signal) opts.signal.addEventListener("abort", () => rej(new Error("aborted")));
                 }) };
  vm.createContext(lane);
  vm.runInContext(src.slice(a, b) + "\nthis.__r = { dispatchRecognize, finalizeUtterance," +
    " queue: () => liveRecogQueue, pending: () => livePendingPreview };", lane);
  const { dispatchRecognize, finalizeUtterance, queue, pending } = lane.__r;
  const pcm = (id) => { const p = new Int16Array(4); p[0] = id; return p; };
  const finalsAsked = () => calls.filter((c) => c.final).map((c) => c.audio_b64);

  dispatchRecognize(pcm(1), true);                  // this request never returns
  dispatchRecognize(pcm(2), true);
  await settle();
  same("one recognition in flight, the next final queued", finalsAsked(), ["1"]);
  same("the request has a 60 s timeout", timers.map((t) => t.ms), [60000]);
  if (timers[0]) timers[0].fn();                    // 60 s later
  await settle();
  same("the lane is free again: the queued final goes out", finalsAsked(), ["1", "2"]);
  calls[calls.length - 1].res({ source: "はい、わかりました。", detected: "ja" });
  await settle();
  same("... and is committed", commits, ["はい、わかりました。"]);

  dispatchRecognize(pcm(3), true);                  // a slow one; 20 more segments end meanwhile
  for (let id = 10; id < 30; id++) dispatchRecognize(pcm(id), true);
  same("only the newest 12 queued finals keep their audio",
       queue().map((q) => (q.int16.length ? q.int16[0] : 0)),
       [0, 0, 0, 0, 0, 0, 0, 0, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29]);
  same("the user is told once", toasts.length, 1);
  calls[calls.length - 1].res({ source: "" });
  await settle(); await settle();                   // eight emptied finals, one after the other
  same("the emptied ones pass without a request", finalsAsked().slice(3), ["18"]);
  while (calls.some((c) => !c.done)) {              // let the rest through
    for (const c of calls) if (!c.done) { c.done = true; c.res({ source: "" }); }
    await settle();
  }

  // With a preview lane every cut segment has a pending preview until its final
  // is in: an emptied final must still take its own away, in order.
  lane.livePreviewLane = true;
  calls.length = 0;
  for (let id = 40; id < 55; id++) finalizeUtterance(pcm(id));   // 1 in flight + 14 queued
  same("a pending preview per segment", pending().length, 15);
  while (calls.some((c) => !c.done)) {
    for (const c of calls) if (!c.done) { c.done = true; c.res({ source: "" }); }
    await settle();
  }
  same("the two oldest queued finals were not recognized", finalsAsked().length, 13);
  same("no pending preview is left behind", pending().length, 0);
  same("... and the user was not told again", toasts.length, 1);
}

// ---- the two panes (appendLive / stream mode) --------------------------------
// One node per line: a pane was ONE text node rewritten whole for every line
// and every streamed token — at 20,000 lines the whole transcript was laid out
// again each time. What reads the panes (el.textContent) must see the same text.
{
  let a = src.indexOf("function liveAddLine(");
  if (a < 0) a = src.indexOf("function appendLive(");
  const b = src.indexOf("// --- Live input hot-switch");
  const c = src.indexOf("async function _translateLiveLine("), d = src.indexOf("function downsamplePCM16(");
  if (a < 0 || b < 0 || c < 0 || d < 0) throw new Error("region markers not found (panes)");
  const pane = () => {
    const rows = [];
    return { rows, rewrites: 0, scrollTop: 0, scrollHeight: 0,
             appendChild(n) { rows.push(n); return n; },
             get textContent() { return rows.map((r) => r.textContent).join(""); },
             set textContent(v) { this.rewrites++; rows.length = 0; if (v) rows.push({ textContent: v }); } };
  };
  const els = { "live-input": pane(), "live-output": pane(), "live-target": { value: "zh" } };
  let chunks = [];
  const page = { $: (id) => els[id], document: { createElement: () => ({ textContent: "" }) },
                 pipEntries: [], PIP_MAX: 12, updatePipCaption() {}, _finalShown() {},
                 BOOT: { config: {} }, liveContext: () => "", liveCtxHistory: [], liveLastDetected: "ja",
                 liveGlossary: () => "", liveSessionTokens: 0, TextDecoder,
                 api: async () => ({ translated: "好的。", tokens: 1 }),
                 fetch: async () => ({ body: { getReader: () => ({
                   read: async () => (chunks.length ? { done: false, value: chunks.shift() } : { done: true }) }) } }) };
  vm.createContext(page);
  vm.runInContext(src.slice(a, b) + src.slice(c, d) + "\nthis.__p = { appendLive, _translateLiveLine };", page);
  const { appendLive, _translateLiveLine } = page.__p;
  const inp = els["live-input"], out = els["live-output"];

  const e1 = appendLive("live-input", "[10:00:01] はい。\n");
  await _translateLiveLine("10:00:01", "はい。", e1);
  same("the panes read as before", [inp.textContent, out.textContent],
       ["[10:00:01] はい。\n", "[10:00:01] 好的。\n"]);
  same("the floating-caption pair comes from the line itself", [e1.src, e1.dst], ["はい。", "好的。"]);
  // stream mode: the tokens grow their own line only
  page.BOOT.config.live_stream_translation = true;
  const enc = new TextEncoder();
  chunks = ['data: "明"\n\n', 'data: "明天见"\n\n', 'data: {"__usage__": 5}\n\ndata: [DONE]\n\n'].map((s) => enc.encode(s));
  const first = out.rows[0];
  const e2 = appendLive("live-input", "[10:00:02] また明日。\n");
  await _translateLiveLine("10:00:02", "また明日。", e2);
  same("stream mode: the same text", out.textContent, "[10:00:01] 好的。\n[10:00:02] 明天见\n");
  same("... its pair, its tokens", [e2.dst, page.liveSessionTokens], ["明天见", 6]);
  same("... and the line before it is not touched", [out.rows[0] === first, first.textContent, out.rows.length],
       [true, "[10:00:01] 好的。\n", 2]);
  for (let i = 0; i < 20000; i++) appendLive("live-input", `[10:00:03] line ${i}\n`);
  same("20,000 lines: one node each", [inp.rows.length, inp.textContent.split("\n").filter(Boolean).length], [20002, 20002]);
  same("a pane is never rewritten whole", [inp.rewrites, out.rewrites], [0, 0]);
}

// ---- the neural VAD's length cap (web_vad=silero) -----------------------------
// Under continuous music the utterance never ended: the buffer only grew and
// all of it was posted every 360 ms. Twin of the worklet's cap (web_vad_cap.mjs).
{
  const a = src.indexOf("function floatToInt16("), b = src.indexOf("function stopLocal()");
  const m = src.indexOf("function liveMaxSegMs("), n = src.indexOf("async function startWorkletVad(");
  if (a < 0 || b < 0) throw new Error("region markers not found (silero)");
  let opts = null, now = 0;
  const partials = [], finals = [], flushes = [];
  const lib = { frameProcessor: { audioBuffer: [1, 2, 3] }, receive() {}, start() {} };
  const page = { loadVadLibs: async () => {}, BOOT: { config: {} }, liveCtx: {}, liveSrc: {},
                 window: { vad: { AudioNodeVAD: { new: async (_ctx, o) => { opts = o; return lib; } } } },
                 performance: { now: () => now }, setLiveStatus() {}, _label: (k) => k,
                 streamPartial: (pcm) => partials.push(pcm.length), finalizeUtterance: (pcm) => finals.push(pcm.length),
                 liveFlushHold: (when) => flushes.push(when),
                 liveSilero: null, liveSileroBuf: [], liveSileroLast: 0, liveSileroSpeaking: false,
                 liveSileroCut: false, liveQuiet: false };
  vm.createContext(page);
  vm.runInContext((m < 0 ? "" : src.slice(m, n)) + src.slice(a, b) + "\nthis.__s = { startSileroVad };", page);
  await page.__s.startSileroVad(true);                 // final mode: 10 s = 160,000 samples
  const FRAME = 1536, CAP = 160000;
  const feed = (frames, amp) => {
    for (let i = 0; i < frames; i++) { now += 96; opts.onFrameProcessed({}, new Float32Array(FRAME).fill(amp)); }
  };
  const buffered = () => page.liveSileroBuf.reduce((s, c) => s + c.length, 0);
  opts.onSpeechStart();
  feed(400, 0.3);                                      // 38 s of steady sound, no pause in it
  same("steady sound is cut at 1.5x the cap", [finals.length, finals.every((s) => s <= 1.5 * CAP + FRAME)], [2, true]);
  same("no partial is longer than that", Math.max(...partials) <= 1.5 * CAP + FRAME, true);
  same("nothing dropped, nothing heard twice", finals[0] + finals[1] + buffered(), 400 * FRAME);
  same("the library's own copy of the utterance is let go", lib.frameProcessor.audioBuffer.length, 0);
  const rest = buffered();
  opts.onSpeechEnd(new Float32Array(999999));          // the library's audio: from the speech start
  same("after a cut the end is what came since, not the library's audio", [finals.length, finals[2], buffered()],
       [3, rest, 0]);

  finals.length = 0;
  opts.onSpeechStart();
  feed(100, 0.3); feed(3, 0.001); feed(2, 0.3);       // a 0.3 s pause just before the cap is reached
  same("past the cap: cut inside the pause", [finals.length, finals[0] > 100 * FRAME, finals[0] < 103 * FRAME],
       [1, true, true]);
  opts.onSpeechEnd(new Float32Array(999999));

  // A blip the library drops (under 3 speech frames) ends without onSpeechEnd.
  finals.length = 0; partials.length = 0;
  opts.onSpeechStart();
  feed(2, 0.3);
  (opts.onVADMisfire || (() => {}))();
  feed(200, 0.0001);                                   // 19 s of silence after it
  same("a dropped blip: nothing recognized, the silence after it not buffered",
       [finals.length, partials.length, buffered(), page.liveQuiet, flushes], [0, 0, 0, true, ["quiet"]]);
}
console.log(fails.length ? "RESULT: FAIL\n" + fails.join("\n") : "RESULT: OK");
