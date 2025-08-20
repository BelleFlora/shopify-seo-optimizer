# ======================================
# Belle Flora Optimizer - app.py
# Volledige productieversie (deel 1/3)
# ======================================

import os
import re
import json
import time
import secrets
import requests
from typing import Any, Dict, List, Optional
from flask import Flask, request, jsonify, render_template

# =========================
# Config
# =========================

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", secrets.token_bytes(32))

BRAND_NAME = os.environ.get("BRAND_NAME", "Belle Flora").strip()
META_SUFFIX = f" | {BRAND_NAME}"
META_TITLE_LIMIT = 60
META_DESC_LIMIT = 155

TRANSACTIONAL_CLAIMS = [
    "Gratis verzending vanaf €49",
    "Binnen 3 werkdagen geleverd",
    "Soepel retourbeleid",
    "Europese kwekers",
    "Top kwaliteit",
]

SHOPIFY_STORE_DOMAIN = os.environ.get("SHOPIFY_STORE_DOMAIN")
SHOPIFY_ACCESS_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN")

# =========================
# Shopify API Helpers
# =========================

def shopify_get(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2024-01/{endpoint}"
    headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN}
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            time.sleep(2)
        except Exception as e:
            print(f"[Shopify GET error] {e}")
            time.sleep(2)
    return {}

def shopify_put(endpoint: str, payload: Dict[str, Any]) -> bool:
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2024-01/{endpoint}"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    for attempt in range(3):
        try:
            r = requests.put(url, headers=headers, data=json.dumps(payload), timeout=20)
            if r.status_code in (200, 201):
                return True
            print(f"[Shopify PUT failed] {r.status_code} {r.text}")
            time.sleep(2)
        except Exception as e:
            print(f"[Shopify PUT error] {e}")
            time.sleep(2)
    return False

# =========================
# Heroicons (inline SVG)
# =========================

HEROICONS = {
    "water": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 2C12 2 6 9 6 14a6 6 0 0012 0c0-5-6-12-6-12z"/></svg>',
    "sun": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2m10-10h-2M4 12H2m15.364-7.364l-1.414 1.414M6.05 17.95l-1.414 1.414M17.95 17.95l1.414 1.414M6.05 6.05L4.636 4.636"/></svg>',
    "bloom": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4m10-10h-4M6 12H2m15.364-7.364l-2.828 2.828M6.05 17.95l-2.828 2.828M17.95 17.95l2.828 2.828M6.05 6.05L3.222 3.222"/></svg>',
    "calendar": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
    "fruit": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="7"/><path d="M12 2v2m0 16v2m8.485-8.485l-1.414-1.414M4.93 19.07l-1.414-1.414M19.07 19.07l-1.414-1.414M4.93 4.93L3.516 3.516"/></svg>',
    "pot": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 3h16l-1 9a7 7 0 01-14 0L4 3z"/></svg>',
    "shield": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 2l8 4v6c0 5.25-3.438 10-8 12-4.563-2-8-6.75-8-12V6l8-4z"/></svg>',
}
# ======================================
# Belle Flora Optimizer - app.py
# Volledige productieversie (deel 2/3)
# ======================================

import openai
openai.api_key = os.environ.get("OPENAI_API_KEY")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "gpt-4o-mini")
DEFAULT_TEMPERATURE = float(os.environ.get("DEFAULT_TEMPERATURE", 0.7))

# =========================
# Meta Helpers
# =========================

def trim_meta_title(title: str) -> str:
    """Trim title maar knip nooit Belle Flora af"""
    if len(title) <= META_TITLE_LIMIT:
        return title
    cut = title[:META_TITLE_LIMIT]
    if "Belle Flora" in title and "Belle Flora" not in cut:
        cut = cut.rsplit(" ", 1)[0]
        cut = cut[: -(len(BRAND_NAME) + 1)]
        cut = cut.strip()
        return f"{cut}{META_SUFFIX}"
    return cut.strip()

def trim_meta_desc(desc: str) -> str:
    if len(desc) <= META_DESC_LIMIT:
        return desc
    cut = desc[:META_DESC_LIMIT]
    cut = cut.rsplit(" ", 1)[0]
    return cut

def inject_heroicons(body: str) -> str:
    """Vervang labels door heroicons waar mogelijk"""
    replacements = {
        "Waterbehoefte": HEROICONS["water"],
        "Lichtbehoefte": HEROICONS["sun"],
        "Bloeiperiode": HEROICONS["bloom"],
        "Plantperiode": HEROICONS["calendar"],
        "Oogsttijd": HEROICONS["fruit"],
        "Pot": HEROICONS["pot"],
        "Veiligheid": HEROICONS["shield"],
    }
    for label, icon in replacements.items():
        body = re.sub(
            rf"(<strong>{label}</strong>:)",
            icon + " " + r"\1",
            body
        )
    return body

# =========================
# AI Prompt Builder
# =========================

