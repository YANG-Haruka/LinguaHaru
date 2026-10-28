// vad-worklet.js — energy VAD + ring buffer + utterance segmentation on the
// audio thread (AudioWorkletGlobalScope), so UI jank can't drop frames or skew
// timing. Adapted from the Harucall project's proven VAD (continuous, hands-free).
//
// main → worklet:  { type:'mode', mode:'open'|'block' }, { type:'mute', value }
// worklet → main:  { type:'level', level }              (throttled input level)
//                  { type:'speechstart' }               (confirmed voice onset)
//                  { type:'partial', sampleRate, pcm }  (Float32 audio-so-far, COPIED — for live streaming captions)
//                  { type:'segment', sampleRate, pcm }  (Float32 utterance, transferred — final)
//                  { type:'silence' }                   (the pause after a segment reached hangMs)
//                  { type:'drop', reason }              (too-short / steady-noise)
class VADProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    this.onMs = o.onMs || 90;            // sustained voice to confirm a turn
    this.hangMs = o.hangMs || 900;       // trailing silence that ends an utterance
    this.minSegMs = o.minSegMs || 280;   // shorter than this → drop as a blip
    this.noiseMaxMs = o.noiseMaxMs || 6000;   // flat loud noise dropped after this
    this.maxSegMs = o.maxSegMs || 30000;      // hard ceiling → force-send
    this.partialMs = o.partialMs || 360;      // emit a live partial this often while capturing
    this.finalMode = !!o.finalMode;           // page commits only complete segments: end long ones sooner
    this.quietMs = 0; this.quietSent = true;  // silence since the last segment (-> 'silence')
    this.lastPartial = 0;
    this.lastCapTry = 0;
    const prerollMs = o.prerollMs || 500;
    this.onAbs = o.onAbs || 0.009; this.onMul = o.onMul || 2.5;       // onset
    this.offAbs = o.offAbs || 0.006; this.offMul = o.offMul || 1.7;   // end-of-speech

    this.sr = sampleRate;                // global in the worklet scope
    this.preN = Math.max(1, Math.floor(this.sr * prerollMs / 1000));
    this.ring = new Float32Array(this.preN);
    this.ringPos = 0; this.ringFull = false;

    this.mode = 'block'; this.muted = false;
    this.noiseFloor0 = o.noiseFloor || 0.003;
    this.capturing = false; this.seg = []; this.segN = 0;
    this.voiceMs = 0; this.silenceMs = 0;
    this.segDip = false; this.segMin = 1; this.segMax = 0;
    this.noiseFloor = this.noiseFloor0; this.lastLevelPost = 0;

    this.port.onmessage = (e) => {
      const m = e.data || {};
      if (m.type === 'mode') {
        this.mode = m.mode;
        if (m.mode === 'block') this._abort();
      } else if (m.type === 'mute') {
        this.muted = !!m.value;
        if (this.muted) this._abort();
      }
    };
  }

  _abort() { this.capturing = false; this.seg = []; this.segN = 0; this.voiceMs = 0; this.silenceMs = 0; }

  _ringWrite(buf) {
    for (let i = 0; i < buf.length; i++) {
      this.ring[this.ringPos] = buf[i];
      this.ringPos++;
      if (this.ringPos >= this.preN) { this.ringPos = 0; this.ringFull = true; }
    }
  }
  _ringSnapshot() {
    const n = this.ringFull ? this.preN : this.ringPos;
    const out = new Float32Array(n);
    if (!this.ringFull) { out.set(this.ring.subarray(0, this.ringPos)); }
    else { const tail = this.preN - this.ringPos; out.set(this.ring.subarray(this.ringPos), 0); out.set(this.ring.subarray(0, this.ringPos), tail); }
    return out;
  }
  _snapshotSeg() {                               // COPY of audio-so-far (keep capturing)
    const pcm = new Float32Array(this.segN);
    let o = 0; for (let i = 0; i < this.seg.length; i++) { pcm.set(this.seg[i], o); o += this.seg[i].length; }
    return pcm;
  }
  _flush(durMs) {
    this.quietMs = this.silenceMs; this.quietSent = false;   // keep timing this pause
    const total = this.segN;
    const pcm = new Float32Array(total);
    let o = 0; for (let i = 0; i < this.seg.length; i++) { pcm.set(this.seg[i], o); o += this.seg[i].length; }
    this.capturing = false; this.seg = []; this.segN = 0; this.voiceMs = 0; this.silenceMs = 0;
    // The pre-roll ring still holds this segment's last 500 ms; after the 30 s
    // cap the next segment starts at once and would re-send it (the same words
    // twice at the seam). Restart the ring from here. Twin of Qt _vad_step.
    this.ringPos = 0; this.ringFull = false;
    if (durMs < this.minSegMs || total === 0) { this.port.postMessage({ type: 'drop', reason: 'too-short' }); return; }
    this.port.postMessage({ type: 'segment', sampleRate: this.sr, pcm: pcm.buffer }, [pcm.buffer]);
  }
  // Quietest ~80 ms window of the last 2 s: [segment sample offset of its
  // centre, depth = its energy / the median window's — near 0 is a real pause,
  // near 1 means there is none]. Twin of Qt LivePage._quiet_cut. Windows
  // overlap (20 ms hop): an end-aligned grid of whole windows, re-checked every
  // 80 ms, kept straddling the same short pause and never saw it.
  _quietCut() {
    const win = Math.floor(this.sr * 0.08), hop = win >> 2;
    const len = Math.min(this.segN >> 1, this.sr * 2);
    if (len < 2 * win) return [this.segN, 0];
    const tail = new Float32Array(len);
    for (let i = this.seg.length - 1, pos = len; i >= 0 && pos > 0; i--) {
      const c = this.seg[i], take = Math.min(pos, c.length);
      tail.set(c.subarray(c.length - take), pos - take); pos -= take;
    }
    const cum = new Float64Array(len + 1);
    for (let i = 0; i < len; i++) cum[i + 1] = cum[i] + tail[i] * tail[i];
    const e = [];
    for (let s = 0; s + win <= len; s += hop) e.push([cum[s + win] - cum[s], s]);
    let best = e[0]; for (const x of e) if (x[0] < best[0]) best = x;
    const med = e.map((x) => x[0]).sort((a, b) => a - b)[e.length >> 1];
    return [this.segN - len + best[1] + (win >> 1), best[0] / Math.max(med, 1e-12)];
  }
  // Length cap mid-speech: send the audio up to `cut` and keep capturing the
  // rest — nothing dropped or recognized twice.
  _cutAt(cut) {
    const pcm = this._snapshotSeg();
    const head = pcm.slice(0, cut), rest = pcm.slice(cut);
    this.seg = [rest]; this.segN = rest.length;
    this.port.postMessage({ type: 'segment', sampleRate: this.sr, pcm: head.buffer }, [head.buffer]);
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const buf = input[0];                          // Float32Array (128 samples)
    let s = 0; for (let i = 0; i < buf.length; i++) { const v = buf[i]; s += v * v; }
    const level = Math.sqrt(s / buf.length);
    const dtMs = (buf.length / this.sr) * 1000;

    this._ringWrite(buf);
    if (this.capturing) {
      this.seg.push(new Float32Array(buf)); this.segN += buf.length;
      // Emit a live partial (audio so far) so the UI can stream the recognition.
      if (currentTime - this.lastPartial > this.partialMs / 1000) {
        this.lastPartial = currentTime;
        const snap = this._snapshotSeg();
        this.port.postMessage({ type: 'partial', sampleRate: this.sr, pcm: snap.buffer }, [snap.buffer]);
      }
    }

    if (currentTime - this.lastLevelPost > 0.1) { this.lastLevelPost = currentTime; this.port.postMessage({ type: 'level', level }); }

    if (this.muted || this.mode === 'block') {
      if (this.capturing) this._abort(); else { this.voiceMs = 0; this.silenceMs = 0; }
      return true;
    }

    const offTh = Math.max(this.offAbs, this.noiseFloor * this.offMul);
    if (!this.capturing) {
      const onTh = Math.max(this.onAbs, this.noiseFloor * this.onMul);
      // Adapt the ambient floor ONLY on quiet frames so speech can't drag the
      // threshold above your own voice.
      if (level < onTh) this.noiseFloor = this.noiseFloor * 0.99 + level * 0.01;
      if (!this.quietSent) {
        if (level <= onTh) this.quietMs += dtMs;
        if (this.quietMs >= this.hangMs) { this.quietSent = true; this.port.postMessage({ type: 'silence' }); }
      }
      if (level > onTh) {
        this.voiceMs += dtMs;
        if (this.voiceMs >= this.onMs) {           // confirmed speech → capture with pre-roll
          this.capturing = true; this.silenceMs = 0; this.segDip = false; this.segMin = 1; this.segMax = 0;
          this.quietSent = true;                   // the speaker went on: no 'silence' for this pause
          this.lastPartial = currentTime;        // first partial ~partialMs after onset
          const pre = this._ringSnapshot();
          this.seg = [pre]; this.segN = pre.length;
          this.port.postMessage({ type: 'speechstart' });
        }
      } else this.voiceMs = 0;
    } else {
      if (level < offTh) { this.silenceMs += dtMs; this.segDip = true; } else this.silenceMs = 0;
      if (level < this.segMin) this.segMin = level;
      if (level > this.segMax) this.segMax = level;
      const durMs = (this.segN / this.sr) * 1000;
      // Progressive silence: longer utterances end on a shorter pause (lower
      // latency on long speech). Mirrors the Qt side. The floor is kept high
      // enough (>=500ms) that a slow speaker's natural between-phrase pauses
      // don't chop a sentence into fragments.
      // Final mode (twin of Qt _FINAL_HANG_STEPS): every sentence of a segment
      // waits for its end, so past 3 s / 6 s a 450 / 300 ms pause ends it.
      const hang = this.finalMode
                 ? (durMs < 3000 ? this.hangMs : Math.min(this.hangMs, durMs < 6000 ? 450 : 300))
                 : durMs < 4000 ? this.hangMs
                 : durMs < 8000 ? this.hangMs * 0.7
                 : Math.max(500, this.hangMs * 0.55);
      if (this.silenceMs >= hang) { this._flush(durMs); }
      else if (durMs > this.noiseMaxMs && !this.segDip && (this.segMax <= 0 || (this.segMax - this.segMin) / this.segMax < 0.45)) {
        this.port.postMessage({ type: 'drop', reason: 'steady-noise' }); this._abort();
      } else if (durMs > this.maxSegMs && currentTime - this.lastCapTry >= 0.08) {
        // Cutting at this exact frame split words in half ("大家好" -> "大"+"家好").
        // Cut at the quietest moment of the last 2 s instead; no real dip there
        // yet? Keep listening for one, up to 1.5x the cap. Twin of Qt _vad_step.
        this.lastCapTry = currentTime;
        const [cut, depth] = this._quietCut();
        if (depth <= 0.15 || durMs >= 1.5 * this.maxSegMs) this._cutAt(cut);
      }
    }
    return true;
  }
}
registerProcessor('vad-processor', VADProcessor);
