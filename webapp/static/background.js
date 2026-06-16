/* LinguaHaru — animated background canvas.
   Night (dark theme): deep-blue void, multi-depth twinkling stars, slow nebula
   glow, occasional shooting stars. Day (light theme): a Makoto-Shinkai sky —
   luminous gradient, drifting cumulus clouds, warm sun bloom, floating motes.
   Sits behind the UI (pointer-events:none); driven by window.LHBackground.setMode. */
(function () {
  const canvas = document.getElementById("bg-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const TAU = Math.PI * 2;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let W = 0, H = 0, DPR = 1, mode = "night", t = 0, last = 0, raf = null, nextMeteor = 2;
  let stars = [], nebula = [], meteors = [], clouds = [], motes = [], birds = [];
  let birdTimer = 3;
  const rand = (a, b) => a + Math.random() * (b - a);

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 1.6);
    W = window.innerWidth; H = window.innerHeight;
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
    canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    initStars(); initNebula(); initClouds(); initMotes();
    buildBackdrop();
    drawOnce();
  }

  // ---------- night ----------
  function initStars() {
    stars = [];
    const n = Math.min(420, Math.round((W * H) / 6200));
    for (let i = 0; i < n; i++) {
      const depth = Math.random();
      stars.push({ x: Math.random() * W, y: Math.random() * H, r: 0.4 + depth * 1.5,
        a: 0.25 + Math.random() * 0.6, tw: 0.4 + Math.random() * 1.7, ph: Math.random() * TAU });
    }
  }
  function initNebula() {
    nebula = [
      { x: 0.20, y: 0.16, r: 0.55, c: "63,120,255", a: 0.10 },
      { x: 0.86, y: 0.10, r: 0.42, c: "46,92,200", a: 0.08 },
      { x: 0.60, y: 0.52, r: 0.62, c: "96,72,210", a: 0.06 },
    ];
  }
  function spawnMeteor() {
    const fromLeft = Math.random() < 0.5;
    meteors.push({
      x: rand(W * 0.1, W * 0.9), y: rand(-60, H * 0.18),
      vx: (fromLeft ? 1 : -0.7) * rand(5, 9), vy: rand(7, 11),
      len: rand(140, 280), life: 0, max: rand(48, 78),
    });
  }
  function drawNight(dt) {
    // Nebula glow is pre-rendered into the cached backdrop; only stars/meteors
    // (the animated layers) are drawn per frame here.
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = "#eaf2ff";
    for (const s of stars) {
      const tw = reduce ? 0.85 : 0.5 + 0.5 * Math.sin(t * s.tw + s.ph);
      ctx.globalAlpha = s.a * tw;
      ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, TAU); ctx.fill();
      if (s.r > 1.25) {
        ctx.globalAlpha = s.a * tw * 0.22;
        ctx.beginPath(); ctx.arc(s.x, s.y, s.r * 3.4, 0, TAU); ctx.fill();
        ctx.fillStyle = "#eaf2ff";
      }
    }
    ctx.globalAlpha = 1;
    if (!reduce) {
      for (const m of meteors) {
        m.x += m.vx; m.y += m.vy; m.life++;
        const inv = 1 / Math.hypot(m.vx, m.vy);
        const tx = m.x - m.vx * inv * m.len, ty = m.y - m.vy * inv * m.len;
        const fade = Math.min(1, m.life / 7) * Math.max(0, 1 - m.life / m.max);
        const g = ctx.createLinearGradient(m.x, m.y, tx, ty);
        g.addColorStop(0, `rgba(255,255,255,${0.9 * fade})`);
        g.addColorStop(0.35, `rgba(175,205,255,${0.45 * fade})`);
        g.addColorStop(1, "rgba(130,165,255,0)");
        ctx.strokeStyle = g; ctx.lineWidth = 1.7; ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(m.x, m.y); ctx.lineTo(tx, ty); ctx.stroke();
        ctx.globalAlpha = fade; ctx.fillStyle = "#fff";
        ctx.beginPath(); ctx.arc(m.x, m.y, 1.7, 0, TAU); ctx.fill(); ctx.globalAlpha = 1;
      }
      meteors = meteors.filter((m) => m.life < m.max && m.y < H + 60 && m.x > -300 && m.x < W + 300);
      nextMeteor -= dt;
      if (nextMeteor <= 0) { spawnMeteor(); nextMeteor = rand(2.6, 5.5); }
    }
  }

  // ---------- day (Shinkai) ----------
  function makeCloud(x, y, scale) {
    // Soft, flat-bottomed cumulus: overlapping pure-white puffs clustered along
    // a baseline (rounded on top, flat underneath), built up additively so the
    // body reads as one fluffy mass rather than a hard white blob, then a cool
    // shaded underside for depth. Pad the sprite so blurred edges don't clip.
    const w = Math.round(360 * scale), h = Math.round(190 * scale);
    const off = document.createElement("canvas"); off.width = w; off.height = h;
    const o = off.getContext("2d");
    const baseY = h * 0.70;                 // flat cloud base sits here
    const puffs = 10 + Math.floor(Math.random() * 4);
    o.globalCompositeOperation = "lighter";  // accumulate softly, no hard cores
    for (let i = 0; i < puffs; i++) {
      const f = puffs === 1 ? 0.5 : i / (puffs - 1);
      const mid = 1 - Math.abs(f - 0.5) * 2;            // 1 in the centre, 0 at the ends
      const pr = h * (0.14 + 0.26 * mid) * rand(0.85, 1.18);
      const px = w * (0.14 + 0.72 * f) + rand(-w * 0.02, w * 0.02);
      const py = baseY - pr * rand(0.30, 0.80);          // rise above the flat base
      const g = o.createRadialGradient(px, py, pr * 0.1, px, py, pr);
      g.addColorStop(0, "rgba(255,255,255,0.34)");
      g.addColorStop(0.5, "rgba(255,255,255,0.18)");
      g.addColorStop(1, "rgba(255,255,255,0)");
      o.fillStyle = g; o.beginPath(); o.arc(px, py, pr, 0, TAU); o.fill();
    }
    // Cool shading hugging the underside → grounds the cloud, kills the smear look.
    o.globalCompositeOperation = "source-atop";
    const shade = o.createLinearGradient(0, baseY - h * 0.18, 0, baseY + h * 0.08);
    shade.addColorStop(0, "rgba(176,202,228,0)");
    shade.addColorStop(1, "rgba(150,180,214,0.30)");
    o.fillStyle = shade; o.fillRect(0, 0, w, h);
    o.globalCompositeOperation = "source-over";
    return { x, y, w, h, spr: off, vx: rand(0.05, 0.14), bob: Math.random() * TAU };
  }
  function initClouds() {
    clouds = [];
    const n = Math.max(4, Math.round(W / 360));
    for (let i = 0; i < n; i++) clouds.push(makeCloud((i / n) * (W + 300) - 150, H * rand(0.12, 0.62), rand(0.7, 1.5)));
  }
  function initMotes() {
    motes = [];
    const n = Math.min(120, Math.round((W * H) / 26000));
    for (let i = 0; i < n; i++) motes.push({ x: Math.random() * W, y: Math.random() * H, r: 0.6 + Math.random() * 1.8,
      a: 0.1 + Math.random() * 0.32, vy: -rand(0.1, 0.32), vx: rand(0.04, 0.13), ph: Math.random() * TAU });
  }
  function spawnFlock() {
    birds = [];
    const n = 3 + Math.floor(Math.random() * 4);
    const dir = Math.random() < 0.5 ? 1 : -1;
    const baseY = H * rand(0.1, 0.32), sx = dir > 0 ? -60 : W + 60;
    for (let i = 0; i < n; i++) {
      birds.push({ x: sx - dir * i * rand(20, 34), y: baseY + Math.abs(i - (n - 1) / 2) * rand(7, 13),
        s: rand(5, 9), v: dir * rand(16, 28), ph: Math.random() * TAU });
    }
  }
  function drawBirds(dt) {
    if (reduce) return;
    if (birds.length === 0) { birdTimer -= dt; if (birdTimer <= 0) spawnFlock(); return; }
    ctx.strokeStyle = "rgba(44,60,82,0.5)"; ctx.lineWidth = 1.4; ctx.lineCap = "round";
    let off = true;
    for (const b of birds) {
      b.x += b.v * dt;
      const wing = b.s * (0.55 + (Math.sin(t * 6 + b.ph) * 0.5 + 0.5) * 0.55);
      ctx.beginPath();
      ctx.moveTo(b.x - b.s, b.y - wing * 0.2);
      ctx.lineTo(b.x, b.y + wing * 0.5);
      ctx.lineTo(b.x + b.s, b.y - wing * 0.2);
      ctx.stroke();
      if (b.x > -50 && b.x < W + 50) off = false;
    }
    if (off) { birds = []; birdTimer = rand(5, 11); }
  }
  function drawDay(dt) {
    // The sky gradient + sun bloom are pre-rendered into the cached backdrop;
    // only the animated layers are drawn per frame here. sx/sy mark the sun.
    const sx = W * 0.82, sy = H * 0.18;
    ctx.globalCompositeOperation = "screen";
    // faint lens-flare dots along sun→center axis (fill only each dot's box, not
    // the whole canvas — same look, a fraction of the pixels)
    if (!reduce) {
      const cx = W * 0.5, cy = H * 0.5;
      for (let i = 1; i <= 3; i++) {
        const fx = sx + (cx - sx) * (i * 0.5), fy = sy + (cy - sy) * (i * 0.5);
        const fr = Math.max(8, 30 + 18 * Math.sin(t * 0.6 + i));
        const fg = ctx.createRadialGradient(fx, fy, 0, fx, fy, fr);
        fg.addColorStop(0, `rgba(255,238,205,${0.06 + 0.03 * Math.sin(t + i)})`);
        fg.addColorStop(1, "rgba(255,238,205,0)");
        ctx.fillStyle = fg; ctx.fillRect(fx - fr, fy - fr, fr * 2, fr * 2);
      }
    }
    // soft god-rays fanning down from the sun
    if (!reduce) {
      const rayLen = Math.max(W, H) * 0.95;
      for (let i = 0; i < 6; i++) {
        const a = Math.PI * 0.60 + i * 0.105 + Math.sin(t * 0.12 + i * 1.7) * 0.016, a2 = a + 0.03;
        const al = Math.max(0, 0.05 + 0.035 * Math.sin(t * 0.5 + i));
        const rg = ctx.createLinearGradient(sx, sy, sx + Math.cos(a) * rayLen, sy + Math.sin(a) * rayLen);
        rg.addColorStop(0, `rgba(255,244,214,${al})`); rg.addColorStop(1, "rgba(255,244,214,0)");
        ctx.fillStyle = rg;
        ctx.beginPath(); ctx.moveTo(sx, sy);
        ctx.lineTo(sx + Math.cos(a) * rayLen, sy + Math.sin(a) * rayLen);
        ctx.lineTo(sx + Math.cos(a2) * rayLen, sy + Math.sin(a2) * rayLen);
        ctx.closePath(); ctx.fill();
      }
    }
    ctx.globalCompositeOperation = "source-over";
    for (const c of clouds) {
      if (!reduce) { c.x += c.vx; if (c.x - c.w > W) c.x = -c.w - rand(0, 200); }
      const by = c.y + (reduce ? 0 : Math.sin(t * 0.3 + c.bob) * 4);
      ctx.globalAlpha = 0.94; ctx.drawImage(c.spr, c.x, by);
    }
    ctx.globalAlpha = 1;
    drawBirds(dt);
    ctx.fillStyle = "#fff8ea";
    for (const m of motes) {
      if (!reduce) { m.y += m.vy; m.x += m.vx; if (m.y < -6) { m.y = H + 6; m.x = Math.random() * W; } if (m.x > W + 6) m.x = -6; }
      ctx.globalAlpha = m.a * (reduce ? 0.7 : 0.55 + 0.45 * Math.sin(t * 1.5 + m.ph));
      ctx.beginPath(); ctx.arc(m.x, m.y, m.r, 0, TAU); ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  // ---------- cached static backdrop ----------
  // The gradient sky + sun bloom (day) / nebula (night) are identical every
  // frame, so render them ONCE to an offscreen canvas and blit that each frame.
  // This removes 3-5 full-canvas gradient creations+fills per frame that were
  // saturating the main thread and making the UI (marquee, scrolling) stutter.
  let backdrop = null, bctx = null;
  function buildBackdrop() {
    if (!backdrop) { backdrop = document.createElement("canvas"); bctx = backdrop.getContext("2d"); }
    backdrop.width = Math.round(W * DPR); backdrop.height = Math.round(H * DPR);
    bctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    bctx.clearRect(0, 0, W, H);
    if (mode === "day") {
      const g = bctx.createLinearGradient(0, 0, 0, H);
      g.addColorStop(0, "#3a93cf"); g.addColorStop(0.42, "#78bfe6");
      g.addColorStop(0.72, "#c2e4f1"); g.addColorStop(1, "#fbe6cd");
      bctx.fillStyle = g; bctx.fillRect(0, 0, W, H);
      const sx = W * 0.82, sy = H * 0.18, rr = Math.max(W, H) * 0.55;
      const sg = bctx.createRadialGradient(sx, sy, 0, sx, sy, rr);
      sg.addColorStop(0, "rgba(255,249,235,0.70)");
      sg.addColorStop(0.07, "rgba(255,243,214,0.40)");
      sg.addColorStop(0.28, "rgba(255,228,186,0.12)");
      sg.addColorStop(1, "rgba(255,226,182,0)");
      bctx.globalCompositeOperation = "screen"; bctx.fillStyle = sg; bctx.fillRect(0, 0, W, H);
      bctx.globalCompositeOperation = "source-over";
    } else {
      bctx.globalCompositeOperation = "screen";
      for (const nb of nebula) {
        const cx = nb.x * W, cy = nb.y * H, rr = nb.r * Math.max(W, H);
        const g = bctx.createRadialGradient(cx, cy, 0, cx, cy, rr);
        g.addColorStop(0, `rgba(${nb.c},${nb.a})`); g.addColorStop(1, `rgba(${nb.c},0)`);
        bctx.fillStyle = g; bctx.fillRect(0, 0, W, H);
      }
      bctx.globalCompositeOperation = "source-over";
    }
  }

  // ---------- loop ----------
  function render(dt) {
    ctx.clearRect(0, 0, W, H);
    if (backdrop) ctx.drawImage(backdrop, 0, 0, W, H);
    if (mode === "day") drawDay(dt); else drawNight(dt);
  }
  function drawOnce() { render(0.016); }
  function frame(ts) {
    const dt = Math.min(0.05, (ts - last) / 1000) || 0.016; last = ts; t += dt;
    render(dt);
    raf = requestAnimationFrame(frame);
  }
  function start() { if (raf == null && !reduce) { last = performance.now(); raf = requestAnimationFrame(frame); } }
  function stop() { if (raf != null) { cancelAnimationFrame(raf); raf = null; } }

  window.addEventListener("resize", resize);
  document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); else start(); });

  resize();
  window.LHBackground = {
    setMode(m) { mode = m === "light" ? "day" : "night"; meteors = []; buildBackdrop(); drawOnce(); },
  };
  start();
})();
