"""End-to-end PDF failure tests against mock.py, through the real Web app.

Setup (from the repo root, with the app's Python):
  1. python tools/pdf_e2e/run.py --setup        # temp interface "(Mock) PDF-Test"
  2. python tools/pdf_e2e/mock.py               # mock API on :8851 (background)
  3. PORT=8842 python -m webapp.server          # web app (background)
  4. python tools/pdf_e2e/run.py <mode> <tag>   # one case; reuse a tag = same PDF
  5. python tools/pdf_e2e/run.py --cleanup      # remove the temp interface + key
Use a NEW tag per failure case: the same text hits the BabelDOC cache."""
import glob, json, os, sys, time, uuid, urllib.request, http.cookiejar

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
WORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_work")
B, M = "http://127.0.0.1:8842", "http://127.0.0.1:8851"
MODEL = "(Mock) PDF-Test"

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def setup():
    from core import backend
    from core.api_keys import save_api_key_for_model
    backend.write_api_config(MODEL, {"base_url": "http://127.0.0.1:8851/v1", "model": "mock",
                                     "temperature": 0.3, "top_p": 0.9, "max_retries": 2})
    save_api_key_for_model(MODEL, "sk-mock")


def cleanup():
    from core.paths import API_CONFIG_DIR, DATA_DIR
    for p in (os.path.join(API_CONFIG_DIR, f"{MODEL}.json"), os.path.join(DATA_DIR, "keys", "Mock.json")):
        if os.path.exists(p):
            os.remove(p)


def make_pdf(tag):
    import fitz
    os.makedirs(WORK, exist_ok=True)
    d = fitz.open()
    p = d.new_page()
    paras = [f"Harbor report {tag}. The northern pier needs new railings before winter.",
             f"Lighthouse keepers {tag} repainted the door last weekend with bright blue paint.",
             f"Fishing boats {tag} returned early because the weather turned cold and windy."]
    for i, t in enumerate(paras):
        p.insert_textbox(fitz.Rect(50, 60 + i * 100, 550, 140 + i * 100), t, fontsize=12)
    path = os.path.join(WORK, f"{tag}.pdf")
    d.save(path)
    return path


def mode(m):
    try:
        op.open(f"{M}/mode/{m}").read()
    except Exception:  # noqa: BLE001 — "down" case: mock intentionally offline
        pass


def start(path):
    bd = uuid.uuid4().hex
    parts = [f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in {"src_lang": "English", "dst_lang": "中文", "model": MODEL,
                          "use_online": "true"}.items()]
    parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="files"; filename="{os.path.basename(path)}"'
                 f'\r\nContent-Type: application/pdf\r\n\r\n'.encode() + open(path, "rb").read() + b"\r\n")
    parts.append(f"--{bd}--\r\n".encode())
    req = urllib.request.Request(B + "/api/translate", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={bd}"})
    return json.load(op.open(req))["task_id"]


def wait(tid, timeout=240):
    last = None
    for line in op.open(f"{B}/api/progress/{tid}", timeout=timeout):
        line = line.decode().strip()
        if line.startswith("data:"):
            last = json.loads(line[5:])
            if last.get("status") in ("done", "error", "stopped"):
                break
    return last


def output_cjk(tag):
    import fitz
    fs = glob.glob(os.path.join(ROOT, "result", "**", f"{tag}*_en2zh*.pdf"), recursive=True)
    if not fs:
        return None
    t = fitz.open(max(fs, key=os.path.getmtime))[0].get_text()
    return sum("一" <= c <= "鿿" for c in t), ("Lighthouse" in t)


if __name__ == "__main__":
    if sys.argv[1] == "--setup":
        setup(); sys.exit()
    if sys.argv[1] == "--cleanup":
        cleanup(); sys.exit()
    m, tag = sys.argv[1], sys.argv[2]
    op.open(B + "/api/bootstrap").read()
    path = os.path.join(WORK, f"{tag}.pdf")
    if not os.path.exists(path):
        make_pdf(tag)
    mode(m)
    t0 = time.time()
    last = wait(start(path))
    try:
        calls = json.load(op.open(M + "/stats"))["calls"]
    except Exception:  # noqa: BLE001
        calls = "mock down"
    print(json.dumps({"mode": m, "tag": tag, "secs": round(time.time() - t0),
                      "status": last.get("status"), "error": (last.get("error") or "")[:200],
                      "tokens": last.get("tokens"), "api_calls": calls,
                      "out_cjk/lighthouse_left": output_cjk(tag)}, ensure_ascii=False))