def build_prompt(title: str, body: str, category: str) -> str:
    """Bouw de instructie voor OpenAI op basis van categorie"""
    if category == "kamerplanten":
        return f"""Optimaliseer dit kamerplanten product:

Titel: {title}
Beschrijving: {body}

- Voeg eigenschappen toe zoals water- en lichtbehoefte
- Houd tekst klantvriendelijk en wervend
- Geef resultaat in HTML met <p> en <strong> labels
"""
    elif category == "tuinplanten":
        return f"""Optimaliseer dit tuinplanten product:

Titel: {title}
Beschrijving: {body}

- Voeg eigenschappen toe zoals water- en lichtbehoefte
- Voeg indien relevant bloeiperiode, plantperiode en oogsttijd toe
- Geef resultaat in HTML met <p> en <strong> labels
"""
    elif category == "potten":
        return f"""Optimaliseer dit potten product:

Titel: {title}
Beschrijving: {body}

- Beschrijf enkel zekere eigenschappen zoals materiaal, kleur, hoogte, diameter
- Geef resultaat in HTML
"""
    elif category == "verzorging":
        return f"""Optimaliseer dit verzorgingsproduct:

Titel: {title}
Beschrijving: {body}

- Geef enkel juiste info (toepassing, inhoud, dosering, frequentie, veiligheid)
- Resultaat in HTML
"""
    return f"""Optimaliseer dit product:

Titel: {title}
Beschrijving: {body}
"""

# =========================
# AI Call
# =========================

def call_openai(prompt: str) -> str:
    try:
        response = openai.ChatCompletion.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "system", "content": "Jij bent een e-commerce SEO specialist."},
                      {"role": "user", "content": prompt}],
            temperature=DEFAULT_TEMPERATURE,
            max_tokens=600,
        )
        return response["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[OpenAI error] {e}")
        return ""

# =========================
# Product Optimizer
# =========================

def optimize_product(title: str, body: str, category: str) -> Dict[str, Any]:
    """Optimaliseer product met AI en SEO"""
    prompt = build_prompt(title, body, category)
    optimized_body = call_openai(prompt)

    # fallback
    if not optimized_body.strip():
        optimized_body = body

    # SEO
    meta_title = trim_meta_title(f"{title}{META_SUFFIX}")
    usp = TRANSACTIONAL_CLAIMS[0]
    meta_desc = trim_meta_desc(f"{title} kopen? {usp}")

    # Heroicons
    optimized_body = inject_heroicons(optimized_body)

    return {
        "title": title,
        "body_html": optimized_body,
        "seo": {
            "title": meta_title,
            "description": meta_desc
        },
    }
# ======================================
# Belle Flora Optimizer - app.py
# Volledige productieversie (deel 3/3)
# ======================================

# ---------- Collection handle → categorie ----------

_COLL_ID_TO_HANDLE: Dict[int, str] = {}  # cache: collection_id -> handle

def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    # vereenvoudigde normalisatie (accenten weghalen kan desgewenst later)
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")

INDOOR_HANDLES = {
    _slug(x) for x in [
        "Kamerplanten","Bloeiende Kamerplanten","Groene Kamerplanten",
        "Luchtzuiverende planten","Kamerplanten in pot","Kamerplanten zonder sierpot",
        "Cactussen","XXL Cactus","XXL Planten",
        "Drakenboom - Dracaena","Flamingoplant - Anthurium","Gatenplant - Monstera",
        "Hartigbladige Klimmer - Philodendron","Pannenkoekenplant - Pilea Peperomioides",
        "Orchidee","Orchideeën met pot","Orchideeën zonder Pot",
        "Vioolbladplant - Ficus Lyrata","Vrouwentong - Sansevieria",
        "ZZ-plant - Zamioculcas Zamiifoli",
    ]
}

GARDEN_HANDLES = {
    _slug(x) for x in [
        "Tuinplanten","Bloeiende Tuinplanten","Siergrassen","Hagen",
        "Klimplanten","Moestuin Planten",
        "Olijfbomen","Citrusbomen","Yucca Rostrata",
        "Bloembollen",
    ]
}

POT_HANDLES = {_slug(x) for x in ["Potten","Orchidee Potten"]}
CARE_HANDLES = {_slug(x) for x in ["Verzorging","Verzorging Buiten","Verzorging Orchideeën","Accessoires"]}

# prioriteit: specifiek > generiek
MODE_PRIORITY = ["potten", "verzorging", "tuinplanten", "kamerplanten"]  # strings die we doorgeven aan AI

def _shopify_rest(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2024-01/{path}"
    headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN}
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, params=params or {}, timeout=20)
            if r.status_code == 200:
                return r.json()
            time.sleep(1.5)
        except Exception as e:
            print(f"[Shopify REST error] {e}")
            time.sleep(1.5)
    return {}

def _fetch_all_collections() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    # custom collections
    data = _shopify_rest("custom_collections.json", {"limit": 250})
    out.extend(data.get("custom_collections", []))
    # smart collections
    data = _shopify_rest("smart_collections.json", {"limit": 250})
    out.extend(data.get("smart_collections", []))
    return out

