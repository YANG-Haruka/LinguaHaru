"""Remote OCR engines for image translation (config "ocr_engine"):

  local          on-device PP-OCRv6 (RapidOCR / PaddleOCR) — the default
  vlm            a vision LLM on any OpenAI-compatible interface (GPT, Claude,
                 Qwen-VL, Gemini, LM Studio / Ollama vision models). Text
                 POSITIONS still come from local text detection (fast, CPU is
                 fine), because LLM-reported coordinates are too loose to paint
                 the translation back; the model only READS the cropped lines.
  paddle_server  a self-hosted PaddleOCR / PaddleX serving endpoint (POST /ocr)
  baidu          Baidu AI Cloud general OCR (with positions)

Every engine returns what the local path does: (texts, quads, scores), quads
being 4 [x, y] points in image pixels, so grouping / inpainting / rendering
downstream is untouched."""
import base64
import io
import json
import re
import threading
import time

from core.log_config import app_logger

ENGINES = ("local", "vlm", "paddle_server", "baidu")
BAIDU_AK, BAIDU_SK = "BaiduOCR-APIKey", "BaiduOCR-SecretKey"   # api_keys entries


def _cfg(key, default=None):
    from core import backend
    return backend.get_config(key, default)


def selected_engine():
    e = _cfg("ocr_engine", "local")
    return e if e in ENGINES else "local"


def run(file_path, src_lang, detect_boxes):
    """OCR with the configured remote engine. `detect_boxes(file_path)` gives
    the local text-detection quads (used by the vlm engine). Raises on failure
    (the caller falls back to local OCR)."""
    engine = selected_engine()
    t0 = time.time()
    if engine == "vlm":
        out = _vlm(file_path, src_lang, detect_boxes)
    elif engine == "paddle_server":
        out = _paddle_server(file_path)
    elif engine == "baidu":
        out = _baidu(file_path, src_lang)
    else:
        raise ValueError(f"not a remote OCR engine: {engine}")
    app_logger.info(f"OCR via {engine}: {len(out[0])} lines in {time.time() - t0:.1f}s")
    return out


def _b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


# --- self-hosted PaddleOCR / PaddleX serving --------------------------------
def _paddle_server(file_path):
    """PaddleX basic serving, OCR pipeline: POST {url}/ocr {"file": b64,
    "fileType": 1} -> result.ocrResults[].prunedResult.{rec_texts, rec_scores,
    rec_polys}. Start one with: paddlex --serve --pipeline OCR --port 8080
    (or paddleocr's serving). An optional token goes in "Authorization: token …"
    (the PaddleX / AI Studio hosted-service form)."""
    import httpx
    url = str(_cfg("ocr_server_url", "") or "").strip().rstrip("/")
    if not url:
        raise ValueError("PaddleOCR server URL is not set")
    if not url.endswith("/ocr"):
        url += "/ocr"
    headers = {}
    token = str(_cfg("ocr_server_token", "") or "").strip()
    if token:
        headers["Authorization"] = f"token {token}"
    r = httpx.post(url, json={"file": _b64(file_path), "fileType": 1,
                              "visualize": False}, headers=headers, timeout=120)
    r.raise_for_status()
    data = r.json()
    if data.get("errorCode", 0) != 0:
        raise RuntimeError(f"PaddleOCR server error {data.get('errorCode')}: {data.get('errorMsg')}")
    texts, boxes, scores = [], [], []
    for page in (data.get("result") or {}).get("ocrResults") or []:
        res = page.get("prunedResult") or {}
        polys = res.get("rec_polys") or res.get("rec_boxes") or []
        for t, b, s in zip(res.get("rec_texts") or [], polys, res.get("rec_scores") or []):
            if len(b) == 4 and not isinstance(b[0], (list, tuple)):   # rec_boxes: x1,y1,x2,y2
                x1, y1, x2, y2 = b
                b = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
            texts.append(t)
            boxes.append([[float(x), float(y)] for x, y in b])
            scores.append(float(s))
    return texts, boxes, scores


# --- Baidu AI Cloud general OCR (with positions) ----------------------------
_BAIDU_LANG = {"zh": "CHN_ENG", "en": "ENG", "ja": "JAP", "ko": "KOR", "fr": "FRE",
               "es": "SPA", "pt": "POR", "de": "GER", "it": "ITA", "ru": "RUS"}
_baidu_token = {"value": None, "expires": 0.0, "for": None}
_baidu_lock = threading.Lock()


def _baidu_access_token(ak, sk):
    import httpx
    with _baidu_lock:
        if _baidu_token["value"] and _baidu_token["for"] == ak and time.time() < _baidu_token["expires"]:
            return _baidu_token["value"]
        r = httpx.post("https://aip.baidubce.com/oauth/2.0/token", timeout=30,
                       params={"grant_type": "client_credentials", "client_id": ak, "client_secret": sk})
        data = r.json()
        if "access_token" not in data:
            raise RuntimeError(f"Baidu OCR auth failed: {data.get('error_description') or data}")
        _baidu_token.update({"value": data["access_token"], "for": ak,   # ~30 days
                             "expires": time.time() + int(data.get("expires_in", 2592000)) - 3600})
        return data["access_token"]


