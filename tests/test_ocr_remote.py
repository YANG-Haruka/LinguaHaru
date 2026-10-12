"""Remote OCR engines: response parsing for PaddleOCR serving / Baidu (mocked
HTTP) and the vision-LLM reply parser. Live paths were checked by hand against a
schema-exact PaddleX mock and LM Studio (qwen3.5-9b)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipelines import ocr_remote  # noqa: E402


class _Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


def _cfg(monkeypatch, **vals):
    monkeypatch.setattr(ocr_remote, "_cfg", lambda k, d=None: vals.get(k, d))


def test_paddle_server_polys_and_boxes(tmp_path, monkeypatch):
    import httpx
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG fake")
    _cfg(monkeypatch, ocr_server_url="http://h:8080/", ocr_server_token="t0k")
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, headers=headers, file_type=json["fileType"])
        return _Resp({"logId": "x", "errorCode": 0, "result": {"ocrResults": [{"prunedResult": {
            "rec_texts": ["你好", "Hi"], "rec_scores": [0.9, 0.8],
            "rec_polys": [[[1, 2], [9, 2], [9, 8], [1, 8]], [10, 20, 30, 40]]}}]}})
    monkeypatch.setattr(httpx, "post", post)
    texts, boxes, scores = ocr_remote._paddle_server(str(img))
    assert seen == {"url": "http://h:8080/ocr", "headers": {"Authorization": "token t0k"}, "file_type": 1}
    assert texts == ["你好", "Hi"] and scores == [0.9, 0.8]
    assert boxes[1] == [[10, 20], [30, 20], [30, 40], [10, 40]]   # rec_boxes -> quad


def test_baidu_scales_boxes_back(tmp_path, monkeypatch):
    import httpx
    from PIL import Image
    img = tmp_path / "big.png"
    Image.new("RGB", (8192, 100), "white").save(img)   # > 4096 -> sent at half size
    monkeypatch.setattr("core.api_keys.load_api_key_for_model", lambda n: {
        ocr_remote.BAIDU_AK: "ak", ocr_remote.BAIDU_SK: "sk"}[n])
    ocr_remote._baidu_token.update({"value": None, "for": None, "expires": 0})

    def post(url, params=None, data=None, timeout=None):
        if "oauth" in url:
            assert params["client_id"] == "ak" and params["client_secret"] == "sk"
            return _Resp({"access_token": "TOK", "expires_in": 2592000})
        assert params["access_token"] == "TOK" and data["language_type"] == "JAP"
        return _Resp({"words_result": [{"words": "東京", "probability": {"average": 0.97},
                                        "location": {"left": 10, "top": 5, "width": 20, "height": 10}}]})
    monkeypatch.setattr(httpx, "post", post)
    texts, boxes, scores = ocr_remote._baidu(str(img), "ja")
    assert texts == ["東京"] and scores == [0.97]
    assert boxes == [[[20, 10], [60, 10], [60, 30], [20, 30]]]   # x2: image was halved


def test_baidu_error_raises(tmp_path, monkeypatch):
    import httpx
    import pytest
    from PIL import Image
    img = tmp_path / "s.png"
    Image.new("RGB", (50, 50), "white").save(img)
    monkeypatch.setattr("core.api_keys.load_api_key_for_model", lambda n: "k")
    ocr_remote._baidu_token.update({"value": "TOK", "for": "k", "expires": 9e12})
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp({"error_code": 17, "error_msg": "Open api daily request limit reached"}))
    with pytest.raises(RuntimeError, match="17"):
        ocr_remote._baidu(str(img), "zh")


def test_vlm_reply_parser():
    assert ocr_remote._parse_ids('```json\n{"1": "你好", "2": "", "3": " x "}\n```', 3) == ["你好", "", "x"]
    assert ocr_remote._parse_ids("sorry, no", 2) == ["", ""]


def test_local_is_default(monkeypatch):
    _cfg(monkeypatch)
    assert ocr_remote.selected_engine() == "local"
    _cfg(monkeypatch, ocr_engine="nonsense")
    assert ocr_remote.selected_engine() == "local"
