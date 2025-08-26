# app.py — Belle Flora SEO Optimizer (sessie-creds + CSRF + producten per collectie selecteren + bundels + garden hints + heroicons)
import os, re, json, time, html, secrets
from typing import Any, Dict, List, Optional, Tuple
from functools import wraps

import requests
from flask import Flask, Response, jsonify, redirect, request, session, g

# =========================
# Config & App
# =========================

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", secrets.token_bytes(32))

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 7,
)

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "michiel")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "CHANGE_ME")

SHOPIFY_STORE_DOMAIN = os.environ.get("SHOPIFY_STORE_DOMAIN", "your-store.myshopify.com").strip()

OPENAI_API_KEY   = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL     = os.environ.get("DEFAULT_MODEL", "gpt-4o-mini")
OPENAI_TEMP      = float(os.environ.get("DEFAULT_TEMPERATURE", "0.7"))
OPENAI_RETRIES   = int(os.environ.get("OPENAI_MAX_RETRIES", "4"))

DELAY_PER_PRODUCT  = float(os.environ.get("DELAY_SECONDS", "0.8"))
SHOPIFY_RETRIES    = int(os.environ.get("SHOPIFY_MAX_RETRIES", "4"))
REQUEST_TIMEOUT    = int(os.environ.get("REQUEST_TIMEOUT", "60"))

BRAND_NAME       = os.environ.get("BRAND_NAME", "Belle Flora").strip()
META_SUFFIX      = f" | {BRAND_NAME}"
META_TITLE_LIMIT = int(os.environ.get("META_TITLE_LIMIT", "60"))
META_DESC_LIMIT  = int(os.environ.get("META_DESC_LIMIT", "155"))

TRANSACTIONAL_MODE = os.environ.get("TRANSACTIONAL_MODE", "true").lower() in ("1","true","yes")
TRANSACTIONAL_CLAIMS = [
    "Gratis verzending vanaf €49",
    "Binnen 3 werkdagen geleverd",
    "Soepel retourbeleid",
    "Europese kwekers",
    "Top kwaliteit",
]

NAME_MAP = {
    "Paradijsvogelplant": "Strelitzia",
    "Flamingoplant": "Anthurium",
    "Slaapplant": "Calathea",
    "Gatenplant": "Monstera",
    "Olifantsoor": "Alocasia",
    "Hartbladige klimmer": "Philodendron",
    "Vrouwentong": "Sanseveria",
    "Vioolbladplant": "Ficus lyrata",
    "Drakenboom": "Dracaena",
    "ZZ-Plant": "Zamioculcas zamiifolia",
}

META_NAMESPACE_DEFAULT = os.environ.get("META_NAMESPACE_DEFAULT", "specs")
META_HEIGHT_HINTS = [s for s in os.environ.get("META_HEIGHT_KEYS_HINT", "hoogte,height").split(",") if s.strip()]
META_DIAM_HINTS   = [s for s in os.environ.get("META_DIAM_KEYS_HINT", "diameter,pot,ø,⌀").split(",") if s.strip()]
META_MIRROR_MAX_HEIGHT = int(os.environ.get("META_MIRROR_MAX_HEIGHT", "2"))
META_MIRROR_MAX_DIAM   = int(os.environ.get("META_MIRROR_MAX_DIAM", "1"))

HEROICON_SIZE = int(os.environ.get("HEROICON_SIZE", "20"))

REQ = requests.Session()
_META_MAP_CACHE: Dict[str, Dict[str, Any]] = {}

# =========================
# CSRF
# =========================

@app.before_request
def _csrf_setup():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    g.csrf_token = session["csrf_token"]

