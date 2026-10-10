"""The local vision model (Ollama): prompt, answer format and HTTP calls.

Pictures go only to the configured Ollama ([vision] url, this machine by default). The model
answers with a fixed JSON structure (Ollama structured output), checked and capped by `parse`.
"""

from __future__ import annotations

import base64
import http.client
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# No proxy, ever: a proxy configured in Windows or the environment must neither receive the pictures
# nor make the local Ollama look unreachable.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

DESCRIBE_VERSION = 1   # bump when the prompt or the passage format changes: every picture is described again
TYPES = ("photo", "schéma", "capture d'écran", "dessin technique", "graphique", "tableau",
         "page de texte", "logo ou décoration")
LOGO = "logo ou décoration"
MAX_DESCRIPTION = 1000
MAX_VISIBLE = 500
MAX_KEYWORDS = 12
MAX_KEYWORD_CHARS = 40

SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(TYPES)},
        "description": {"type": "string"},
        "texte_visible": {"type": "string"},
        "mots_cles": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["type", "description", "texte_visible", "mots_cles"],
}

SYSTEM_PROMPT = """Tu décris des images pour le moteur de recherche documentaire de Demaurex, fabricant suisse de \
lignes d'emballage robotisées (robots Delta, préhenseurs, convoyeurs, dépileurs, formeuses de boîtes, \
interfaces opérateur). Réponds uniquement en JSON selon le schéma imposé.
Règles :
- Décris seulement ce qui est visible dans l'image.
- Le contexte sert uniquement à nommer correctement les machines et les pièces : n'en recopie pas le \
contenu et n'invente rien qui n'est pas visible.
- Si tu n'es pas sûr, écris « semble être ».
- Ne recopie un nombre ou un texte que s'il est lisible.
- Pas d'adjectifs publicitaires.
Champs :
- type : la nature de l'image.
- description : en français. Pour une photo, 2 à 5 phrases (quoi, quelles parties, quelle action ou \
quel état). Pour un schéma, un dessin technique, un tableau, un graphique ou une capture d'écran : les \
éléments, libellés, valeurs, flux, boutons et onglets, jusqu'à 10 lignes.
- texte_visible : le texte lisible dans l'image, recopié tel quel (500 caractères au plus), vide s'il \
n'y en a pas.
- mots_cles : 5 à 12 mots-clés utiles pour la recherche, en français et en anglais."""


class VisionError(Exception):
    """A call that gave no usable description: timeout, HTTP error, Ollama gone, invalid answer."""

    def __init__(self, message: str, timeout: bool = False):
        super().__init__(message)
        self.timeout = timeout


@dataclass
class Description:
    type: str
    description: str
    visible_text: str
    keywords: list[str] = field(default_factory=list)
    seconds: float = 0.0


def _post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def unavailable(url: str, model: str) -> str | None:
    """Why the model cannot be used now (Ollama not answering, model not installed), or None."""
    try:
        with _OPENER.open(url.rstrip("/") + "/api/tags", timeout=10) as r:
            names = {m.get("name") for m in json.loads(r.read().decode("utf-8")).get("models", [])}
    except Exception as e:  # noqa: BLE001 - not running, refused, wrong address
        return (f"Ollama does not answer at {url} ({type(e).__name__}: {e}). Start Ollama on this "
                "machine, or set [vision] url.")
    if model not in names and f"{model}:latest" not in names:
        return f"The vision model {model} is not installed in Ollama: run `ollama pull {model}` on this machine."
    return None


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split()) if "\n" not in text else text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    return (cut[:cut.rfind(" ")] if " " in cut[limit // 2:] else cut).rstrip() + "…"


def parse(content: str) -> Description:
    """The model's JSON answer, checked and capped so that one occurrence is one passage."""
    try:
        d = json.loads(content)
    except ValueError:
        raise VisionError("invalid JSON answer: " + str(content)[:200]) from None
    if not isinstance(d, dict) or not isinstance(d.get("description"), str) or not d["description"].strip():
        raise VisionError("answer without a description")
    kind = str(d.get("type") or "").strip()[:30] or "image"
    keywords, seen = [], set()
    for k in d.get("mots_cles") or []:
        k = " ".join(str(k).split())[:MAX_KEYWORD_CHARS]
        if k and k.lower() not in seen:
            seen.add(k.lower())
            keywords.append(k)
    visible = str(d.get("texte_visible") or "").strip()[:MAX_VISIBLE]
    return Description(kind, _cut(d["description"], MAX_DESCRIPTION), visible, keywords[:MAX_KEYWORDS])


def describe_image(url: str, model: str, image: bytes, context: str, timeout: float) -> Description:
    """One picture described by the model. Raises VisionError (timeout=True for a timeout)."""
    body = {"model": model, "stream": False, "think": False, "format": SCHEMA, "keep_alive": "10m",
            "options": {"temperature": 0, "num_ctx": 8192},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": f"Contexte :\n{context}\n\nDécris l'image.",
                          "images": [base64.b64encode(image).decode("ascii")]}]}
    endpoint = url.rstrip("/") + "/api/chat"
    started = time.perf_counter()
    try:
        try:
            out = _post(endpoint, body, timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code != 400 or "think" not in detail.lower():
                raise VisionError(f"HTTP {e.code}: {detail[:200]}") from None
            body.pop("think")  # a model without a thinking switch
            out = _post(endpoint, body, timeout)
    except VisionError:
        raise
    except urllib.error.HTTPError as e:
        raise VisionError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from None
    except TimeoutError:
        raise VisionError(f"no answer within {timeout:.0f} s", timeout=True) from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise VisionError(f"no answer within {timeout:.0f} s", timeout=True) from None
        raise VisionError(f"Ollama not reachable at {url}: {e.reason}") from None
    except (OSError, ValueError, http.client.HTTPException) as e:  # e.g. IncompleteRead, invalid JSON body
        raise VisionError(f"{type(e).__name__}: {e}"[:200]) from None
    message = out.get("message") if isinstance(out, dict) else None
    if not isinstance(message, dict):
        raise VisionError("unexpected answer from Ollama: " + json.dumps(out)[:200])
    desc = parse(message.get("content") or "")
    desc.seconds = time.perf_counter() - started
    return desc


def unload(url: str, model: str) -> None:
    """Free the GPU memory now (the embedding step needs it); errors are ignored."""
    try:
        _post(url.rstrip("/") + "/api/generate", {"model": model, "keep_alive": 0}, 60)
    except Exception:  # noqa: BLE001
        pass


def passage_text(desc: Description, marker: str, file_name: str | None = None, folders: str | None = None) -> str:
    """The searchable passage of one picture occurrence ('[Image 3 — photo] ...'). A picture file's
    passage starts with its name and its two parent folders, which are searchable too."""
    where = ""
    if file_name:
        where = f"Fichier : {file_name}" + (f" (dossiers : {folders})" if folders else "") + ". "
    first = f"[{marker} — {desc.type}] " + where + desc.description
    lines = [first, "Texte visible : " + (desc.visible_text or "—")]
    if desc.keywords:
        lines.append("Mots-clés : " + ", ".join(desc.keywords))
    return "\n".join(lines)