def _fill_collection_cache() -> List[Dict[str, Any]]:
    global _COLL_ID_TO_HANDLE
    cols = _fetch_all_collections()
    _COLL_ID_TO_HANDLE = {}
    for c in cols:
        cid = int(c["id"])
        handle = _slug(c.get("handle") or c.get("title") or str(cid))
        _COLL_ID_TO_HANDLE[cid] = handle
    # Voor UI: return enkel id + title
    return [{"id": c["id"], "title": c.get("title", "(zonder titel)")} for c in cols]

def _handles_for_product(product_id: int) -> List[str]:
    # haalt collecties voor dit product op
    collects = _shopify_rest("collects.json", {"product_id": product_id, "limit": 250}).get("collects", [])
    handles: List[str] = []
    for cl in collects:
        cid = int(cl["collection_id"])
        h = _COLL_ID_TO_HANDLE.get(cid)
        if not h:
            # fallback: fetch specifiek
            custom = _shopify_rest("custom_collections.json", {"ids": cid}).get("custom_collections", [])
            smart  = _shopify_rest("smart_collections.json",  {"ids": cid}).get("smart_collections", [])
            item = (custom + smart)
            if item:
                h = _slug(item[0].get("handle") or item[0].get("title") or str(cid))
                _COLL_ID_TO_HANDLE[cid] = h
        if h:
            handles.append(h)
    return sorted(set(handles))

def _detect_category_from_handles(handles: List[str]) -> str:
    hs = set(handles)
    found = set()
    if hs & POT_HANDLES:    found.add("potten")
    if hs & CARE_HANDLES:   found.add("verzorging")
    if hs & GARDEN_HANDLES: found.add("tuinplanten")
    if hs & INDOOR_HANDLES: found.add("kamerplanten")
    for cat in MODE_PRIORITY:
        if cat in found:
            return cat
    return "kamerplanten"

# ---------- Shopify product helpers ----------

def _get_product(product_id: int) -> Optional[Dict[str, Any]]:
    data = _shopify_rest(f"products/{product_id}.json")
    return data.get("product")

def _update_product(product_id: int, new_title: Optional[str], new_body_html: Optional[str]) -> bool:
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2024-01/products/{product_id}.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {"product": {"id": product_id}}
    if new_title is not None:
        payload["product"]["title"] = new_title
    if new_body_html is not None:
        payload["product"]["body_html"] = new_body_html
    try:
        r = requests.put(url, headers=headers, data=json.dumps(payload), timeout=30)
        if r.status_code in (200, 201):
            return True
        print(f"[Shopify update failed] {r.status_code} {r.text}")
        return False
    except Exception as e:
        print(f"[Shopify update error] {e}")
        return False

# ---------- Routes ----------

@app.route("/", methods=["GET"])
def home():
    return (
        "<h1>Belle Flora Optimizer</h1>"
        "<p>Endpoints:</p>"
        "<ul>"
        "<li>POST /api/collections — laad & cache collecties</li>"
        "<li>POST /api/optimize — optimaliseer 1 product (JSON: product_id, apply[=true|false])</li>"
        "</ul>",
        200,
        {"Content-Type": "text/html; charset=utf-8"},
    )

@app.route("/api/collections", methods=["POST"])
def api_collections():
    # Laad alle collecties en vul cache (id -> handle)
    if not SHOPIFY_STORE_DOMAIN or not SHOPIFY_ACCESS_TOKEN:
        return jsonify({"error": "SHOPIFY_STORE_DOMAIN of SHOPIFY_ACCESS_TOKEN ontbreekt"}), 400
    cols = _fill_collection_cache()
    return jsonify({"collections": cols, "cached": len(_COLL_ID_TO_HANDLE)})

@app.route("/api/optimize", methods=["POST"])
def api_optimize():
    if not SHOPIFY_STORE_DOMAIN or not SHOPIFY_ACCESS_TOKEN:
        return jsonify({"error": "SHOPIFY_STORE_DOMAIN of SHOPIFY_ACCESS_TOKEN ontbreekt"}), 400

    data = request.get_json(force=True) or {}
    product_id = int(data.get("product_id") or 0)
    apply = bool(data.get("apply", False))
    category_override = (data.get("category") or "").strip().lower()

    if not product_id:
        return jsonify({"error": "product_id ontbreekt"}), 400

    prod = _get_product(product_id)
    if not prod:
        return jsonify({"error": f"Product {product_id} niet gevonden"}), 404

    title = prod.get("title") or ""
    body_html = prod.get("body_html") or ""

    # detecteer categorie via collection handles (met prioriteit)
    handles = _handles_for_product(product_id)
    category = category_override if category_override else _detect_category_from_handles(handles)

    # optimaliseer via AI
    result = optimize_product(title=title, body=body_html, category=category)

    updated = False
    if apply:
        updated = _update_product(
            product_id=product_id,
            new_title=result.get("title"),
            new_body_html=result.get("body_html"),
        )

    return jsonify({
        "product_id": product_id,
        "category": category,
        "handles": handles,
        "result": result,
        "updated": updated,
    })

# ---------- Main ----------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