def require_csrf(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            hdr = request.headers.get("X-CSRF-Token", "")
            if hdr != session.get("csrf_token"):
                return jsonify({"error": "CSRF"}), 403
        return fn(*a, **kw)
    return wrapper

# =========================
# Utils
# =========================

def _s(x: Any) -> str:
    return x if isinstance(x, str) else ("" if x is None else str(x))

def _trim_word_boundary(text: str, limit: int) -> str:
    if len(text) <= limit: return text
    cut = text[:limit]
    m = list(re.finditer(r"[ \u00A0\u2009\u200A\u200B\u202F\-–—·•,:;|]", cut))
    if m: cut = cut[:m[-1].start()]
    return cut.rstrip(" -–—·|")

def _normalize_store_domain(raw: str) -> str:
    s = (raw or "").strip()
    s = re.sub(r"^https?://", "", s, flags=re.I)
    s = s.split("/")[0]
    return s

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

def _gql_url(store_domain: str) -> str:
    return f"https://{store_domain}/admin/api/2025-01/graphql.json"

def _get_creds(payload: dict | None = None) -> tuple[str, str]:
    payload = payload or {}
    store = _normalize_store_domain(
        payload.get("store")
        or session.get("store")
        or os.environ.get("SHOPIFY_STORE_DOMAIN", "")
    )
    token = (payload.get("token")
             or session.get("token")
             or os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
            ).strip()
    return store, token

# =========================
# Prompts & OpenAI
# =========================

def _build_system_prompt(txn: bool) -> str:
    usps_str = " | ".join(TRANSACTIONAL_CLAIMS)
    txn_block = ""
    if txn:
        txn_block = (
            "TRANSACTIONELE META-RICHTLIJNEN:\n"
            f"  • Meta title ≤{META_TITLE_LIMIT}: begin met koopwoord + product; eindig met '| {BRAND_NAME}' indien passend.\n"
            f"  • Meta description ≤{META_DESC_LIMIT}: voeg 1–2 USP’s toe en subtiele CTA. USP’s: {usps_str}\n\n"
        )
    nm_lines = "\n".join([f"  • {k} → {v}" for k, v in NAME_MAP.items()])
    return (
        "Je bent een ervaren Nederlandstalige SEO-copywriter voor een plantenwebshop (Belle Flora). "
        "Schrijf natuurlijk, feitelijk en klantgericht; geen emoji.\n\n"
        "Titelformat (bij voorkeur): [NL-naam] / [Latijn] – ↕[hoogte cm] – ⌀[pot cm]. "
        "Als er een sierpot is: gebruik ‘– in [kleur] pot’ of ‘– in pot’ (niet raden).\n\n"
        "Beschrijving (HTML):\n"
        "  <h3>Beschrijving</h3><p>…</p>\n"
        "  <h3>Eigenschappen & behoeften</h3>\n"
        "  <p><strong>Lichtbehoefte</strong>: …</p>\n"
        "  <p><strong>Waterbehoefte</strong>: …</p>\n"
        "  <p><strong>Standplaats</strong>: …</p>\n"
        "  <p><strong>Giftigheid</strong>: …</p>\n"
        "  Gebruik alleen simpele HTML; geen lijsten.\n\n"
        "SEO: Lever Meta title (≤60) en Meta description (≤155). Elke tekst uniek.\n\n"
        f"NAAMCONSISTENTIE (toepassen waar relevant):\n{nm_lines}\n\n"
        f"{txn_block}"
        "OUTPUT (exacte labels):\n"
        "Nieuwe titel: …\n\n"
        "Beschrijving: … (HTML)\n\n"
        "Meta title: …\n"
        "Meta description: …\n"
    )

def _openai_chat(sys_prompt: str, user_prompt: str) -> str:
    if not OPENAI_API_KEY: raise RuntimeError("OPENAI_KEY ontbreekt.")
    url = "https://api.openai.com/v1/chat/completions"
    body = {"model": OPENAI_MODEL, "temperature": OPENAI_TEMP,
            "messages": [{"role":"system","content":sys_prompt},{"role":"user","content":user_prompt}]}
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    for i in range(OPENAI_RETRIES):
        r = REQ.post(url, headers=headers, json=body, timeout=120)
        if r.status_code == 429 and i < OPENAI_RETRIES - 1:
            time.sleep(2 ** i); continue
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    r.raise_for_status()
    return ""

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

# =========================
# Parsing titels/dimensies/pot
# =========================

RE_CM_RANGE       = re.compile(r"\b(\d{1,3})\s*[-–]\s*(\d{1,3})\s*cm\b", re.I)
RE_HEIGHT_LABEL   = re.compile(r"(?:↕|hoogte)\s*[:=]?\s*(\d{1,3})\s*cm\b", re.I)
RE_DIAM_LABEL     = re.compile(r"(?:⌀|Ø|ø|diameter|doorsnede|pot\s*maat|potmaat|pot\s*diameter|potdiameter)\s*[:=]?\s*(\d{1,3})\s*cm?\b", re.I)
RE_DIAM_SYMBOL    = re.compile(r"[⌀Øø]\s*(\d{1,3})\s*cm?\b", re.I)
RE_CM_ALL         = re.compile(r"\b(\d{1,3})\s*cm\b", re.I)

COLOR_RE = r"(?:wit|witte|zwart|zwarte|grijs|grijze|antraciet|beige|taupe|terracotta|terra|bruin|bruine|groen|groene|lichtgroen|donkergroen|blauw|blauwe|rood|rode|roze|paars|paarse|geel|gele|oranje|cr[eè]me|goud|gouden|zilver|zilveren|koper|koperen|brons|bronzen)"
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

# --- Bundel detectie
RE_BUNDLE_QTY_PREFIX = re.compile(r"^\s*(\d+)\s*[xX]\s+")
RE_BUNDLE_MIX_HINTS  = re.compile(r"\b(mix|assorti|pakket|bundel|cadeau|geschenk|set|combi|combinatie|box)\b", re.I)
# --- Soortherkenning & 'zekere' tuin-kennis (alleen veelvoorkomende, veilige info)
GARDEN_KB = {
    "hydrangea": ("juni–september", "najaar (sep–nov) of vroege lente (mrt–apr)"),
    "lavandula": ("juni–augustus", "najaar (sep–okt) of lente (apr)"),
    "rosa": ("juni–oktober", "najaar (okt–nov) of vroege lente (mrt)"),
    "helleborus": ("december–maart", "najaar (sep–okt)"),
    "acer palmatum": (None, "najaar (okt–nov) of vroege lente (mrt)"),
    "buxus": (None, "najaar (sep–nov) of vroege lente (mrt–apr)"),
    "olea": (None, "late lente tot zomer (mei–juni)"),
    "lavendel": ("juni–augustus", "najaar (sep–okt) of lente (apr)"),
    "hortensia": ("juni–september", "najaar (sep–nov) of vroege lente (mrt–apr)"),
}

def _detect_species_key(text: str) -> Optional[str]:
    t = (text or "").lower()
    for key in GARDEN_KB.keys():
        if key in t:
            return key
    return None

def _ensure_garden_lines(body_html: str, title_for_species: str) -> str:
    if not body_html:
        return body_html
    lower = body_html.lower()
    has_bloom = "bloeiperiode" in lower
    has_plant = "plantperiode" in lower
    if has_bloom and has_plant:
        return body_html
    key = _detect_species_key(title_for_species)
    if not key:
        return body_html
    bloom, plant = GARDEN_KB.get(key, (None, None))
    start_idx = lower.find("<h3>eigenschappen & behoeften</h3>")
    if start_idx == -1:
        return body_html
    insert_at = start_idx + len("<h3>eigenschappen & behoeften</h3>")
    new_bits = ""
    if not has_bloom and bloom:
        new_bits += f'\n<p><strong>Bloeiperiode</strong>: {bloom}</p>'
    if not has_plant and plant:
        new_bits += f'\n<p><strong>Plantperiode</strong>: {plant}</p>'
    if not new_bits:
        return body_html
    return body_html[:insert_at] + new_bits + body_html[insert_at:]

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

def parse_dimensions_from_variants(product: Dict[str, Any]) -> Dict[str, str]:
    """
    Extra bron als titel/tekst niets oplevert: kijk naar varianten.
    Haal alle '... cm' getallen uit variant-titels en optievelden.
    """
    nums: List[int] = []
    for v in (product.get("variants") or []):
        fields = [v.get("title",""), v.get("option1",""), v.get("option2",""), v.get("option3","")]
        blob = " | ".join([_s(x) for x in fields])
        nums += [int(n) for n in RE_CM_ALL.findall(blob)]
    nums = sorted(set(nums))
    out: Dict[str, str] = {}
    if len(nums) >= 2:
        out["height_cm"] = str(max(nums))
        out["pot_diameter_cm"] = str(min(nums))
    elif len(nums) == 1:
        out["height_cm"] = str(nums[0])
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

def analyze_bundle(title: str) -> Tuple[bool, Optional[int]]:
    t = title or ""
    m = RE_BUNDLE_QTY_PREFIX.match(t)
    qty = int(m.group(1)) if m else None
    if RE_BUNDLE_MIX_HINTS.search(t):
        return True, qty
    if "+" in t:
        after_plus = t.split("+", 1)[1].strip().lower()
        if not after_plus.startswith("pot"):
            return True, qty
    return False, qty

def enforce_title_name_map(title: str) -> str:
    for nl, lat in NAME_MAP.items():
        if re.search(rf"\b{re.escape(nl)}\b", title, re.I) and not re.search(rf"\b{re.escape(lat)}\b", title, re.I):
            return f"{nl} / {lat} – {title}"
    return title

def normalize_title(title: str, dims: Dict[str, str], pot_color: Optional[str], pot_present: bool) -> str:
    t = title or ""
    t = re.sub(r"(↕\s*\d{1,3})(?!\s*cm)\b", r"\1cm", t)
    t = re.sub(r"([⌀Øø]\s*\d{1,3})(?!\s*cm)\b", r"\1cm", t)
    h = dims.get("height_cm"); d = dims.get("pot_diameter_cm")
    if h and not re.search(r"↕\s*\d{1,3}\s*cm", t): t = (t.strip() + f" – ↕{int(h)}cm").strip()
    if d and not re.search(r"[⌀Øø]\s*\d{1,3}\s*cm", t): t = (t.strip() + f" – ⌀{int(d)}cm").strip()
    has_pot_suffix = re.search(r"\bin\s+[^\n]*\bpot\b", t, re.I) is not None
    if pot_color and not has_pot_suffix: t += f" – in {pot_color} pot"
    elif pot_present and not has_pot_suffix: t += " – in pot"
    return t.strip()

def finalize_meta_title(raw: str, title_fallback: str) -> str:
    base = (raw or title_fallback or "").strip()
    if not base:
        return BRAND_NAME[:META_TITLE_LIMIT]
    full = base + META_SUFFIX
    if len(full) <= META_TITLE_LIMIT:
        return full
    keep = META_TITLE_LIMIT - len(META_SUFFIX)
    prefix = _trim_word_boundary(base, keep)
    if not prefix:
        return _trim_word_boundary(BRAND_NAME, META_TITLE_LIMIT)
    return (prefix + META_SUFFIX)[:META_TITLE_LIMIT]

def finalize_meta_desc(raw: str, body_fallback: str, title_fallback: str, txn: bool) -> str:
    text = (raw or re.sub(r"<[^>]+>", " ", body_fallback or "") or title_fallback or "").strip()
    if txn:
        add = " | ".join(TRANSACTIONAL_CLAIMS[:2])
        if add and add not in text:
            text = f"{text}. {add}"
    if len(text) <= META_DESC_LIMIT:
        return text
    return _trim_word_boundary(text, META_DESC_LIMIT)

# ---------- Inline SVG heroicons ----------
def _svg_attrs(name: str) -> str:
    return (f'class="bf-icon" data-heroicon="{name}" '
            f'width="{HEROICON_SIZE}" height="{HEROICON_SIZE}" viewBox="0 0 24 24" '
            'fill="none" stroke="currentColor" stroke-width="1.5" '
            'stroke-linecap="round" stroke-linejoin="round" '
            'style="vertical-align:text-bottom;margin-right:6px;opacity:.95"')

def _icon_svg(name: str) -> str:
    if name == "sun":
        return f'''<svg {_svg_attrs(name)} xmlns="http://www.w3.org/2000/svg">
  <circle cx="12" cy="12" r="4"></circle>
  <line x1="12" y1="2" x2="12" y2="5"></line>
  <line x1="12" y1="19" x2="12" y2="22"></line>
  <line x1="2" y1="12" x2="5" y2="12"></line>
  <line x1="19" y1="12" x2="22" y2="12"></line>
  <line x1="4.22" y1="4.22" x2="6.34" y2="6.34"></line>
  <line x1="17.66" y1="17.66" x2="19.78" y2="19.78"></line>
  <line x1="17.66" y1="6.34" x2="19.78" y2="4.22"></line>
  <line x1="4.22" y1="19.78" x2="6.34" y2="17.66"></line>
</svg>'''
    if name == "droplet":
        return f'''<svg {_svg_attrs(name)} xmlns="http://www.w3.org/2000/svg">
  <path d="M12 2.8C9.2 6 6 9.9 6 13.2a6 6 0 1 0 12 0c0-3.3-3.2-7.2-6-10.4z" fill="none"></path>
</svg>'''
    if name == "home":
        return f'''<svg {_svg_attrs(name)} xmlns="http://www.w3.org/2000/svg">
  <path d="M3 11.5l9-7 9 7"></path>
  <path d="M5.5 10.8V20a1.2 1.2 0 0 0 1.2 1.2H10v-6h4v6h3.3A1.2 1.2 0 0 0 18.5 20v-9.2"></path>
</svg>'''
    if name == "exclamation-triangle":
        return f'''<svg {_svg_attrs(name)} xmlns="http://www.w3.org/2000/svg">
  <path d="M10.3 3.6L2.6 17.1a1.5 1.5 0 0 0 1.3 2.2h16.2a1.5 1.5 0 0 0 1.3-2.2L13.7 3.6a1.5 1.5 0 0 0-3.4 0z"></path>
  <line x1="12" y1="8" x2="12" y2="13"></line>
  <circle cx="12" cy="16.5" r="1"></circle>
</svg>'''
    if name == "calendar":
        return f'''<svg {_svg_attrs(name)} xmlns="http://www.w3.org/2000/svg">
  <rect x="3" y="5" width="18" height="16" rx="2" ry="2"></rect>
  <line x1="8" y1="3" x2="8" y2="7"></line>
  <line x1="16" y1="3" x2="16" y2="7"></line>
  <line x1="3" y1="10" x2="21" y2="10"></line>
</svg>'''
    if name == "sprout":
        return f'''<svg {_svg_attrs(name)} xmlns="http://www.w3.org/2000/svg">
  <path d="M12 21V12"></path>
  <path d="M12 12c0-3 2.5-5 6-5 0 3-2.5 5-6 5z"></path>
  <path d="M12 12c0-3-2.5-5-6-5 0 3 2.5 5 6 5z"></path>
</svg>'''
    return _icon_svg("droplet")

def inject_heroicons(body_html: str) -> str:
    if not body_html:
        return body_html
    if 'class="bf-icon"' in body_html or 'data-heroicon=' in body_html:
        return body_html
    if len(body_html) > 20000:
        return body_html
    out = body_html

    def add_icon(out_html: str, label: str, icon_svg: str) -> str:
        candidates = [
            f"<p><strong>{label}</strong>:", f"<p> <strong>{label}</strong>:",
            f"<p><strong>{label}</strong> :", f"<p>{label}:", f"<p> {label}:",
            f"<p><strong>{label}</strong>&nbsp;:", f"<p>{label}&nbsp;:"
        ]
        for pat in candidates:
            pos = out_html.lower().find(pat.lower())
            if pos != -1:
                return out_html[:pos] + out_html[pos:].replace("<p><strong", f"<p>{icon_svg}<strong", 1)
        return out_html

    out = add_icon(out, "Lichtbehoefte", _icon_svg("sun"))
    out = add_icon(out, "Waterbehoefte", _icon_svg("droplet"))
    out = add_icon(out, "Standplaats",   _icon_svg("home"))
    out = add_icon(out, "Giftigheid",    _icon_svg("exclamation-triangle"))
    out = add_icon(out, "Bloeiperiode",  _icon_svg("calendar"))
    out = add_icon(out, "Plantperiode",  _icon_svg("sprout"))
    return out

# =========================
# Shopify: teksten + metafields
# =========================

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
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out

def _ensure_meta_map(token: str, store_domain: str) -> Dict[str, Any]:
    cache_key = store_domain
    if cache_key in _META_MAP_CACHE: return _META_MAP_CACHE[cache_key]
    defs = _defs_for_product(token, store_domain)
    h = _rank_candidates(defs, META_HEIGHT_HINTS or ["hoogte", "height"])
    d = _rank_candidates(defs, META_DIAM_HINTS  or ["diameter", "pot", "ø", "⌀"])
    if not h: h = [{"namespace": META_NAMESPACE_DEFAULT, "key": "height_cm", "name": "height_cm", "type": "single_line_text_field", "score": 0}]
    if not d: d = [{"namespace": META_NAMESPACE_DEFAULT, "key": "pot_diameter_cm", "name": "pot_diameter_cm", "type": "single_line_text_field", "score": 0}]
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
    mutation setOne($metafields: [MetafieldsSetInput!]!] {
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

# =========================
# Auth & UI
# =========================

def _require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"): return redirect("/login")
        return fn(*args, **kwargs)
    return wrapper

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
<meta name="csrf" content="{{CSRF}}">
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#0b1020;color:#eef;margin:0}
.wrap{max-width:1100px;margin:28px auto;padding:0 16px}
.card{background:#121735;padding:20px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.35);margin-bottom:16px}
label{display:block;margin:10px 0 6px}
input,textarea,select{width:100%;padding:12px;border-radius:10px;border:1px solid #2a335a;background:#0f1430;color:#eef}
button{padding:12px 16px;border:0;border-radius:12px;background:#4f7dff;color:#fff;font-weight:600;cursor:pointer}
pre{white-space:pre-wrap}
.pill{display:inline-block;padding:6px 10px;border-radius:999px;background:#243165;margin-left:8px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
</style></head><body>
<div class="wrap">
  <div class="card">
    <h1>Belle Flora SEO Optimizer</h1>
    <div class="row">
      <div>
        <label>Store domein</label>
        <input id="store" placeholder="belle-flora-be.myshopify.com" value="">
      </div>
      <div>
        <label>Shopify Access Token</label>
        <input id="token" placeholder="shpat_..." value="">
      </div>
    </div>
    <div style="margin-top:10px">
      <button onclick="saveCreds()">Opslaan</button>
      <button onclick="loadCollections()">Collecties laden</button>
      <span id="cstatus" class="pill">Nog niet geladen</span>
    </div>
  </div>

  <div class="card">
    <label>Selecteer collecties</label>
    <select id="collections" multiple size="10" style="height:220px"></select>

    <div style="margin-top:10px">
      <button onclick="loadProductsForCollections()">Producten laden</button>
      <span id="pstatus" class="pill">Geen producten geladen</span>
    </div>

    <div style="margin-top:10px">
      <label>Producten in de geselecteerde collecties (de-selecteer wat je wil overslaan)</label>
      <select id="products" multiple size="12" style="height:260px"></select>
    </div>

    <div style="margin-top:12px">
      <label><input type="checkbox" id="txn" checked> Transactiefocus (koopwoorden + USP’s)</label>
      <div style="opacity:.8;margin-top:4px;font-size:12px;">USP’s: Gratis verzending vanaf €49 | Binnen 3 werkdagen geleverd | Soepel retourbeleid | Europese kwekers | Top kwaliteit</div>
    </div>
    <div style="margin-top:12px">
      <button id="btnRun" onclick="optimizeSelected()">Optimaliseer geselecteerde producten</button>
      <button id="btnCancel" onclick="cancelJob()" disabled>Annuleer</button>
    </div>
  </div>

  <div class="card">
    <small>Live status</small>
    <pre id="status">Klaar om te starten…</pre>
  </div>
</div>

<script>
function qs(s){return document.querySelector(s);}
function setLog(t){qs('#status').textContent = t}
function addLog(t){qs('#status').textContent += '\\n' + t}

const CSRF = document.querySelector('meta[name="csrf"]').content;

function post(url, body){
  return fetch(url, {
    method:'POST',
    headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},
    body: JSON.stringify(body || {})
  });
}

async function saveCreds(){
  const store=(qs('#store')?.value||'').trim();
  const token=(qs('#token')?.value||'').trim();
  if(!store || !token){ alert('Vul store en token in.'); return; }
  try{
    const res = await post('/api/set-creds', {store, token});
    const data = await res.json().catch(()=>({}));
    if(!res.ok){ alert('Opslaan mislukt: ' + (data.error || res.status)); return; }
    qs('#cstatus').textContent = 'Gegevens opgeslagen';
  }catch(e){ alert('Netwerkfout: ' + e.message); }
}

async function loadCollections(){
  setLog('Collecties laden…');
  try{
    const store=(qs('#store')?.value||'').trim();
    const token=(qs('#token')?.value||'').trim();
    const res=await post('/api/collections', {store, token});
    const data=await res.json().catch(()=>null);
    if(!res.ok){
      addLog('❌ ' + (data && data.error ? data.error : ('Fout '+res.status)));
      return;
    }
    const sel=qs('#collections'); sel.innerHTML='';
    (data||[]).forEach(c=>{const o=document.createElement('option');o.value=String(c.id);o.textContent=c.title;sel.appendChild(o);});
    qs('#cstatus').textContent=`${(data||[]).length} collecties geladen`;
    addLog('✅ Collecties geladen.');
  }catch(e){ addLog('❌ Netwerkfout: '+e.message); }
}

async function loadProductsForCollections(){
  setLog('Producten ophalen…');
  const ids=Array.from(qs('#collections').selectedOptions).map(o=>o.value);
  if(ids.length===0){ addLog('Kies eerst 1 of meer collecties.'); return; }
  const store=(qs('#store')?.value||'').trim();
  const token=(qs('#token')?.value||'').trim();
  try{
    const res = await post('/api/collection-products',{store, token, collection_ids: ids});
    const data = await res.json().catch(()=>null);
    if(!res.ok){ addLog('❌ ' + (data && data.error ? data.error : ('Fout '+res.status))); return; }
    const sel=qs('#products'); sel.innerHTML='';
    (data||[]).forEach(p=>{
      const o=document.createElement('option');
      o.value=String(p.id); o.textContent=`#${p.id} — ${p.title}`; o.selected=true;
      sel.appendChild(o);
    });
    qs('#pstatus').textContent = `${(data||[]).length} producten geladen (alles geselecteerd)`;
    addLog('✅ Producten geladen.');
  }catch(e){ addLog('❌ Netwerkfout: '+e.message); }
}

let abortCtrl=null, RUN=false;
async function optimizeSelected(){
  if(RUN) return; RUN=true; qs('#btnCancel').disabled=false; setLog('Start optimalisatie…');
  abortCtrl=new AbortController();
  const collection_ids=Array.from(qs('#collections').selectedOptions).map(o=>o.value);
  const product_ids=Array.from(qs('#products').selectedOptions).map(o=>o.value);
  const store=(qs('#store')?.value||'').trim();
  const token=(qs('#token')?.value||'').trim();
  const body={store, token, collection_ids, product_ids, txn: qs('#txn').checked};
  const res=await fetch('/api/optimize',{method:'POST',signal:abortCtrl.signal,headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},body:JSON.stringify(body)});
  if(!res.ok){ addLog('❌ '+res.status); RUN=false; qs('#btnCancel').disabled=true; return; }
  const reader=res.body.getReader(); const dec=new TextDecoder();
  while(true){ const {value,done}=await reader.read(); if(done) break; addLog(dec.decode(value));}
  RUN=false; qs('#btnCancel').disabled=true;
}
function cancelJob(){ if(abortCtrl){ abortCtrl.abort(); addLog('⏹ Job geannuleerd.'); qs('#btnCancel').disabled=true; } }
</script>
</body></html>"""

# =========================
# Routes
# =========================

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

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "GET":
        return Response(LOGIN_HTML, mimetype="text/html")
    if request.form.get("username")==ADMIN_USERNAME and request.form.get("password")==ADMIN_PASSWORD:
        session["logged_in"]=True; session.permanent=True; return redirect("/")
    return Response(LOGIN_HTML, mimetype="text/html", status=401)

@app.route("/")
def dashboard():
    if not session.get("logged_in"): return redirect("/login")
    html = DASHBOARD_HTML.replace("{{CSRF}}", g.csrf_token)
    return Response(html, mimetype="text/html")

@app.post("/api/set-creds")
@_require_login
@require_csrf
def api_set_creds():
    data = request.get_json(force=True) or {}
    store = _normalize_store_domain(data.get("store", ""))
    token = (data.get("token") or "").strip()
    if not store or not token:
        return jsonify({"error": "Store en token zijn verplicht."}), 400
    session["store"] = store
    session["token"] = token
    session.permanent = True
    return jsonify({"ok": True, "store": store})

@app.route("/api/collections", methods=["POST"])
@_require_login
@require_csrf
def api_collections():
    try:
        data = request.get_json(force=True) or {}
        store, token = _get_creds(data)
        if not store or not token:
            return jsonify({"error": "SHOPIFY_STORE_DOMAIN of SHOPIFY_ACCESS_TOKEN ontbreekt."}), 400
        customs = _paged("/admin/api/2024-07/custom_collections.json", token, store=store)
        smarts  = _paged("/admin/api/2024-07/smart_collections.json",  token, store=store)
        cols = [{"id": c["id"], "title": c.get("title","(zonder titel)")} for c in (customs+smarts)]
        return jsonify(cols)
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", 502)
        text = ""
        try: text = e.response.text
        except Exception: text = str(e)
        return jsonify({"error": f"Shopify HTTP {code}: {text}"}), code
    except Exception as e:
        return jsonify({"error": f"Collecties laden mislukt: {e}"}), 400

# >>> Nieuw: producten uit geselecteerde collecties ophalen
@app.post("/api/collection-products")
@_require_login
@require_csrf
def api_collection_products():
    try:
        data = request.get_json(force=True) or {}
        store, token = _get_creds(data)
        coll_ids = data.get("collection_ids") or []
        if not store or not token:
            return jsonify({"error":"Store of token ontbreekt."}), 400
        if not coll_ids:
            return jsonify([])

        # Verzamel unieke product_ids via collects
        pid_set = set()
        for cid in coll_ids:
            collects = _get(f"https://{store}/admin/api/2024-07/collects.json", token,
                            params={"collection_id": cid, "limit": 250}).json().get("collects", [])
            for c in collects:
                pid_set.add(int(c["product_id"]))

        pids = sorted(pid_set)
        products: List[Dict[str, Any]] = []
        for i in range(0, len(pids), 50):
            batch = pids[i:i+50]
            r = _get(f"https://{store}/admin/api/2024-07/products.json", token,
                     params={"ids": ",".join(map(str, batch)), "limit": 250})
            prods = r.json().get("products", [])
            for p in prods:
                products.append({"id": int(p["id"]), "title": p.get("title","")})

        products.sort(key=lambda x: (x["title"].lower(), x["id"]))
        return jsonify(products)
    except Exception as e:
        return jsonify({"error": f"Producten laden mislukt: {e}"}), 400

@app.route("/api/optimize", methods=["POST"])
@_require_login
@require_csrf
def api_optimize():
    payload = request.get_json(force=True) or {}
    store, token = _get_creds(payload)
    txn   = bool(payload.get("txn", TRANSACTIONAL_MODE))
    colls = payload.get("collection_ids") or []
    explicit_pids = payload.get("product_ids") or []
    if not store or not token:
        return Response("Store of token ontbreekt.\n", mimetype="text/plain", status=400)
    if not OPENAI_API_KEY:
        return Response("OPENAI_API_KEY ontbreekt.\n", mimetype="text/plain", status=500)

    sys_prompt = _build_system_prompt(txn)

    # Bepaal of er tuin-collecties zitten in de selectie (stuurt prompt-hints)
    garden_words = {"tuinplanten","bloeiende tuinplanten","siergrassen","hagen","klimplanten","olijfbomen","moestuin"}
    selected_titles = []
    for coll_id in colls:
        try:
            r1 = _get(f"https://{store}/admin/api/2024-07/smart_collections/{coll_id}.json", token)
            t1 = (r1.json().get("smart_collection") or {}).get("title")
            if t1: selected_titles.append(t1.lower())
        except Exception:
            pass
        try:
            r2 = _get(f"https://{store}/admin/api/2024-07/custom_collections/{coll_id}.json", token)
            t2 = (r2.json().get("custom_collection") or {}).get("title")
            if t2: selected_titles.append(t2.lower())
        except Exception:
            pass
    is_garden_selection = any(any(w in t for w in garden_words) for t in selected_titles)

    def stream():
        try:
            pid_list: List[int] = []
            if explicit_pids:
                pid_list = [int(x) for x in explicit_pids]
                yield f"{len(pid_list)} expliciet geselecteerde producten ontvangen.\n"
            else:
                pid_set = set()
                for coll_id in colls:
                    url=f"https://{store}/admin/api/2024-07/collects.json"
                    collects=_get(url, token, params={"collection_id":coll_id,"limit":250}).json().get("collects",[])
                    for c in collects:
                        pid_set.add(int(c["product_id"]))
                pid_list = sorted(pid_set)
                yield f"{len(pid_list)} producten gevonden uit collecties.\n"

            total_updated = 0
            if not pid_list:
                yield "Niets te doen (lege selectie).\n"
                return

            for i in range(0, len(pid_list), 50):
                batch_ids = pid_list[i:i+50]
                r=_get(f"https://{store}/admin/api/2024-07/products.json", token,
                       params={"ids":",".join(map(str,batch_ids)),"limit":250})
                prods=r.json().get("products",[])
                for p in prods:
                    pid=int(p["id"])
                    title=_s(p.get("title",""))
                    body=_s(p.get("body_html",""))

                    skip_bundle, qty = analyze_bundle(title)
                    if skip_bundle:
                        yield f"⏭️ #{pid}: overgeslagen (bundel met verschillende producten)\n"
                        continue

                    base_prompt = (
                        f"Originele titel: {title}\n"
                        f"Originele beschrijving (HTML toegestaan): {body}\n"
                        "Taken:\n"
                        "1) Lever ‘Nieuwe titel’ volgens format.\n"
                        "2) Lever ‘Beschrijving’ (HTML) met vaste h3-secties en 4 regels.\n"
                        "3) Lever ‘Meta title’ (≤60) en ‘Meta description’ (≤155).\n"
                    )
                    if is_garden_selection:
                        base_prompt += (
                            "\nVOOR TUINPLANTEN:\n"
                            "- Voeg ONDER 'Eigenschappen & behoeften' optioneel extra regels toe (alleen als je het met hoge zekerheid weet):\n"
                            "  <p><strong>Bloeiperiode</strong>: …</p>\n"
                            "  <p><strong>Plantperiode</strong>: …</p>\n"
                            "- Als je het NIET zeker weet: laat de regels weg (NIET raden).\n"
                            "- Gebruik korte, duidelijke maandenreeksen (bv. 'juni–september', 'najaar (sep–nov)').\n"
                        )

                    try:
                        yield f"→ #{pid}: AI-tekst genereren...\n"
                        ai_raw=_openai_chat(sys_prompt, base_prompt)
                        pieces = split_ai_output(ai_raw)

                        title_ai = enforce_title_name_map(_s(pieces.get("title")) or title)
                        body_ai  = _s(pieces.get("body_html")) or body

                        # 1) Afmetingen – AI → originele content → varianten
                        dims = parse_dimensions(title_ai, body_ai)
                        if not dims.get("height_cm") and not dims.get("pot_diameter_cm"):
                            dims = parse_dimensions(title, body)  # fixed indent (actieve fallback)
                        if not dims.get("height_cm") and not dims.get("pot_diameter_cm"):
                            dims = parse_dimensions_from_variants(p)

                        pot_color   = extract_pot_color(title_ai, body_ai)
                        pot_present = detect_pot_presence(title_ai, body_ai)

                        final_title = normalize_title(title_ai, dims, pot_color, pot_present)
                        if qty and not re.match(r"^\s*\d+\s*[xX]\s+", final_title):
                            final_title = f"{qty}x {final_title}"

                        # 2) Tuin-info (veilig bekende bloei/plantperiode)
                        final_body = body_ai
                        if is_garden_selection:
                            final_body = _ensure_garden_lines(final_body, final_title)

                        # 3) Heroicons injecteren
                        final_body = inject_heroicons(final_body)

                        final_meta_title = finalize_meta_title(pieces.get("meta_title"), final_title)
                        final_meta_desc  = finalize_meta_desc(pieces.get("meta_description"), final_body, final_title, txn)

                        update_product_texts(store, token, pid, final_title, final_body, final_meta_title, final_meta_desc)

                        # Metafields op product
                        missing = {}
                        if dims.get("height_cm"):        missing["height_cm"] = dims["height_cm"]
                        if dims.get("pot_diameter_cm"):  missing["pot_diameter_cm"] = dims["pot_diameter_cm"]
                        if missing:
                            w = set_product_metafields(token, store, pid, missing)
                            yield f"   • Metafields aangevuld: {missing} → {w}\n"
                        else:
                            yield "   • Metafields al aanwezig of geen waarden gevonden\n"

                        total_updated += 1
                        yield f"✅ #{pid} bijgewerkt: {final_title}\n"

                    except Exception as e:
                        yield f"❌ Fout bij product #{pid}: {e}\n"

                    time.sleep(DELAY_PER_PRODUCT)

                yield f"-- Batch klaar ({len(prods)} producten) --\n"

            yield f"Klaar. Totaal bijgewerkt: {total_updated}\n"
        except Exception as e:
            yield f"⚠️ Beëindigd met fout: {e}\n"

    return Response(stream(), mimetype="text/plain", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# =========================
# Health
# =========================

@app.get("/healthz")
def healthz():
    return "ok", 200

# =========================
# Main
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)
