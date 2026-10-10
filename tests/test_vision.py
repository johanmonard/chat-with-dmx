import base64
import json
import socket

import pytest

from dmx_docs import vision
from fake_ollama import answer


def free_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def test_unavailable_reasons(fake_ollama):
    assert "does not answer" in vision.unavailable(free_port_url(), "test-vision")
    assert "ollama pull other:7b" in vision.unavailable(fake_ollama.url, "other:7b")
    assert vision.unavailable(fake_ollama.url, "test-vision") is None
    fake_ollama.models = ["gemma4:latest"]
    assert vision.unavailable(fake_ollama.url, "gemma4") is None   # 'gemma4' means 'gemma4:latest'


def test_describe_image_request_and_answer(fake_ollama):
    d = vision.describe_image(fake_ollama.url, "test-vision", b"PNGDATA", "Document : Manuel", timeout=10)
    assert (d.type, d.description) == ("photo", "Photo d'un préhenseur à ventouses sur un robot Delta.")
    assert d.keywords == ["préhenseur", "ventouses", "gripper", "suction cups"] and d.seconds >= 0
    body = fake_ollama.calls[0]
    assert body["model"] == "test-vision" and body["stream"] is False and body["think"] is False
    assert body["format"] == vision.SCHEMA and body["options"]["temperature"] == 0
    assert body["messages"][0] == {"role": "system", "content": vision.SYSTEM_PROMPT}
    user = body["messages"][1]
    assert "Document : Manuel" in user["content"] and user["images"] == [base64.b64encode(b"PNGDATA").decode()]


def test_model_without_thinking_switch_is_asked_again_without_it(fake_ollama):
    def reply(body):
        if "think" in body:
            return 400, {"error": '"test-vision" does not support thinking'}
        return answer()
    fake_ollama.reply = reply
    d = vision.describe_image(fake_ollama.url, "test-vision", b"x", "c", timeout=10)
    assert d.type == "photo" and len(fake_ollama.calls) == 2 and "think" not in fake_ollama.calls[1]


def test_invalid_answers_and_http_errors_raise(fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": "pas du JSON"}})
    with pytest.raises(vision.VisionError, match="invalid JSON"):
        vision.describe_image(fake_ollama.url, "test-vision", b"x", "c", timeout=10)
    fake_ollama.reply = lambda body: answer(description="  ")
    with pytest.raises(vision.VisionError, match="without a description"):
        vision.describe_image(fake_ollama.url, "test-vision", b"x", "c", timeout=10)
    fake_ollama.reply = lambda body: (500, {"error": "model crashed"})
    with pytest.raises(vision.VisionError, match="HTTP 500") as e:
        vision.describe_image(fake_ollama.url, "test-vision", b"x", "c", timeout=10)
    assert e.value.timeout is False


def test_timeout_raises_a_timeout_error(fake_ollama):
    fake_ollama.delay = 2
    with pytest.raises(vision.VisionError) as e:
        vision.describe_image(fake_ollama.url, "test-vision", b"x", "c", timeout=0.5)
    assert e.value.timeout is True


def test_parse_caps_and_cleans():
    content = json.dumps({"type": "capture d'écran", "description": "mot " * 400, "texte_visible": "V" * 900,
                          "mots_cles": ["Dépileur", "dépileur", " denester ", ""] + [f"k{i}" for i in range(20)]})
    d = vision.parse(content)
    assert d.type == "capture d'écran"
    assert len(d.description) <= vision.MAX_DESCRIPTION and d.description.endswith("…")
    assert len(d.visible_text) == vision.MAX_VISIBLE
    assert d.keywords[:2] == ["Dépileur", "denester"] and len(d.keywords) == vision.MAX_KEYWORDS
    assert vision.parse(json.dumps({"type": "", "description": "x", "texte_visible": "", "mots_cles": []})).type == "image"


def test_passage_text():
    d = vision.Description("photo", "Main tenant un outil.", "", ["dépileur", "denester"])
    assert vision.passage_text(d, "Image 3") == (
        "[Image 3 — photo] Main tenant un outil.\nTexte visible : —\nMots-clés : dépileur, denester")
    assert vision.passage_text(d, "Image 1", file_name="Outil.jpg").startswith(
        "[Image 1 — photo] Fichier : Outil.jpg. Main tenant un outil.")


def test_unload_is_quiet(fake_ollama):
    vision.unload(fake_ollama.url, "test-vision")
    assert fake_ollama.unloaded == ["test-vision"]
    vision.unload(free_port_url(), "test-vision")   # no exception when Ollama is gone
