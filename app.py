# app.py
# Belle Flora SEO Optimizer – compact & efficiënt
# Nieuwste wijzigingen:
# • Heroicons-injectie gefikst: bredere regex + pinned CDN (unpkg) + set-keuze (outline/solid).
# • Idempotent: geen dubbele iconen. Water-icoon wordt nu consequent getoond.
# • Alle eerdere features behouden (annuleren, transactiefocus, naamkoppelingen, metafields autodetect+mirror, cm-fixes, “– in pot”, streaming).

import os, re, json, time, html, textwrap
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4
from threading import Lock

import requests
from flask import Flask, Response, jsonify, redirect, request, session

# =========================
# Config
# =========================

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(32))

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "michiel")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "CHANGE_ME")
SHOPIFY_STORE_DOMAIN = os.environ.get("SHOPIFY_STORE_DOMAIN", "your-store.myshopify.com").strip()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("DEFAULT_MODEL", "gpt-4o-mini")
OPENAI_TEMP = float(os.environ.get("DEFAULT_TEMPERATURE", "0.7"))
OPENAI_RETRIES = int(os.environ.get("OPENAI_MAX_RETRIES", "4"))

BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "8"))
DELAY_PER_PRODUCT = float(os.environ.get("DELAY_SECONDS", "2.5"))
SHOPIFY_RETRIES = int(os.environ.get("SHOPIFY_MAX_RETRIES", "4"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "60"))

# Merknaam / meta-suffix
BRAND_NAME = os.environ.get("BRAND_NAME", "Belle Flora").strip()
META_SUFFIX = f" | {BRAND_NAME}"
META_TITLE_LIMIT = int(os.environ.get("META_TITLE_LIMIT", "60"))
META_DESC_LIMIT  = int(os.environ.get("META_DESC_LIMIT", "155"))

# Heroicons configuratie — gepind zodat droplet.svg altijd bestaat
HEROICON_CDN  = os.environ.get("HEROICON_CDN", "https://unpkg.com/heroicons@2.1.5").rstrip("/")
HEROICON_SET  = os.environ.get("HEROICON_SET", "24/outline").strip()  # bv. "24/solid"
HEROICON_SIZE = int(os.environ.get("HEROICON_SIZE", "20"))

# Metafields autodetect/mirroring
META_NAMESPACE_DEFAULT = os.environ.get("META_NAMESPACE_DEFAULT", "specs")
META_HEIGHT_KEYS_HINT = [s for s in os.environ.get("META_HEIGHT_KEYS_HINT", "hoogte,height").split(",") if s]
META_DIAM_KEYS_HINT   = [s for s in os.environ.get("META_DIAM_KEYS_HINT", "diameter,pot,ø,⌀").split(",") if s]
META_MIRROR_MAX_HEIGHT = int(os.environ.get("META_MIRROR_MAX_HEIGHT", "2"))
META_MIRROR_MAX_DIAM   = int(os.environ.get("META_MIRROR_MAX_DIAM", "1"))
META_DEBUG_MAPPING     = os.environ.get("META_DEBUG_MAPPING", "1").lower() not in ("0","false","no")

# Transactiefocus
TRANSACTIONAL_MODE = os.environ.get("TRANSACTIONAL_MODE", "0").lower() not in ("0","false","no")
TRANSACTIONAL_CLAIMS = [s.strip() for s in os.environ.get(
    "TRANSACTIONAL_CLAIMS",
    "Gratis verzending vanaf €49|Vandaag besteld, snel in huis|30 dagen retour|Lokale kweker|Verse kwaliteit"
).split("|") if s.strip()]

# Standaard NL→Latijn koppelingen
NAME_MAP_DEFAULT = os.environ.get(
    "NAME_MAP_DEFAULT",
    "paradijsvogelplant=Strelitzia|flamingoplant=Anthurium|slaapplant=Calathea|gatenplant=Monstera|"
    "olifantsoor=Alocasia|hartbladige klimmer=Philodendron|vrouwentong=Sansevieria|"
    "vioolbladplant=Ficus lyrata|drakenboom=Dracaena|zz-plant=Zamioculcas zamiifolia|"
    "pannenkoekenplant=Pilea peperomioides|lepelplant=Spathiphyllum wallisii|rubberplant=Ficus elastica|"
    "treurvijg=Ficus benjamina|ficus ginseng=Ficus microcarpa|luchtplantje=Tillandsia|graslelie=Chlorophytum comosum|"
    "arecapalm=Dypsis lutescens|kentia=Howea forsteriana|kamerpalm=Chamaedorea elegans|dwergdadelpalm=Phoenix roebelenii|"
    "bamboepalm=Rhapis excelsa|vissenstaartpalm=Caryota mitis|olifantspoot=Beaucarnea recurvata|jadeplant=Crassula ovata|"
    "peperomia watermeloen=Peperomia argyreia|mozaïekplant=Fittonia albivenis|gebedplant=Maranta leuconeura|"
    "schildpadplantje=Peperomia prostrata|string of hearts=Ceropegia woodii|erwtenplantje=Senecio rowleyanus|"
    "dolfijnenplant=Senecio peregrinus|koraalcactus=Rhipsalis|kerstcactus=Schlumbergera|klimop=Hedera helix|"
    "sierasperge=Asparagus setaceus|wasbloem=Hoya carnosa|goudrank=Epipremnum aureum|zilverrank=Scindapsus pictus|"
    "klaverplant=Oxalis triangularis|krulvaren=Nephrolepis exaltata|vogelnestvaren=Asplenium nidus|venushaar=Adiantum raddianum|"
    "geweihvaren=Platycerium bifurcatum|alocasia polly=Alocasia amazonica|pijlplant=Syngonium podophyllum|"
    "ezelsstaart=Sedum morganianum|kalanchoë=Kalanchoe blossfeldiana|koffieplant=Coffea arabica|olijfboom=Olea europaea|"
    "laurier=Laurus nobilis|oleander=Nerium oleander|Chinese roos=Hibiscus rosa-sinensis|kaapse jasmijn=Gardenia jasminoides|"
    "kamerjasmijn=Jasminum polyanthum|bruidsbloem=Stephanotis floribunda|vingerboom=Schefflera arboricola|"
    "parapluplant=Schefflera arboricola|vingerplant=Fatsia japonica|kameraralia=Polyscias scutellaria|clusia=Clusia rosea|"
    "stromanthe=Stromanthe sanguinea|aloë vera=Aloe barbadensis"
)

