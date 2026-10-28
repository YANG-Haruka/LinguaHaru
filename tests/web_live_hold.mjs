// Run the REAL web translation-hold code (extracted verbatim from app.js; twin
// of Qt _queue_translation): lines ending mid-sentence — including the STT's
// own "。" after a particle at a segment cut — are held and translated with
// their continuation; a held line goes once the pause reached the full hang,
// checked inside the commit chain (where the hold list is up to date).
//
// usage: node tests/web_live_hold.mjs webapp/static/app.js  (driven by tests/test_live_stream_race.py)
import fs from "node:fs";
import vm from "node:vm";

const src = fs.readFileSync(process.argv[2], "utf8");
const start = src.indexOf("let liveCommitChain = Promise.resolve();");
const end = src.indexOf("async function _translateLiveLine(");
if (start < 0 || end < 0) throw new Error("region markers not found");
const sent = [];
const ctx = { WORDLIKE: /[0-9A-Za-z぀-ヿ㐀-鿿]/, liveTimeStamp: () => "00:00:00", appendLive: () => {},
              _translateLiveLine: async (_ts, s) => { sent.push(s); },
              liveQuiet: false, liveRecogBusy: false, liveRecogQueue: [] };
vm.createContext(ctx);
vm.runInContext(src.slice(start, end) + "\nthis.api = { commitLiveSentence, liveFlushHold };", ctx);
const { commitLiveSentence, liveFlushHold } = ctx.api;
const settle = async () => { for (let i = 0; i < 50; i++) await Promise.resolve(); };
const fails = [];
const expect = (what, want) => {
  if (JSON.stringify(sent) !== JSON.stringify(want)) fails.push(`${what}: ${JSON.stringify(sent)}`);
  sent.length = 0;
};

commitLiveSentence("ちなみにそこってあの。");          // the STT's "。" after a filler
commitLiveSentence("前にいただいた資料の中で。");      // ... and after a particle
await settle();
expect("dangling lines are held", []);
commitLiveSentence("十月十日って書いてありました。");
await settle();
expect("held lines go with the sentence that completes them",
       ["ちなみにそこってあの。前にいただいた資料の中で。十月十日って書いてありました。"]);
commitLiveSentence("それで大丈夫です。");
await settle();
expect("a real sentence end translates alone", ["それで大丈夫です。"]);

commitLiveSentence("では次の議題に");
liveFlushHold(true);                    // a short pause: not quiet yet
await settle();
expect("not before the pause reached the full hang", []);
ctx.liveQuiet = true; ctx.liveRecogBusy = true;
liveFlushHold(true);
await settle();
expect("not while a recognition result is pending", []);
ctx.liveRecogBusy = false;
commitLiveSentence("ついて");             // still queued on the chain when ...
liveFlushHold(true);                     // ... the flush is asked for: must include it
await settle();
expect("the flush sees lines still queued on the commit chain", ["では次の議題について"]);
console.log(fails.length ? "RESULT: FAIL\n" + fails.join("\n") : "RESULT: OK");
