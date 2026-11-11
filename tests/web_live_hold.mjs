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
let lastEntry = null, lastFid = 0, interim = "";
let gate = null;   // an array: final translations wait until their resolver in it is called
let lastAlt = "";  // the second opinion (the preview model's text) sent with the last final translation
const provEl = { textContent: "" };
const ctx = { WORDLIKE: /[0-9A-Za-z぀-ヿ㐀-鿿]/, liveTimeStamp: () => "00:00:00",
              // a source line opens its floating-caption pair
              appendLive: (id, text) => {
                if (id !== "live-input") return null;
                const e = { src: text.replace(/^\[[^\]]*\]\s*/, "").trim(), dst: "" };
                entries.push(e); return e;
              },
              _translateLiveLine: async (_ts, s, entry, fid, alt) => {
                sent.push(s); lastEntry = entry; lastFid = fid; lastAlt = alt;
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
console.log(fails.length ? "RESULT: FAIL\n" + fails.join("\n") : "RESULT: OK");