# =========================
# Globals
# =========================

REQ = requests.Session()
CANCEL_FLAGS: Dict[str, bool] = {}
CANCEL_LOCK = Lock()
_META_MAP_CACHE: Dict[str, Dict[str, Any]] = {}

# =========================
# Utilities
# =========================

def _cancel_start(jid: str) -> None:
    with CANCEL_LOCK:
        CANCEL_FLAGS[jid] = False

def _cancel_set(jid: str) -> None:
    with CANCEL_LOCK:
        if jid in CANCEL_FLAGS:
            CANCEL_FLAGS[jid] = True

def _cancel_check(jid: str) -> bool:
    with CANCEL_LOCK:
        return CANCEL_FLAGS.get(jid, False)

def _cancel_end(jid: str) -> None:
    with CANCEL_LOCK:
        CANCEL_FLAGS.pop(jid, None)

def _shopify_headers(token: str) -> Dict[str, str]:
    return {"X-Shopify-Access-Token": token, "Content-Type": "application/json", "Accept": "application/json"}

def _get(url: str, token: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
    for i in range(SHOPIFY_RETRIES):
        r = REQ.get(url, headers=_shopify_headers(token), params=params or {}, timeout=REQUEST_TIMEOUT)
        if r.status_code == 429 and i < SHOPIFY_RETRIES - 1:
            time.sleep(float(r.headers.get("Retry-After", 2 ** i))); continue
        r.raise_for_status(); return r
    r.raise_for_status(); return r

def _post(url: str, token: str, json_body: Dict[str, Any]) -> Dict[str, Any]:
    for i in range(SHOPIFY_RETRIES):
        r = REQ.post(url, headers=_shopify_headers(token), json=json_body, timeout=REQUEST_TIMEOUT)
        if r.status_code == 429 and i < SHOPIFY_RETRIES - 1:
            time.sleep(float(r.headers.get("Retry-After", 2 ** i))); continue
        r.raise_for_status(); return r.json()
    r.raise_for_status(); return r.json()

def _paged(path: str, token: str, params: Optional[Dict[str, Any]] = None, store: Optional[str] = None) -> List[Dict[str, Any]]:
    store_domain = store or SHOPIFY_STORE_DOMAIN
    p = dict(params or {}); p["limit"] = min(int(p.get("limit", 250)), 250)
    since = 0; out: List[Dict[str, Any]] = []
    while True:
        p["since_id"] = since
        url = f"https://{store_domain}{path}"
        data = _get(url, token, params=p).json()
        key = next((k for k in ("custom_collections", "smart_collections", "products", "collects") if k in data), None)
        if not key: break
        items = data.get(key, [])
        if not items: break
        out.extend(items)
        since = items[-1]["id"]
        if len(items) < p["limit"]: break
    return out

# =========================
# OpenAI
# =========================

def _openai_chat(system_prompt: str, user_prompt: str, model: str = OPENAI_MODEL, temperature: float = OPENAI_TEMP) -> str:
    if not OPENAI_API_KEY: raise RuntimeError("OPENAI_API_KEY ontbreekt.")
    url = "https://api.openai.com/v1/chat/completions"
    body = {"model": model, "temperature": temperature, "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]}
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    for i in range(OPENAI_RETRIES):
        try:
            r = REQ.post(url, headers=headers, json=body, timeout=120)
            if r.status_code == 429 and i < OPENAI_RETRIES - 1:
                time.sleep(2 ** i); continue
            r.raise_for_status(); return r.json()["choices"][0]["message"]["content"]
        except requests.HTTPError:
            if r is not None and r.status_code == 429 and i < OPENAI_RETRIES - 1:
                time.sleep(2 ** i); continue
            raise RuntimeError(f"OpenAI HTTP {getattr(r,'status_code','?')}: {getattr(r,'text','')}")
        except Exception as ex:
            if i < OPENAI_RETRIES - 1: time.sleep(2 ** i); continue
            raise RuntimeError(f"OpenAI call failed: {ex}")

# =========================
# Prompts
# =========================

def _build_system_prompt(txn: bool, usps: List[str], name_map: Dict[str, str]) -> str:
    nm_lines = "\n".join([f"  • {k} → {v}" for k, v in name_map.items()]) if name_map else ""
    usps_str = " • ".join(usps) if usps else ""
    txn_block = ""
    if txn:
        txn_block = (
            "TRANSACTIONELE META-RICHTLIJNEN:\n"
            f"  • Meta title ≤{META_TITLE_LIMIT}: start met koopwoord + product, eindig met '| {BRAND_NAME}' indien ruimte.\n"
            f"  • Meta description ≤{META_DESC_LIMIT}: voeg 1–2 USP's toe en een subtiele CTA. USP-lijst: {usps_str}\n\n"
        )
    return (
        "Je bent een ervaren NL SEO-copywriter voor planten (Belle Flora). Schrijf natuurlijk, feitelijk, klantgericht.\n\n"
        "TITEL:\n"
        "  • Bij voorkeur: [NL-naam] / [Latijn] – ↕[hoogte cm] – ⌀[pot cm].\n"
        "  • Als product een sierpot heeft: voeg toe: '– in [kleur] pot' (of '– in pot' zonder kleur, nooit raden).\n\n"
        "BESCHRIJVING (HTML):\n"
        "  <h3>Beschrijving</h3><p>…</p>\n"
        "  <h3>Eigenschappen & behoeften</h3>\n"
        "  <p><strong>Lichtbehoefte</strong>: …</p>\n"
        "  <p><strong>Waterbehoefte</strong>: …</p>\n"
        "  <p><strong>Standplaats</strong>: …</p>\n"
        "  <p><strong>Giftigheid</strong>: …</p>\n"
        "  • Géén bullets, géén emoji. Alleen eenvoudige HTML: <h3>, <p>, <strong>, <em>.\n\n"
        "SEO:\n"
        "  • Lever Meta title en Meta description binnen de lengte-limieten. Iedere tekst uniek.\n\n"
        f"NAAMCONSISTENTIE (toepassen wanneer relevant):\n{nm_lines}\n\n"
        f"{txn_block}"
        "OUTPUT (exacte labels):\n"
        "Nieuwe titel: …\n\n"
        "Beschrijving: … (HTML)\n\n"
        "Meta title: …\n"
        "Meta description: …\n"
    )

# =========================
# Parsing & normalisatie
# =========================

RE_CM_RANGE       = re.compile(r"\b(\d{1,3})\s*[-–]\s*(\d{1,3})\s*cm\b", re.I)
RE_HEIGHT_LABEL   = re.compile(r"(?:↕|hoogte)\s*[:=]?\s*(\d{1,3})\s*cm\b", re.I)
RE_DIAM_LABEL     = re.compile(r"(?:⌀|Ø|ø|diameter|doorsnede|pot\s*maat|potmaat|pot\s*diameter|potdiameter)\s*[:=]?\s*(\d{1,3})\s*cm?\b", re.I)
RE_DIAM_SYMBOL    = re.compile(r"[⌀Øø]\s*(\d{1,3})\s*cm?\b", re.I)
RE_CM_ALL         = re.compile(r"\b(\d{1,3})\s*cm\b", re.I)

COLOR_RE = r"(?:wit|witte|zwart|zwarte|grijs|grijze|lichtgrijs|lichtgrijze|donkergrijs|donkergrijze|antraciet|antracietgrijs|antracietgrijze|beige|taupe|terra(?:cotta)?|terracotta|bruin|bruine|groen|groene|lichtgroen|lichtgroene|donkergroen|donkergroene|blauw|blauwe|rood|rode|roze|paars|paarse|geel|gele|oranje|cr[eè]me|cr[eè]mekleur|goud|gouden|zilver|zilveren|koper|koperen|brons|bronzen)"
RE_IN_COLOR_POT   = re.compile(r"\bin\s+(?P<color>"+COLOR_RE+r")\s+pot\b", re.I)
RE_COLOR_POT      = re.compile(r"\b(?P<color>"+COLOR_RE+r")\s+pot\b", re.I)
RE_POT_COLOR      = re.compile(r"\bpot(?:\s*[:\-]?\s*)(?P<color>"+COLOR_RE+r")\b", re.I)

POT_PRESENCE = [
    re.compile(r"\bin\s+(?:een\s+)?pot\b", re.I),
    re.compile(r"\bmet\s+(?:een\s+)?(?:sier)?pot\b", re.I),
    re.compile(r"\bsierpot\b", re.I),
    re.compile(r"\bdecor(?:atieve)?\s*pot\b", re.I),
    re.compile(r"\bcoverpot\b", re.I),
    re.compile(r"\bcachepot\b", re.I),
    re.compile(r"\bplanter\b", re.I),
]

def _html_to_text(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "", flags=re.I)

def parse_dimensions(title: str, body_html: str) -> Dict[str, str]:
    text = f"{title or ''}\n{_html_to_text(body_html)}"
    height = None; diam = None
    m = RE_HEIGHT_LABEL.search(text)
    if m: height = int(m.group(1))
    else:
        mr = RE_CM_RANGE.search(text)
        if mr:
            a, b = int(mr.group(1)), int(mr.group(2)); height = round((a + b) / 2)
    md = RE_DIAM_LABEL.search(text) or RE_DIAM_SYMBOL.search(text)
    if md: diam = int(md.group(1))
    nums = [int(n) for n in RE_CM_ALL.findall(text)]
    if len(nums) >= 2:
        hi, lo = max(nums), min(nums)
        if height is None: height = hi
        if diam   is None: diam   = lo
    if diam is not None and height is not None and diam == height and len(set(nums)) >= 2:
        for v in sorted(set(nums)):
            if v != height: diam = v; break
    out: Dict[str, str] = {}
    if height is not None: out["height_cm"] = str(height)
    if diam   is not None: out["pot_diameter_cm"] = str(diam)
    return out

def extract_pot_color(title: str, body_html: str) -> Optional[str]:
    text = f"{title or ''}\n{_html_to_text(body_html)}"
    for rx in (RE_IN_COLOR_POT, RE_COLOR_POT, RE_POT_COLOR):
        m = rx.search(text)
        if m: return m.group("color").strip()
    return None

def detect_pot_presence(title: str, body_html: str) -> bool:
    if extract_pot_color(title, body_html): return True
    text = f"{title or ''}\n{_html_to_text(body_html)}"
    return any(rx.search(text) for rx in POT_PRESENCE)

def normalize_title(title: str, dims: Dict[str, str], pot_color: Optional[str], pot_present: bool) -> str:
    t = title or ""
    # cm achter ↕/⌀ afdwingen
    t = re.sub(r"(↕\s*\d{1,3})(?!\s*cm)\b", r"\1cm", t)
    t = re.sub(r"([⌀Øø]\s*\d{1,3})(?!\s*cm)\b", r"\1cm", t)
    h = dims.get("height_cm"); d = dims.get("pot_diameter_cm")
    if h and not re.search(r"↕\s*\d{1,3}\s*cm", t):
        if re.search(r"[⌀Øø]\s*\d{1,3}\s*cm", t):
            t = re.sub(r"([⌀Øø]\s*\d{1,3}\s*cm)", f"↕{int(h)}cm – \\1", t, count=1)
        else:
            t = (t.strip() + f" – ↕{int(h)}cm").strip()
    if d and not re.search(r"[⌀Øø]\s*\d{1,3}\s*cm", t):
        t = (t.strip() + f" – ⌀{int(d)}cm").strip()
    # pot-suffix
    has_pot_suffix = re.search(r"\bin\s+[^\n]*\bpot\b", t, re.I) is not None
    if pot_color and not has_pot_suffix: t += f" – in {pot_color} pot"
    elif pot_present and not has_pot_suffix: t += " – in pot"
    return t.strip()

def parse_name_map_text(txt: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in re.split(r"[|\n]+", txt or ""):
        if not raw.strip() or raw.strip().startswith("#") or "=" not in raw: continue
        nl, la = raw.split("=", 1); out[nl.strip().lower()] = la.strip()
    return out

def enforce_title_name_map(title: str, name_map: Dict[str, str]) -> str:
    if not title or not name_map: return title
    m = re.match(r"^\s*([^/\n]+?)\s*/\s*([^–—\-]+?)\s*(?:–|—|-)\s*(.*)$", title)
    if not m: return title
    nl = m.group(1).strip(); lat = m.group(2).strip(); rest = m.group(3)
    key = nl.lower()
    for k, v in name_map.items():
        if k in key and lat != v:
            return f"{nl} / {v}" + (f" – {rest}" if rest else "")
    return title

def split_ai_output(text: str) -> Dict[str, str]:
    lines = [l.rstrip() for l in (text or "").splitlines()]
    blob = "\n".join(lines)
    def find(marks: List[str]) -> str:
        for m in marks:
            if m and m.lower() in blob.lower(): return m
        return ""
    marks = {
        "title": find(["Nieuwe titel:", "Titel:", "SEO titel:", "SEO-titel:"]),
        "body": find(["Beschrijving:", "Body:", "Productbeschrijving:", "Gestandaardiseerde beschrijving:"]),
        "meta_title": find(["Meta title:", "SEO-meta title:", "Title tag:"]),
        "meta_desc": find(["Meta description:", "SEO-meta description:", "Description tag:"]),
    }
    def extract(start: str, enders: List[str]) -> str:
        if not start: return ""
        s = blob.lower().find(start.lower())
        if s == -1: return ""
        s += len(start)
        ends = [blob.lower().find(e.lower(), s) for e in enders if e]; ends = [p for p in ends if p != -1]
        e = min(ends) if ends else len(blob)
        return blob[s:e].strip().strip("-: ").strip()
    title = extract(marks["title"], [marks["body"], marks["meta_title"], marks["meta_desc"]])
    body  = extract(marks["body"],  [marks["meta_title"], marks["meta_desc"]])
    meta_title = extract(marks["meta_title"], [marks["meta_desc"]])
    meta_desc  = extract(marks["meta_desc"],  [])
    if not any([title, body, meta_title, meta_desc]):
        parts = [p.strip() for p in re.split(r"\n\s*\n", blob) if p.strip()]
        title = parts[0] if len(parts) > 0 else ""
        body  = parts[1] if len(parts) > 1 else ""
        meta_title = parts[2] if len(parts) > 2 else title
        meta_desc  = parts[3] if len(parts) > 3 else (body or title)
    # Fallback body zonder emoji; iconen server-side via injectie
    if body and not re.search(r"</?(p|h3|strong|em|br)\b", body, flags=re.I):
        safe = html.escape(body)
        body = ("<h3>Beschrijving</h3>\n"
                f"<p>{safe}</p>\n"
                "<h3>Eigenschappen & behoeften</h3>\n"
                "<p><strong>Lichtbehoefte</strong>: Onbekend</p>\n"
                "<p><strong>Waterbehoefte</strong>: Onbekend</p>\n"
                "<p><strong>Standplaats</strong>: Onbekend</p>\n"
                "<p><strong>Giftigheid</strong>: Onbekend</p>")
    return {"title": title, "body_html": body, "meta_title": meta_title, "meta_description": meta_desc}

# ---------- Meta finalizers ----------

_BREAK_RE = re.compile(r"[ \u00A0\u2009\u200A\u200B\u202F\-–—·•,:;]")

def _trim_to_limit(text: str, limit: int) -> str:
    if len(text) <= limit: return text
    cut = text[:limit]; m = list(_BREAK_RE.finditer(cut))
    if m: cut = cut[:m[-1].start()]
    return cut.rstrip(" -–—·|")

def finalize_meta_title(raw: str, title_fallback: str, limit: int = META_TITLE_LIMIT,
                        brand: str = BRAND_NAME, suffix: str = META_SUFFIX) -> str:
    base = (raw or title_fallback or "").strip()
    if not base: return (brand if len(brand) <= limit else brand[:limit])
    partial = re.compile(rf"\s*\|\s*{re.escape(brand[:-1])}[a-zA-Z]?\s*$")
    if not base.endswith(suffix):
        base = partial.sub("", base)
    if base.endswith(suffix):
        if len(base) <= limit: return base
        prefix = base[:-len(suffix)].rstrip(" -–—|")
        prefix = _trim_to_limit(prefix, max(0, limit - len(suffix)))
        return (prefix + suffix)[:limit]
    else:
        if f" {brand}" in base or brand in base:
            return _trim_to_limit(base, limit)
        if len(base) + len(suffix) <= limit:
            return base + suffix
        prefix = _trim_to_limit(base, max(0, limit - len(suffix)))
        if not prefix: return _trim_to_limit(brand, limit)
        return (prefix + suffix)[:limit]

def finalize_meta_desc(raw: str, body_fallback: str, title_fallback: str, limit: int = META_DESC_LIMIT) -> str:
    text = (raw or body_fallback or title_fallback or "").strip()
    if len(text) <= limit: return text
    return _trim_to_limit(text, limit)

# ---------- Heroicons injectie (robust & idempotent) ----------

def _icon_img(name: str) -> str:
    src = f'{HEROICON_CDN}/{HEROICON_SET}/{name}.svg'
    return (
        f'<img class="bf-icon" data-heroicon="{name}" '
        f'src="{src}" alt="" width="{HEROICON_SIZE}" height="{HEROICON_SIZE}" '
        'style="vertical-align:text-bottom;margin-right:6px;opacity:.9" loading="lazy" />'
    )

ICON_MAP = {
    "licht":  _icon_img("sun"),
    "water":  _icon_img("droplet"),  # gefikst pad/naam
    "plaats": _icon_img("home"),
    "giftig": _icon_img("exclamation-triangle"),
}

def inject_heroicons(body_html: str) -> str:
    """
    Voeg vóór de 4 regels steeds een Heroicon <img> toe.
    - Stript eventuele bestaande emoji/img vóór het label.
    - Idempotent: als .bf-icon of data-heroicon al aanwezig is, laat staan.
    """
    if not body_html:
        return body_html
    if 'class="bf-icon"' in body_html or 'data-heroicon=' in body_html:
        return body_html

    out = body_html

    def _inject(label: str, icon_html: str, html: str) -> str:
        # <p> [rommel] (<strong>)?Label(</strong>)? : rest </p>
        rx = re.compile(
            rf'(<p[^>]*>\s*)'
            rf'(?:<img[^>]*>\s*|[\u2600-\u27BF\u1F300-\u1FAFF\s]*?)*'
            rf'(?:(?:<strong>)\s*)?{label}\s*(?::)?\s*(?:</strong>)?\s*(.*?)(</p>)',
            re.I | re.S
        )
        def repl(m):
            pre, rest, end = m.group(1), m.group(2), m.group(3)
            return f'{pre}{icon_html}<strong>{label}</strong>: {rest}{end}'
        return rx.sub(repl, html)

    out = _inject("Lichtbehoefte", ICON_MAP["licht"], out)
    out = _inject("Waterbehoefte", ICON_MAP["water"], out)
    out = _inject("Standplaats",   ICON_MAP["plaats"], out)
    out = _inject("Giftigheid",    ICON_MAP["giftig"], out)

    return out

# =========================
# Shopify GraphQL helpers (metafields)
# =========================

def _gql_url(store_domain: str) -> str:
    return f"https://{store_domain}/admin/api/2025-01/graphql.json"

def _defs_for_product(token: str, store_domain: str) -> List[Dict[str, Any]]:
    query = """
    query defs {
      metafieldDefinitions(ownerType: PRODUCT, first: 250) {
        edges { node { name namespace key type { name } } }
      }
    }"""
    data = _post(_gql_url(store_domain), token, {"query": query})
    edges = (((data or {}).get("data") or {}).get("metafieldDefinitions") or {}).get("edges") or []
    return [e["node"] for e in edges if "node" in e]

def _rank_candidates(defs: List[Dict[str, Any]], hints: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    hints = [h.lower().strip() for h in hints]
    for d in defs:
        name = (d.get("name") or ""); key = (d.get("key") or ""); ns = (d.get("namespace") or "")
        lname, lkey = name.lower(), key.lower()
        if not any(h in lname or h in lkey for h in hints): continue
        tname = (((d.get("type") or {}).get("name")) or "single_line_text_field").lower()
        score = 0
        if "number_integer" in tname: score += 30
        elif "dimension" in tname:    score += 22
        elif "number_decimal" in tname: score += 18
        elif "single_line_text_field" in tname: score += 10
        if lkey in ("hoogte_cm", "hoogte"): score += 50
        if lkey in ("diameter_cm", "pot_diameter_cm", "diameter"): score += 50
        if ns.lower() == "custom": score += 2
        out.append({"namespace": ns, "key": key, "name": name,
                    "type": (d.get("type") or {}).get("name") or "single_line_text_field",
                    "score": score})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out

def _ensure_meta_map(token: str, store_domain: str) -> Dict[str, Any]:
    cache_key = store_domain
    if cache_key in _META_MAP_CACHE: return _META_MAP_CACHE[cache_key]
    defs = _defs_for_product(token, store_domain)
    h = _rank_candidates(defs, META_HEIGHT_KEYS_HINT or ["hoogte", "height"])
    d = _rank_candidates(defs, META_DIAM_KEYS_HINT  or ["diameter", "pot", "ø", "⌀"])
    if not h:
        h = [{"namespace": META_NAMESPACE_DEFAULT, "key": "height_cm", "name": "height_cm", "type": "single_line_text_field", "score": 0}]
    if not d:
        d = [{"namespace": META_NAMESPACE_DEFAULT, "key": "pot_diameter_cm", "name": "pot_diameter_cm", "type": "single_line_text_field", "score": 0}]
    mm = {"height_candidates": h, "diam_candidates": d,
          "height_ns": h[0]["namespace"], "height_key": h[0]["key"], "height_type": h[0]["type"],
          "diam_ns": d[0]["namespace"], "diam_key": d[0]["key"], "diam_type": d[0]["type"]}
    _META_MAP_CACHE[cache_key] = mm; return mm

def _encode_value(val_str: str, tname: str) -> str:
    t = (tname or "").lower()
    try:
        if t == "number_integer": return str(int(val_str))
        if t == "number_decimal": return str(float(val_str))
        if t == "dimension":      return json.dumps({"value": float(val_str), "unit": "cm"})
    except Exception:
        pass
    return str(val_str)

def _set_one(token: str, store_domain: str, gid: str, ns: str, key: str, tname: str, value: str) -> Tuple[bool, str]:
    mutation = """
    mutation setOne($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields { namespace key type value }
        userErrors { field message }
      }
    }"""
    payload = {"query": mutation, "variables": {"metafields": [{
        "ownerId": gid, "namespace": ns, "key": key, "type": tname, "value": _encode_value(value, tname)
    }]}}
    data = _post(_gql_url(store_domain), token, payload)
    ue = (data.get("data", {}).get("metafieldsSet", {}) or {}).get("userErrors", [])
    if ue: return False, (ue[0].get("message") or str(ue))
    return True, ""

def set_product_metafields(token: str, store_domain: str, product_id: int, values: Dict[str, str]) -> Dict[str, List[str]]:
    if not values: return {"height": [], "diam": []}
    mm = _ensure_meta_map(token, store_domain)
    gid = f"gid://shopify/Product/{int(product_id)}"
    written = {"height": [], "diam": []}
    if values.get("height_cm"):
        left = META_MIRROR_MAX_HEIGHT
        for cand in mm["height_candidates"]:
            ok, msg = _set_one(token, store_domain, gid, cand["namespace"], cand["key"], cand["type"], values["height_cm"])
            if ok:
                written["height"].append(f"{cand['namespace']}.{cand['key']} [{cand['type']}]"); left -= 1
                if left <= 0: break
            elif "Owner subtype does not match" in msg:
                continue
            else:
                raise RuntimeError(f"metafieldsSet (hoogte): {msg}")
    if values.get("pot_diameter_cm"):
        left = META_MIRROR_MAX_DIAM
        for cand in mm["diam_candidates"]:
            ok, msg = _set_one(token, store_domain, gid, cand["namespace"], cand["key"], cand["type"], values["pot_diameter_cm"])
            if ok:
                written["diam"].append(f"{cand['namespace']}.{cand['key']} [{cand['type']}]"); left -= 1
                if left <= 0: break
            elif "Owner subtype does not match" in msg:
                continue
            else:
                raise RuntimeError(f"metafieldsSet (diameter): {msg}")
    return written

def update_product_texts(store_domain: str, token: str, product_id: int,
                         new_title: str, new_body_html: str, seo_title: str, seo_desc: str) -> None:
    mutation = """
    mutation productSeoAndDesc($input: ProductInput!) {
      productUpdate(input: $input) {
        product { id }
        userErrors { field message }
      }
    }"""
    gid = f"gid://shopify/Product/{int(product_id)}"
    variables = {"input": {"id": gid, "title": new_title, "descriptionHtml": new_body_html,
                           "seo": {"title": seo_title or new_title, "description": seo_desc or ""}}}
    data = _post(_gql_url(store_domain), token, {"query": mutation, "variables": variables})
    errs = (data.get("data", {}).get("productUpdate", {}) or {}).get("userErrors", [])
    if errs: raise RuntimeError(f"Shopify productUpdate: {errs}")

# =========================
# Auth & UI
# =========================

def _require_login(fn):
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"): return redirect("/login")
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__; return wrapper

LOGIN_HTML = """<!doctype html><html lang="nl"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Inloggen – Belle Flora</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#0b1020;color:#eef;margin:0}
.card{max-width:820px;margin:48px auto;background:#121735;padding:24px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
h1{margin:0 0 12px 0}label{display:block;margin:12px 0 8px}
input{width:100%;padding:12px;border-radius:10px;border:1px solid #2a335a;background:#0f1430;color:#eef}
button{padding:12px 16px;border:0;border-radius:12px;background:#4f7dff;color:#fff;font-weight:600;cursor:pointer;margin-top:12px}
</style></head><body>
<div class="card">
  <h1>Inloggen</h1>
  <form method="post" action="/login">
    <label>Gebruikersnaam</label><input name="username" required />
    <label>Wachtwoord</label><input name="password" type="password" required />
    <button>Inloggen</button>
  </form>
</div></body></html>"""

DASHBOARD_HTML = """<!doctype html><html lang="nl"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Belle Flora SEO Optimizer</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#0b1020;color:#eef;margin:0}
.wrap{max-width:1100px;margin:28px auto;padding:0 16px}
.card{background:#121735;padding:20px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.35);margin-bottom:16px}
label{display:block;margin:10px 0 6px}
input,textarea,select{width:100%;padding:12px;border-radius:10px;border:1px solid #2a335a;background:#0f1430;color:#eef}
button{padding:12px 16px;border:0;border-radius:12px;background:#4f7dff;color:#fff;font-weight:600;cursor:pointer}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.pill{display:inline-block;padding:6px 10px;border-radius:999px;background:#243165;margin-left:8px}
pre{white-space:pre-wrap}
small{opacity:.85}
</style></head><body><div class="wrap">

<div class="card">
  <h1>Belle Flora SEO Optimizer</h1>
  <div class="row">
    <div><label>Store domein</label><input id="store" value="[[STORE]]" /></div>
    <div><label>Model (server-side)</label><input id="model" value="[[MODEL]]" /></div>
  </div>
  <div class="row">
    <div><label>Shopify Access Token</label><input id="token" placeholder="shpat_..." /></div>
    <div><label>Extra prompt (optioneel)</label><input id="prompt" placeholder="Extra richtlijnen..." /></div>
  </div>
  <div class="row">
    <div><label><input type="checkbox" id="txn"> Transactiefocus (koopwoorden in meta’s)</label></div>
    <div><label>USP’s (| gescheiden)</label><input id="txn_usps" value="[[TXNUSPS]]" /></div>
  </div>
  <div class="row">
    <div style="grid-column:1/-1">
      <label>Vaste naamkoppelingen NL → Latijn (één per regel; nl=Latijn)</label>
      <textarea id="name_map" rows="6">[[NAMEMAP]]</textarea>
    </div>
  </div>
  <div style="margin-top:10px">
    <button onclick="loadCollections()">Collecties laden</button>
    <span id="cstatus" class="pill">Nog niet geladen</span>
  </div>
</div>

<div class="card">
  <label>Selecteer collecties</label>
  <select id="collections" multiple size="10" style="height:220px"></select>
  <div style="margin-top:12px">
    <button id="btnRun" onclick="optimizeSelected()">Optimaliseer geselecteerde producten</button>
    <button id="btnCancel" onclick="cancelRun()" disabled>Annuleren</button>
  </div>
</div>

<div class="card">
  <small>Live status (batch=[[BATCH]], delay=[[DELAY]]s, model=server-side)</small>
  <pre id="status">Klaar om te starten…</pre>
</div>

</div>
<script>
const qs=s=>document.querySelector(s);
function setLog(t){qs('#status').textContent=t}
function addLog(t){qs('#status').textContent+='\\n'+t}
window.addEventListener('DOMContentLoaded',()=>{qs('#txn').checked=[[TXNCHK]];});
let RUN=false, abortCtrl=null, jobId=null;

async function loadCollections(){
  setLog('Collecties laden…');
  const res=await fetch('/api/collections',{method:'POST',headers:{'Content-Type':'application/json'},
    body: JSON.stringify({store:qs('#store').value.trim(), token:qs('#token').value.trim()})});
  const data=await res.json();
  if(data.error){ setLog('❌ '+data.error); return; }
  const sel=qs('#collections'); sel.innerHTML='';
  (data.collections||[]).forEach(c=>{const o=document.createElement('option'); o.value=String(c.id); o.textContent=`${c.title} (#${c.id})`; sel.appendChild(o);});
  qs('#cstatus').textContent=`${(data.collections||[]).length} collecties geladen`; addLog('Collecties geladen.');
}

async function optimizeSelected(){
  if(RUN) return; RUN=true; abortCtrl=new AbortController();
  jobId = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));
  qs('#btnRun').disabled=true; qs('#btnCancel').disabled=false; setLog('Start optimalisatie…');
  const ids=Array.from(qs('#collections').selectedOptions).map(o=>o.value);
  const res=await fetch('/api/optimize',{method:'POST',signal:abortCtrl.signal,headers:{'Content-Type':'application/json'},
    body: JSON.stringify({store:qs('#store').value.trim(), token:qs('#token').value.trim(), model:qs('#model').value.trim(),
      prompt:qs('#prompt').value, collection_ids:ids, txn:qs('#txn').checked, txn_usps:qs('#txn_usps').value,
      name_map:qs('#name_map').value, job_id:jobId})});
  try{
    const rd=res.body.getReader(); const dec=new TextDecoder();
    while(true){ const {value,done}=await rd.read(); if(done) break; addLog(dec.decode(value)); }
  }catch(e){ addLog('⏹ Gestopt: '+(e?.name||'onderbroken')); }
  finally{ RUN=false; qs('#btnRun').disabled=false; qs('#btnCancel').disabled=true; abortCtrl=null; jobId=null; }
}

async function cancelRun(){
  if(!RUN) return; addLog('⏹ Annuleren aangevraagd…');
  try{ await fetch('/api/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:jobId})}); }catch(e){}
  try{ abortCtrl?.abort(); }catch(e){}
}
</script>
</body></html>"""

# =========================
# Routes
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET": return Response(LOGIN_HTML, mimetype="text/html")
    if request.form.get("username") == ADMIN_USERNAME and request.form.get("password") == ADMIN_PASSWORD:
        session["logged_in"] = True; return redirect("/dashboard")
    return Response(LOGIN_HTML, mimetype="text/html", status=401)

@app.route("/")
def root():
    if not session.get("logged_in"): return redirect("/login")
    return redirect("/dashboard")

@app.route("/dashboard")
@_require_login
def dashboard():
    html = (DASHBOARD_HTML
            .replace("[[STORE]]", SHOPIFY_STORE_DOMAIN)
            .replace("[[MODEL]]", OPENAI_MODEL)
            .replace("[[BATCH]]", str(BATCH_SIZE))
            .replace("[[DELAY]]", f"{DELAY_PER_PRODUCT:.1f}")
            .replace("[[TXNUSPS]]", " | ".join(TRANSACTIONAL_CLAIMS))
            .replace("[[TXNCHK]]", "true" if TRANSACTIONAL_MODE else "false")
            .replace("[[NAMEMAP]]", NAME_MAP_DEFAULT.replace("|", "\n")))
    return Response(html, mimetype="text/html")

@app.route("/logout")
def logout():
    session.clear(); return redirect("/login")

@app.route("/api/collections", methods=["POST"])
@_require_login
def api_collections():
    global SHOPIFY_STORE_DOMAIN
    payload = request.get_json(force=True) or {}
    store = (payload.get("store") or SHOPIFY_STORE_DOMAIN).strip()
    token = (payload.get("token") or "").strip()
    SHOPIFY_STORE_DOMAIN = store
    if not token: return jsonify({"error": "Geen Shopify token meegegeven."}), 400
    customs = _paged("/admin/api/2024-07/custom_collections.json", token, store=store)
    smarts  = _paged("/admin/api/2024-07/smart_collections.json",  token, store=store)
    cols = [{"id": c["id"], "title": c.get("title", "(zonder titel)")} for c in (customs + smarts)]
    return jsonify({"collections": cols})

@app.route("/api/cancel", methods=["POST"])
@_require_login
def api_cancel():
    payload = request.get_json(force=True) or {}
    jid = (payload.get("job_id") or "").strip()
    if not jid: return jsonify({"error": "job_id ontbreekt"}), 400
    _cancel_set(jid); return jsonify({"ok": True})

@app.route("/api/optimize", methods=["POST"])
@_require_login
def api_optimize():
    payload = request.get_json(force=True) or {}
    store = (payload.get("store") or SHOPIFY_STORE_DOMAIN).strip()
    token = (payload.get("token") or "").strip()
    model = (payload.get("model") or OPENAI_MODEL).strip()
    user_prompt = (payload.get("prompt") or "").strip()
    collection_ids = payload.get("collection_ids") or []
    txn = bool(payload.get("txn")) if "txn" in payload else TRANSACTIONAL_MODE
    txn_usps = [s.strip() for s in (payload.get("txn_usps") or "").split("|") if s.strip()] or TRANSACTIONAL_CLAIMS
    name_map = parse_name_map_text(payload.get("name_map") or NAME_MAP_DEFAULT.replace("|", "\n"))
    job_id = (payload.get("job_id") or str(uuid4())).strip()

    if not token: return Response("Shopify token ontbreekt.\n", mimetype="text/plain", status=400)
    if not OPENAI_API_KEY: return Response("OPENAI_API_KEY ontbreekt.\n", mimetype="text/plain", status=500)

    _cancel_start(job_id)
    sys_prompt = _build_system_prompt(txn, txn_usps, name_map)

    def stream():
        try:
            yield f"Job: {job_id}\n"
            mm = _ensure_meta_map(token, store)
            if META_DEBUG_MAPPING:
                def _f(d): return f"{d['namespace']}.{d['key']} [{d['type']}]"
                hs = ", ".join(_f(x) for x in mm.get("height_candidates", [])[:2]) or "-"
                ds = ", ".join(_f(x) for x in mm.get("diam_candidates", [])[:1]) or "-"
                yield f"Metafields mapping → Hoogte-kandidaten: {hs}; Diameter-kandidaat: {ds}\n"

            # Product-ids
            if collection_ids:
                all_pids: List[int] = []
                for cid in collection_ids:
                    collects = _paged("/admin/api/2024-07/collects.json", token, {"collection_id": cid}, store)
                    all_pids.extend(int(c["product_id"]) for c in collects)
            else:
                prods = _paged("/admin/api/2024-07/products.json", token, store=store)
                all_pids = [int(p["id"]) for p in prods]

            yield f"Collecties: {len(collection_ids) or 0} geselecteerd — {len(all_pids)} producten gevonden\n"
            yield f"Instellingen: batch={BATCH_SIZE}, delay={DELAY_PER_PRODUCT:.1f}s, model={model} (server-side), transactie={'aan' if txn else 'uit'}\n"

            processed = 0
            for i in range(0, len(all_pids), BATCH_SIZE):
                if _cancel_check(job_id): yield "⏹ Geannuleerd vóór batch.\n"; break
                batch = all_pids[i:i + BATCH_SIZE]
                ids = ",".join(map(str, batch))
                url = f"https://{store}/admin/api/2024-07/products.json"
                prods = _get(url, token, {"ids": ids, "limit": 250}).json().get("products", [])

                for p in prods:
                    if _cancel_check(job_id): yield "⏹ Geannuleerd.\n"; break
                    pid = int(p["id"]); title = p.get("title") or ""; body_html = p.get("body_html") or ""

                    base_prompt = textwrap.dedent(f"""
                        Originele titel: {title}
                        Originele beschrijving (HTML toegestaan): {body_html}

                        Taken:
                        1) Nieuwe titel (flexibel; bij voorkeur 'NL / Latijn – ↕… – ⌀…').
                        2) Gestandaardiseerde beschrijving (200–250 woorden) in schone HTML met exact:
                           <h3>Beschrijving</h3>
                           <p>…</p>
                           <h3>Eigenschappen & behoeften</h3>
                           <p><strong>Lichtbehoefte</strong>: …</p>
                           <p><strong>Waterbehoefte</strong>: …</p>
                           <p><strong>Standplaats</strong>: …</p>
                           <p><strong>Giftigheid</strong>: …</p>
                           (géén bullets, géén emoji)
                        3) Meta title (≤{META_TITLE_LIMIT}) en Meta description (≤{META_DESC_LIMIT}).

                        Output EXACT in dit format:
                        Nieuwe titel: …

                        Beschrijving: … (HTML)

                        Meta title: …
                        Meta description: …
                    """).strip()

                    prompt = (user_prompt + "\n\n" + base_prompt).strip() if user_prompt else base_prompt

                    try:
                        yield f"→ #{pid}: AI-tekst genereren...\n"
                        ai_out = _openai_chat(sys_prompt, prompt, model=model, temperature=OPENAI_TEMP)
                        parts = split_ai_output(ai_out)

                        dims = parse_dimensions(parts["title"] or title, parts["body_html"] or body_html)
                        pot_color = extract_pot_color(parts["title"] or title, parts["body_html"] or body_html)
                        pot_present = detect_pot_presence(parts["title"] or title, parts["body_html"] or body_html)

                        t1 = enforce_title_name_map(parts["title"] or title, name_map)
                        parts["title"] = normalize_title(t1, dims, pot_color, pot_present)

                        # Heroicons toevoegen aan de 4 regels
                        parts["body_html"] = inject_heroicons(parts["body_html"])

                        # Meta afronden
                        final_meta_title = finalize_meta_title(parts["meta_title"], parts["title"], META_TITLE_LIMIT, BRAND_NAME, META_SUFFIX)
                        final_meta_desc  = finalize_meta_desc(parts["meta_description"], parts["body_html"], parts["title"], META_DESC_LIMIT)

                        update_product_texts(store, token, pid,
                                             parts["title"] or title,
                                             parts["body_html"] or body_html,
                                             final_meta_title, final_meta_desc)

                        to_write: Dict[str, str] = {}
                        if dims.get("height_cm"): to_write["height_cm"] = dims["height_cm"]
                        if dims.get("pot_diameter_cm"): to_write["pot_diameter_cm"] = dims["pot_diameter_cm"]
                        if to_write:
                            written = set_product_metafields(token, store, pid, to_write)
                            wlog = []
                            if written.get("height"): wlog.append("hoogte→ " + ", ".join(written["height"]))
                            if written.get("diam"):   wlog.append("diameter→ " + ", ".join(written["diam"]))
                            yield f"   • Metafields gezet: {to_write}  ({'; '.join(wlog) or 'geen matches'})\n"
                        else:
                            yield "   • Geen hoogte/diameter herkend\n"

                        processed += 1
                        yield f"✅ #{pid} bijgewerkt: {(parts['title'] or title)[:120]}\n"

                    except Exception as e:
                        yield f"❌ OpenAI/Shopify-fout bij product #{pid}: {e}\n"

                    time.sleep(DELAY_PER_PRODUCT)

                yield f"-- Batch klaar ({len(prods)} producten) --\n"

            yield f"\nKlaar. Totaal bijgewerkt: {processed}.\n"

        except Exception as e:
            yield f"⚠️ Beëindigd met fout: {e}\n"
        finally:
            _cancel_end(job_id)

    return Response(stream(), mimetype="text/plain", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/healthz")
def healthz():
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