def _baidu(file_path, src_lang):
    """general (标准含位置版): words_result[].{words, location{left,top,width,
    height}, probability{average}}. Images over 4096 px are downscaled (Baidu's
    limit) and the boxes scaled back."""
    import httpx
    from PIL import Image
    from core.api_keys import load_api_key_for_model
    ak, sk = load_api_key_for_model(BAIDU_AK).strip(), load_api_key_for_model(BAIDU_SK).strip()
    if not (ak and sk):
        raise ValueError("Baidu OCR API Key / Secret Key are not set")
    img = Image.open(file_path).convert("RGB")
    scale = min(1.0, 4096.0 / max(img.size))
    if scale < 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    from core.pipelines.image_translation_pipeline import _lang_code
    r = httpx.post("https://aip.baidubce.com/rest/2.0/ocr/v1/general",
                   params={"access_token": _baidu_access_token(ak, sk)}, timeout=60,
                   data={"image": base64.b64encode(buf.getvalue()).decode("ascii"),
                         "language_type": _BAIDU_LANG.get(_lang_code(src_lang), "CHN_ENG"),
                         "probability": "true"})
    data = r.json()
    if "error_code" in data:
        raise RuntimeError(f"Baidu OCR error {data['error_code']}: {data.get('error_msg')}")
    texts, boxes, scores = [], [], []
    for w in data.get("words_result") or []:
        loc = w.get("location") or {}
        x, y = loc.get("left", 0) / scale, loc.get("top", 0) / scale
        x2, y2 = x + loc.get("width", 0) / scale, y + loc.get("height", 0) / scale
        texts.append(w.get("words", ""))
        boxes.append([[x, y], [x2, y], [x2, y2], [x, y2]])
        scores.append(float((w.get("probability") or {}).get("average", 0.9)))
    return texts, boxes, scores


# --- vision LLM on an OpenAI-compatible interface ---------------------------
_VLM_BATCH = 16   # crops per request (small images: cheap, and keeps replies short)


def _crop_data_uri(img, quad, pad=4):
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    box = (max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad),
           min(img.width, int(max(xs)) + pad), min(img.height, int(max(ys)) + pad))
    crop = img.crop(box)
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _parse_ids(reply, n):
    """{"1": "text", ...} (JSON, possibly fenced / with chatter) -> list of n texts."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    return [str(data.get(str(i + 1), "") or "").strip() for i in range(n)]


def _vlm(file_path, src_lang, detect_boxes):
    from openai import OpenAI
    from PIL import Image
    from core.api_keys import load_api_key_for_model
    from core.llm.online_translation import (load_model_config, _create_completion,
                                             _thinking_off_params)
    from core.translation_modes import _sampling_supported
    name = str(_cfg("ocr_vlm_interface", "") or "")
    cfg = load_model_config(name) if name else None
    if not cfg or not cfg.get("base_url") or not cfg.get("model"):
        raise ValueError("No vision-model interface selected for OCR")
    quads = detect_boxes(file_path)
    if not quads:
        return [], [], []
    img = Image.open(file_path).convert("RGB")
    client = OpenAI(api_key=load_api_key_for_model(name) or "none",
                    base_url=cfg["base_url"], timeout=120.0, max_retries=1)
    lang_hint = f" The text is most likely {src_lang}." if src_lang and src_lang != "auto" else ""
    texts = []
    for start in range(0, len(quads), _VLM_BATCH):
        batch = quads[start:start + _VLM_BATCH]
        content = [{"type": "text", "text": (
            f"Below are {len(batch)} numbered image crops, each one line of text from a "
            "picture (manga, poster, screenshot...)." + lang_hint + " Transcribe the text "
            "in each crop exactly as written (keep the original language; do not "
            "translate, explain or correct it). Reply with ONLY a JSON object mapping the "
            'crop number to its text, e.g. {"1": "...", "2": "..."}; use "" for a crop '
            "with no readable text.")}]
        for i, q in enumerate(batch, 1):
            content.append({"type": "text", "text": f"[{i}]"})
            content.append({"type": "image_url", "image_url": {"url": _crop_data_uri(img, q)}})
        params = {"model": cfg["model"], "messages": [{"role": "user", "content": content}],
                  "max_tokens": 4096}
        if _sampling_supported(cfg):
            params["temperature"] = 0
        p_upd, eb = _thinking_off_params(cfg["base_url"])   # reading text needs no reasoning
        params.update(p_upd)
        if eb:
            params["extra_body"] = eb
        resp = _create_completion(client, params)
        texts += _parse_ids(resp.choices[0].message.content, len(batch))
    keep = [i for i, t in enumerate(texts) if t]
    return ([texts[i] for i in keep], [[[float(x), float(y)] for x, y in quads[i]] for i in keep],
            [0.99] * len(keep))
