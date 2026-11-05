// Run the REAL web translation-hold code (extracted verbatim from app.js; twin
// of Qt _queue_translation / _age_tr_hold / _split_clause_tail): a line that
// stops mid-phrase — including the STT's own "。" after a particle at a segment
// cut — is held and translated with its continuation; a clause end translates
// at once; a held line waits for ONE more segment (no clock), or goes once the
// pause reached the full hang, checked inside the commit chain.
//
// usage: node tests/web_live_hold.mjs webapp/static/app.js  (driven by tests/test_live_stream_race.py)
import fs from "node:fs";
import vm from "node:vm";

const src = fs.readFileSync(process.argv[2], "utf8");
const start = src.indexOf("let liveCommitChain = Promise.resolve();");
const end = src.indexOf("async function _translateLiveLine(");
if (start < 0 || end < 0) throw new Error("region markers not found");
const sent = [], entries = [], apiCalls = [];
let lastEntry = null, lastProv = null;
const provEl = { textContent: "" };
const ctx = { WORDLIKE: /[0-9A-Za-z぀-ヿ㐀-鿿]/, liveTimeStamp: () => "00:00:00",
              // a source line opens its floating-caption pair
              appendLive: (id, text) => {
                if (id !== "live-input") return null;
                const e = { src: text.replace(/^\[[^\]]*\]\s*/, "").trim(), dst: "" };
                entries.push(e); return e;
              },
              _translateLiveLine: async (_ts, s, entry, prov) => { sent.push(s); lastEntry = entry; lastProv = prov; },
              api: (_url, opts) => new Promise((res) => apiCalls.push({ source: JSON.parse(opts.body).source, res })),
              $: (id) => (id === "live-output-prov" ? provEl : { value: "zh" }),
              updatePipCaption: () => {}, liveContext: () => "", liveLastDetected: "ja", liveSessionTokens: 0,
              liveQuiet: false, liveRecogBusy: false, liveRecogQueue: [] };
vm.createContext(ctx);
vm.runInContext(src.slice(start, end) +
  "\nthis.__t = { commitLiveSentence, liveFlushHold, liveAgeHold, liveRequestProvisional, splitClauseTail," +
  " lineEnd, setFinal: (v) => { liveFinalMode = v; } };", ctx);
const { commitLiveSentence, liveFlushHold, liveAgeHold, liveRequestProvisional, splitClauseTail,
        lineEnd, setFinal } = ctx.__t;
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
commitLiveSentence("ネクストムーブとの");
await settle();
expect("stream mode: two held lines still wait", []);
commitLiveSentence("三菱自動車からの");
await settle();
expect("stream mode: the third unfinished line sends them",
       ["その秘密保持のレベルとネクストムーブとの三菱自動車からの"]);

// Final mode (Qwen3-ASR): no clock — the continuation is in the next segment,
// however long it takes.
setFinal(true);
commitLiveSentence("三菱自動車からコピーに対してのその秘密保持のレベルと、ネクストムーブとの");
liveAgeHold();                           // end of its own segment
await settle();
expect("held through the end of its own segment", []);
commitLiveSentence("機密保持NDAを巻き直しています。");
liveAgeHold();
await settle();
expect("the next segment completes it: one translation",
       ["三菱自動車からコピーに対してのその秘密保持のレベルと、ネクストムーブとの機密保持NDAを巻き直しています。"]);
commitLiveSentence("その秘密保持のレベルと");
liveAgeHold();
commitLiveSentence("ネクストムーブとの");
await settle();
expect("still held while the next segment's lines come in", []);
liveAgeHold();
await settle();
expect("a run-on with no clause end in two segments goes after the second",
       ["その秘密保持のレベルとネクストムーブとの"]);
commitLiveSentence("その秘密保持のレベルと");
commitLiveSentence("ネクストムーブとの");
commitLiveSentence("三菱自動車からの");          // three unfinished lines of ONE segment
await settle();
expect("final mode: no line limit within a segment", []);
liveFlushHold();
await settle();
expect("stop flushes the rest", ["その秘密保持のレベルとネクストムーブとの三菱自動車からの"]);

// Two-layer captions: held text gets a provisional translation at once; the
// whole sentence's translation pairs with the completing line and hands over
// the provisional pair (to clear); a provisional that comes back late is stale.
commitLiveSentence("三菱自動車からの");
liveRequestProvisional();
await settle();
same("provisional asked for the held text", apiCalls.map((c) => c.source), ["三菱自動車からの"]);
liveRequestProvisional();
await settle();
same("once per held text", apiCalls.length, 1);
apiCalls[0].res({ translated: "来自三菱汽车的", tokens: 7 });
await settle();
const heldEntry = entries[entries.length - 1];
same("shown dim on the held line's pair and under the output",
     [heldEntry.dst, heldEntry.prov, provEl.textContent], ["来自三菱汽车的", true, "… 来自三菱汽车的"]);
commitLiveSentence("機密保持NDAを巻き直しています。");
await settle();
expect("the whole sentence is translated once", ["三菱自動車からの機密保持NDAを巻き直しています。"]);
same("paired with the completing line; the provisional pair handed over",
     [lastEntry === entries[entries.length - 1], lastProv === heldEntry], [true, true]);
provEl.textContent = "";
commitLiveSentence("その秘密保持のレベルと");
liveRequestProvisional();
await settle();
commitLiveSentence("ということです。");
await settle();
expect("completed before its provisional came back", ["その秘密保持のレベルとということです。"]);
apiCalls[1].res({ translated: "那个保密级别", tokens: 5 });
await settle();
same("a late provisional is dropped", [provEl.textContent, !!entries[entries.length - 2].prov], ["", false]);
same("provisional calls are counted", ctx.liveSessionTokens, 12);
setFinal(false);

// A segment cut after a clause end: the clause goes now, only the tail waits.
same("split at the last clause end", splitClauseTail(["はい。", "お知らせがあるかもしれないですけど、えっと三菱自動車からの。"]),
     ["はい。", "お知らせがあるかもしれないですけど、", "えっと三菱自動車からの。"]);
same("a finished line is not split", splitClauseTail(["決まっています、はい。"]), ["決まっています、はい。"]);
same("no clause end inside: not split", splitClauseTail(["三菱自動車からコピーに対しての、"]),
     ["三菱自動車からコピーに対しての、"]);
same("line ends", ["それで大丈夫です。", "今日追加で来たんですが。", "資料の中で。", "一応ご留意。", "えっとあの。",
                   "the meeting starts at the.", "We can decide it later.", "我觉得这个方案", "那我们明天再聊吧。"].map(lineEnd),
     ["S", "C", "M", "N", "F", "M", "S", "M", "S"]);
console.log(fails.length ? "RESULT: FAIL\n" + fails.join("\n") : "RESULT: OK");
