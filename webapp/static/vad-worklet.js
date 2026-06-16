// vad-worklet.js — energy VAD + ring buffer + utterance segmentation on the
// audio thread (AudioWorkletGlobalScope), so UI jank can't drop frames or skew
// timing. Adapted from the Harucall project's proven VAD (continuous, hands-free).
//
// main → worklet:  { type:'mode', mode:'open'|'block' }, { type:'mute', value }
// worklet → main:  { type:'level', level }              (throttled input level)
//                  { type:'speechstart' }               (confirmed voice onset)
//                  { type:'partial', sampleRate, pcm }  (Float32 audio-so-far, COPIED — for live streaming captions)
//                  { type:'segment', sampleRate, pcm }  (Float32 utterance, transferred — final)
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
    this.lastPartial = 0;
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
    const total = this.segN;
    const pcm = new Float32Array(total);
    let o = 0; for (let i = 0; i < this.seg.length; i++) { pcm.set(this.seg[i], o); o += this.seg[i].length; }
    this.capturing = false; this.seg = []; this.segN = 0; this.voiceMs = 0; this.silenceMs = 0;
    if (durMs < this.minSegMs || total === 0) { this.port.postMessage({ type: 'drop', reason: 'too-short' }); return; }
    this.port.postMessage({ type: 'segment', sampleRate: this.sr, pcm: pcm.buffer }, [pcm.buffer]);
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
      if (level > onTh) {
        this.voiceMs += dtMs;
        if (this.voiceMs >= this.onMs) {           // confirmed speech → capture with pre-roll
          this.capturing = true; this.silenceMs = 0; this.segDip = false; this.segMin = 1; this.segMax = 0;
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
      const hang = durMs < 4000 ? this.hangMs
                 : durMs < 8000 ? this.hangMs * 0.7
                 : Math.max(500, this.hangMs * 0.55);
      if (this.silenceMs >= hang) { this._flush(durMs); }
      else if (durMs > this.noiseMaxMs && !this.segDip && (this.segMax <= 0 || (this.segMax - this.segMin) / this.segMax < 0.45)) {
        this.port.postMessage({ type: 'drop', reason: 'steady-noise' }); this._abort();
      } else if (durMs > this.maxSegMs) { this._flush(durMs); }
    }
    return true;
  }
}
registerProcessor('vad-processor', VADProcessor);
