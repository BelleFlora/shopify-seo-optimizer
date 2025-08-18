# app.py
# Belle Flora SEO Optimizer – Flask (Render/Railway ready)
# NL UI • Login via env vars • Server-side OpenAI key • Collectie-selectie • Batching • Backoff • GraphQL updates
# Metafields: auto-detectie (namespace/key/type) + fallback + mirroring
# Transactiefocus: koopwoorden in meta's + USP-lijst (default: Gratis verzending vanaf €49)

import os, json, time, textwrap, html, re
from typing import List, Dict, Any, Optional
from flask import Flask, request, session, redirect, Response, jsonify
import requests
import urllib.request
import urllib.error

# -------------------------------
# Config & App
# -------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', os.urandom(32))

ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'michiel')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'CHANGE_ME')
SHOPIFY_STORE_DOMAIN = os.environ.get('SHOPIFY_STORE_DOMAIN', 'your-store.myshopify.com').strip()
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '').strip()  # server-side, niet in UI tonen

DEFAULT_MODEL = os.environ.get('DEFAULT_MODEL', 'gpt-4o-mini')
DEFAULT_TEMPERATURE = float(os.environ.get('DEFAULT_TEMPERATURE', '0.7'))

# Batch/vertragingsinstellingen
BATCH_SIZE = int(os.environ.get('BATCH_SIZE', '8'))                # aantal producten per API-read batch
DELAY_PER_PRODUCT = float(os.environ.get('DELAY_SECONDS', '2.5'))  # pauze tussen producten
OPENAI_MAX_RETRIES = int(os.environ.get('OPENAI_MAX_RETRIES', '4'))
SHOPIFY_MAX_RETRIES = int(os.environ.get('SHOPIFY_MAX_RETRIES', '4'))

# Metafields-config (overridebaar via env vars)
META_NAMESPACE_DEFAULT = os.environ.get('META_NAMESPACE_DEFAULT', 'specs')
META_HEIGHT_KEYS_HINT  = os.environ.get('META_HEIGHT_KEYS_HINT', 'hoogte,height').split(',')
META_DIAM_KEYS_HINT    = os.environ.get('META_DIAM_KEYS_HINT', 'diameter,pot,ø,⌀').split(',')
META_DEBUG_MAPPING     = os.environ.get('META_DEBUG_MAPPING', '1') not in ('0', 'false', 'False')

# Mirroring (altijd beide hoogtes schrijven; 1 diameter)
META_MIRROR_MAX_HEIGHT = int(os.environ.get('META_MIRROR_MAX_HEIGHT', '2'))  # schrijf naar max 2 hoogtevelden
META_MIRROR_MAX_DIAM   = int(os.environ.get('META_MIRROR_MAX_DIAM', '1'))    # schrijf naar max 1 diameterveld

# Transactiefocus (koopwoorden in meta's) – standaard UIT, USP's inclusief €49-drempel
TRANSACTIONAL_MODE = os.environ.get('TRANSACTIONAL_MODE', '0') not in ('0', 'false', 'False')
TRANSACTIONAL_CLAIMS = [s.strip() for s in os.environ.get(
    'TRANSACTIONAL_CLAIMS',
    'Gratis verzending vanaf €49|Vandaag besteld, snel in huis|30 dagen retour|Lokale kweker|Verse kwaliteit'
).split('|') if s.strip()]

# -------------------------------
# Helpers
# -------------------------------

def require_login(fn):
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect('/login')
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


def build_system_prompt(txn_mode: bool = False, txn_usps: Optional[List[str]] = None) -> str:
    """System prompt – titel-format + vaste secties + optionele transactiefocus voor meta's."""
    txn_usps = [u for u in (txn_usps or []) if u]

    base = (
        "Je bent een ervaren Nederlandstalige SEO-copywriter voor een plantenwebshop (Belle Flora). "
        "Schrijf klantgericht, natuurlijk en informatief. Optimaliseer subtiel voor SEO zonder keyword stuffing. "
        "Gebruik correcte plantennamen en wees feitelijk; verzin geen gegevens.\n\n"

        "TITELFORMAT – ALTIJD DIT PATROON GEBRUIKEN:\n"
        "  [Generieke naam] / [Latijnse naam] – ↕[hoogte in cm] – ⌀[pot diameter in cm]\n"
        "  Als de plant in een sierpot zit: voeg toe: '– in [kleur] pot'.\n"
        "  Voorbeeld zonder pot: Gatenplant / Monstera Deliciosa – ↕150cm – ⌀27\n"
        "  Voorbeeld met pot:   Gatenplant / Monstera Deliciosa – ↕150cm – ⌀27 – in bruine pot\n"
        "Regels:\n"
        "  • Gebruik altijd de generieke NL-naam + Latijnse naam in die volgorde.\n"
        "  • Hoogte en potdiameter alleen invullen als je ze aantoonbaar kunt afleiden uit titel, variant of beschrijving. "
        "    Als een waarde onbekend is, laat dat blok dan weg in de titel (liever niets dan gokken).\n"
        "  • Gebruik het ‘–’ (en dash) tussen blokken en de symbolen ↕ en ⌀.\n\n"

        "BESCHRIJVING – HTML-LAYOUT (vast stramien):\n"
        "  • Gebruik exact deze secties met <h3>-kopjes en 4 regels zonder bullets:\n"
        "    <h3>Beschrijving</h3> + 1 korte alinea; daarna\n"
        "    <h3>Eigenschappen & behoeften</h3>\n"
        "    <p>☀ Lichtbehoefte: …</p>\n"
        "    <p>∿ Waterbehoefte: …</p>\n"
        "    <p>⌂ Standplaats: …</p>\n"
        "    <p>☠ Giftigheid: …</p>\n"
        "  • Alleen schone HTML (<h3>, <p>, <strong>, <em>).\n\n"

        "SEO-UITGANGSPUNTEN:\n"
        "  • Lever ook een meta title (≤60 tekens) en meta description (≤155 tekens). Kort, duidelijk, klik-waardig.\n"
        "  • Elke tekst moet uniek zijn per product.\n\n"
    )

    txn = ""
    if txn_mode:
        usps = " • ".join(txn_usps) if txn_usps else ""
        txn = (
            "TRANSACTIONELE FOCUS VOOR META-TAGS:\n"
            "  • Gebruik koopgerichte trefwoorden (bijv. Koop, Bestel, Shop, Online, Nu) waar natuurlijk.\n"
            "  • Meta title (≤60): begin waar mogelijk met een koopwoord + productnaam; merk aan het eind mag als er ruimte is (| Belle Flora). "
            "Geen schreeuwerige hoofdletters of uitroeptekens.\n"
            "  • Meta description (≤155): voeg 1–2 korte USP's toe en een subtiele call-to-action (Bestel nu / Shop online). "
            "Gebruik alleen USP's uit de volgende lijst en verzin niets: "
            f"{usps if usps else '[geen USP-lijst opgegeven]'}\n"
            "  • Doe geen prijs- of leverbelofte tenzij expliciet in de USP-lijst vermeld.\n\n"
        )

    closing = (
        "OUTPUTFORMAAT (exact deze labels gebruiken):\n"
        "Nieuwe titel: …\n\n"
        "Beschrijving: … (HTML)\n\n"
        "Meta title: …\n"
        "Meta description: …\n"
    )

    return base + txn + closing


def openai_chat_with_backoff(system_prompt: str, user_prompt: str, model: str = DEFAULT_MODEL,
                             temperature: float = DEFAULT_TEMPERATURE) -> str:
    """ChatCompletion met exponentiële backoff (429 etc.)."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY ontbreekt als omgevingsvariabele.")

    url = 'https://api.openai.com/v1/chat/completions'
    body = {
        'model': model,
        'temperature': temperature,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
    }
    data = json.dumps(body).encode('utf-8')
    headers = {'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'}

    for attempt in range(OPENAI_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode('utf-8'))
                return payload['choices'][0]['message']['content']
        except urllib.error.HTTPError as e:
            code = getattr(e, 'code', None)
            if code == 429 and attempt < OPENAI_MAX_RETRIES - 1:
                time.sleep(2 ** attempt)  # backoff: 1, 2, 4, ...
                continue
            try:
                detail = e.read().decode('utf-8', 'ignore')
            except Exception:
                detail = ''
            raise RuntimeError(f"OpenAI call failed: HTTP {code}: {detail}")
        except Exception as e:
            if attempt < OPENAI_MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"OpenAI call failed: {e}")


def shopify_headers(token: str) -> Dict[str, str]:
    return {
        'X-Shopify-Access-Token': token,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }


def shopify_get_with_backoff(url: str, token: str, params: Dict[str, Any] = None) -> requests.Response:
    for attempt in range(SHOPIFY_MAX_RETRIES):
        r = requests.get(url, headers=shopify_headers(token), params=params or {}, timeout=60)
        if r.status_code == 429 and attempt < SHOPIFY_MAX_RETRIES - 1:
            retry_after = float(r.headers.get('Retry-After', 2 ** attempt))
            time.sleep(retry_after)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


def shopify_post_graphql_with_backoff(url: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    for attempt in range(SHOPIFY_MAX_RETRIES):
        r = requests.post(url, headers=shopify_headers(token), json=payload, timeout=60)
        if r.status_code == 429 and attempt < SHOPIFY_MAX_RETRIES - 1:
            retry_after = float(r.headers.get('Retry-After', 2 ** attempt))
            time.sleep(retry_after)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


def paged_shopify_get(path: str, token: str, limit: int = 250, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """Paginatie via since_id (REST Admin)"""
    params = dict(params or {})
    params['limit'] = min(limit, 250)
    since_id, out = 0, []
    while True:
        params['since_id'] = since_id
        url = f'https://{SHOPIFY_STORE_DOMAIN}{path}'
        r = shopify_get_with_backoff(url, token, params=params)
        data = r.json()
        key = None
        for k in ('custom_collections', 'smart_collections', 'products', 'collects'):
            if k in data:
                key = k
                break
        if not key:
            break
        items = data.get(key, [])
        if not items:
            break
        out.extend(items)
        since_id = items[-1]['id']
        if len(items) < params['limit']:
            break
    return out


def shopify_graphql_update_product(store_domain: str, access_token: str, product_id_int: int,
                                   new_title: str, new_desc_html: str,
                                   seo_title: str, seo_desc: str) -> Dict[str, Any]:
    """Update titel, descriptionHtml en SEO via GraphQL productUpdate."""
    gid = f"gid://shopify/Product/{int(product_id_int)}"
    url = f"https://{store_domain}/admin/api/2025-01/graphql.json"
    mutation = """
    mutation productSeoAndDesc($input: ProductInput!) {
      productUpdate(input: $input) {
        product { id title seo { title description } }
        userErrors { field message }
      }
    }"""
    variables = {
        "input": {
            "id": gid,
            "title": new_title if new_title else None,
            "descriptionHtml": new_desc_html if new_desc_html else None,
            "seo": {"title": seo_title or new_title, "description": seo_desc or ""}
        }
    }
    payload = {"query": mutation, "variables": variables}
    data = shopify_post_graphql_with_backoff(url, access_token, payload)
    user_errors = data.get("data", {}).get("productUpdate", {}).get("userErrors", [])
    if data.get("errors") or user_errors:
        raise RuntimeError(f"Shopify GraphQL error: {data}")
    return data["data"]["productUpdate"]["product"]


# ---------- Metafields helpers (auto-detect + correct type + fallback + mirroring) ----------

DIM_HEIGHT_RE = re.compile(r"(?:↕\s*)?(\d{1,3})\s*cm", re.I)
DIM_POT_RE    = re.compile(r"(?:[⌀ØO]\s*)?(\d{1,3})\s*(?:cm)?\b", re.I)

# Cache: per store mapping + kandidaten
_META_MAP_CACHE: Dict[str, Dict[str, Any]] = {}

def parse_dimensions_from_text(title: str, body_html: str) -> Dict[str, str]:
    """Haal hoogte & potdiameter uit titel/body (strings, bv. '150')."""
    text_body = re.sub(r"<[^>]+>", " ", body_html or "", flags=re.I)
    text = f"{title or ''}\n{text_body}"

    h = re.search(r"↕\s*(\d{1,3})\s*cm", text) or DIM_HEIGHT_RE.search(text)
    d = re.search(r"[⌀Ø]\s*(\d{1,3})\s*cm?", text) or DIM_POT_RE.search(text)

    out: Dict[str, str] = {}
    if h: out["height_cm"] = h.group(1)
    if d: out["pot_diameter_cm"] = d.group(1)
    return out


def _fetch_product_metafield_definitions(token: str, store_domain: str) -> List[Dict[str, Any]]:
    url = f"https://{store_domain}/admin/api/2025-01/graphql.json"
    query = """
    query defs {
      metafieldDefinitions(ownerType: PRODUCT, first: 250) {
        edges {
          node {
            name
            namespace
            key
            type { name }
          }
        }
      }
    }"""
    data = shopify_post_graphql_with_backoff(url, token, {"query": query})
    edges = (((data or {}).get("data") or {}).get("metafieldDefinitions") or {}).get("edges") or []
    return [e["node"] for e in edges if "node" in e]


def _rank_candidates(defs: List[Dict[str, Any]], hints: List[str]) -> List[Dict[str, Any]]:
    """Geef gescoorde lijst kandidaten; prefer 'Hoogte (cm)' & 'Hoogte' (en diameter-varianten)."""
    hints = [h.strip().lower() for h in hints if h.strip()]
    out = []
    for d in defs:
        name = (d.get("name") or "")
        key  = (d.get("key") or "")
        ns   = (d.get("namespace") or "")
        low_name, low_key = name.lower(), key.lower()
        if not any(h in low_name or h in low_key for h in hints):
            continue

        tname = (((d.get("type") or {}).get("name")) or "single_line_text_field").lower()
        score = 0
        # type voorkeur
        if "number_integer" in tname: score += 30
        elif "dimension" in tname:    score += 22
        elif "number_decimal" in tname: score += 18
        elif "single_line_text_field" in tname: score += 10

        # exacte voorkeuren
        nname = re.sub(r"\s+", " ", name).strip().lower()
        if low_key == "hoogte_cm" or nname in ("hoogte (cm)", "height (cm)"):
            score += 60
        elif low_key == "hoogte" or nname == "hoogte":
            score += 50
        if low_key in ("diameter_cm", "pot_diameter_cm") or nname in ("diameter (cm)", "pot diameter (cm)"):
            score += 60
        elif low_key == "diameter" or nname == "diameter":
            score += 50

        # semantische tweaks
        if low_key.endswith("_"): score -= 8  # vaak category-gebonden of legacy
        if "cm" in low_key or "cm" in low_name: score += 4
        if low_key in ("hoogte_cm", "diameter_cm", "pot_diameter_cm", "hoogte", "diameter"): score += 6
        if ns.lower() == "custom": score += 2

        out.append({
            "namespace": ns,
            "key": key,
            "name": name,
            "type": (d.get("type") or {}).get("name") or "single_line_text_field",
            "score": score
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def _ensure_meta_map(token: str, store_domain: str) -> Dict[str, Any]:
    """Bepaal (en cache) kandidatenlijsten + topkeuzes."""
    cache_key = store_domain
    if cache_key in _META_MAP_CACHE:
        return _META_MAP_CACHE[cache_key]

    defs = _fetch_product_metafield_definitions(token, store_domain)
    height_cands = _rank_candidates(defs, META_HEIGHT_KEYS_HINT or ["hoogte","height"])
    diam_cands   = _rank_candidates(defs, META_DIAM_KEYS_HINT or ["diameter","pot","ø","⌀"])

    # Fallbacks als er niets gevonden wordt
    if not height_cands:
        height_cands = [{"namespace": META_NAMESPACE_DEFAULT, "key": "height_cm",
                         "name": "height_cm", "type": "single_line_text_field", "score": 0}]
    if not diam_cands:
        diam_cands = [{"namespace": META_NAMESPACE_DEFAULT, "key": "pot_diameter_cm",
                       "name": "pot_diameter_cm", "type": "single_line_text_field", "score": 0}]

    meta_map = {
        "height_candidates": height_cands,
        "diam_candidates": diam_cands,
        "height_ns": height_cands[0]["namespace"], "height_key": height_cands[0]["key"], "height_type": height_cands[0]["type"],
        "diam_ns":   diam_cands[0]["namespace"],   "diam_key":   diam_cands[0]["key"],   "diam_type":   diam_cands[0]["type"],
    }
    _META_MAP_CACHE[cache_key] = meta_map
    return meta_map


def shopify_product_metafields(token: str, store_domain: str, product_id_int: int) -> Dict[str, Any]:
    """Lees huidige waarden volgens de beste gok (top-kandidaat)."""
    mm = _ensure_meta_map(token, store_domain)
    gid = f"gid://shopify/Product/{int(product_id_int)}"
    url = f"https://{store_domain}/admin/api/2025-01/graphql.json"
    query = """
    query getMeta($id: ID!, $nsH: String!, $keyH: String!, $nsD: String!, $keyD: String!) {
      product(id: $id) {
        h: metafield(namespace: $nsH, key: $keyH) { value }
        d: metafield(namespace: $nsD, key: $keyD) { value }
      }
    }"""
    variables = {"id": gid, "nsH": mm["height_ns"], "keyH": mm["height_key"], "nsD": mm["diam_ns"], "keyD": mm["diam_key"]}
    data = shopify_post_graphql_with_backoff(url, token, {"query": query, "variables": variables})
    p = (data.get("data") or {}).get("product") or {}
    return {"height_cm": (p.get("h") or {}).get("value"), "pot_diameter_cm": (p.get("d") or {}).get("value")}


def _encode_value_for_type(val_str: str, tname: str) -> str:
    """Converteer '45' naar juiste value-string per type."""
    t = (tname or "").lower()
    try:
        if t == "number_integer":
            return str(int(val_str))
        if t == "number_decimal":
            return str(float(val_str))
        if t == "dimension":
            return json.dumps({"value": float(val_str), "unit": "cm"})
    except Exception:
        pass
    return str(val_str)


def _try_set_one(token: str, store_domain: str, gid: str, ns: str, key: str, tname: str, value_str: str) -> tuple[bool, str]:
    """Probeer één metafield te zetten; retourneer (ok, foutmelding)."""
    url = f"https://{store_domain}/admin/api/2025-01/graphql.json"
    mutation = """
    mutation setOne($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields { namespace key type value }
        userErrors { field message }
      }
    }"""
    payload = {
        "query": mutation,
        "variables": {
            "metafields": [{
                "ownerId": gid,
                "namespace": ns,
                "key": key,
                "type": tname,
                "value": _encode_value_for_type(value_str, tname),
            }]
        }
    }
    data = shopify_post_graphql_with_backoff(url, token, payload)
    ue = (data.get("data", {}).get("metafieldsSet", {}) or {}).get("userErrors", [])
    if ue:
        return False, (ue[0].get("message") or str(ue))
    return True, ""


def shopify_set_product_metafields(token: str, store_domain: str, product_id_int: int,
                                   values: Dict[str, str]) -> Dict[str, List[str]]:
    """
    Schrijf metafields met fallback over meerdere kandidaten (mirroring).
    Retourneert {"height": ["ns.key [type]", ...], "diam": ["ns.key [type]", ...]}
    """
    if not values:
        return {"height": [], "diam": []}

    mm = _ensure_meta_map(token, store_domain)
    gid = f"gid://shopify/Product/{int(product_id_int)}"
    written = {"height": [], "diam": []}

    # Hoogte: spiegel naar max 2 kandidaten (bijv. 'hoogte_cm' + 'hoogte')
    if values.get("height_cm"):
        left = META_MIRROR_MAX_HEIGHT
        for d in mm["height_candidates"]:
            ok, msg = _try_set_one(token, store_domain, gid, d["namespace"], d["key"], d["type"], values["height_cm"])
            if ok:
                written["height"].append(f"{d['namespace']}.{d['key']} [{d['type']}]")
                left -= 1
                if left <= 0:
                    break
            else:
                if "Owner subtype does not match" in msg:
                    continue
                raise RuntimeError(f"metafieldsSet userErrors (height): {msg}")

    # Diameter: spiegel naar max 1 kandidaat
    if values.get("pot_diameter_cm"):
        left = META_MIRROR_MAX_DIAM
        for d in mm["diam_candidates"]:
            ok, msg = _try_set_one(token, store_domain, gid, d["namespace"], d["key"], d["type"], values["pot_diameter_cm"])
            if ok:
                written["diam"].append(f"{d['namespace']}.{d['key']} [{d['type']}]")
                left -= 1
                if left <= 0:
                    break
            else:
                if "Owner subtype does not match" in msg:
                    continue
                raise RuntimeError(f"metafieldsSet userErrors (diameter): {msg}")

    return written

# ---------- Einde Metafields helpers ----------


def split_ai_output(text: str) -> Dict[str, str]:
    """Parseer AI-output in titel, body_html, meta_title, meta_description."""
    lines = [l.strip() for l in text.splitlines()]
    blob = "\n".join(lines)

    def find_marker(name_variants: List[str]) -> str:
        for m in name_variants:
            if m.lower() in blob.lower():
                return m
        return ""

    markers = {
        'title': find_marker(['Nieuwe titel:', 'Titel:', 'SEO titel:', 'Nieuwe SEO-titel:']),
        'body': find_marker(['Beschrijving:', 'Body:', 'Productbeschrijving:', 'Gestandaardiseerde beschrijving:']),
        'meta_title': find_marker(['Meta title:', 'SEO-meta title:', 'Title tag:']),
        'meta_desc': find_marker(['Meta description:', 'SEO-meta description:', 'Description tag:']),
    }

    def extract(start_marker: str, end_markers: List[str]) -> str:
        if not start_marker:
            return ""
        start = blob.lower().find(start_marker.lower())
        if start == -1:
            return ""
        start += len(start_marker)
        end_positions = []
        for m in end_markers:
            if not m:
                continue
            p = blob.lower().find(m.lower(), start)
            if p != -1:
                end_positions.append(p)
        end = min(end_positions) if end_positions else len(blob)
        return blob[start:end].strip().strip('-:').strip()

    title = extract(markers['title'], [markers['body'], markers['meta_title'], markers['meta_desc']])
    body = extract(markers['body'], [markers['meta_title'], markers['meta_desc']])
    meta_title = extract(markers['meta_title'], [markers['meta_desc']])
    meta_desc = extract(markers['meta_desc'], [])

    if not title and not body and not meta_title and not meta_desc:
        parts = [p.strip() for p in re.split(r"\n\s*\n", blob) if p.strip()]
        title = parts[0] if len(parts) > 0 else ''
        body = parts[1] if len(parts) > 1 else ''
        meta_title = parts[2] if len(parts) > 2 else title[:60]
        meta_desc = parts[3] if len(parts) > 3 else (body[:155] if body else title[:155])

    meta_title = (meta_title or title)[:60]
    meta_desc = (meta_desc or meta_title)[:155]

    # Body naar eenvoudige HTML als er geen tags in staan
    if body and not re.search(r"</?(p|h3|strong|em|br)\b", body, flags=re.I):
        safe = html.escape(body)
        body = (
            "<h3>Beschrijving</h3>\n"
            f"<p>{safe}</p>\n"
            "<h3>Eigenschappen & behoeften</h3>\n"
            "<p>☀ Lichtbehoefte: Onbekend</p>\n"
            "<p>∿ Waterbehoefte: Onbekend</p>\n"
            "<p>⌂ Standplaats: Onbekend</p>\n"
            "<p>☠ Giftigheid: Onbekend</p>"
        )

    return {
        'title': title,
        'body_html': body,
        'meta_title': meta_title,
        'meta_description': meta_desc,
    }

# -------------------------------
# UI (HTML)  (placeholders [[STORE]], [[MODEL]], [[BATCH]], [[DELAY]], [[TXNUSPS]], [[TXNCHECKED]])
# -------------------------------

LOGIN_HTML = '''<!doctype html><html lang="nl"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Inloggen – Belle Flora SEO Optimizer</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;background:#0b1020;color:#eef}
.card{max-width:820px;margin:48px auto;background:#121735;padding:24px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
h1{margin:0 0 12px 0}label{display:block;margin:12px 0 8px}
input{width:100%;padding:12px;border-radius:10px;border:1px solid #2a335a;background:#0f1430;color:#eef}
button{padding:12px 16px;border:0;border-radius:12px;background:#4f7dff;color:#fff;font-weight:600;cursor:pointer;margin-top:12px}
.muted{opacity:.9}
</style></head><body>
<div class="card">
  <h1>Inloggen</h1>
  <p class="muted">Voer je admin-gegevens in om verder te gaan.</p>
  <form method="post" action="/login">
    <label>Gebruikersnaam</label><input name="username" placeholder="michiel" required />
    <label>Wachtwoord</label><input name="password" type="password" required />
    <button type="submit">Inloggen</button>
  </form>
</div></body></html>'''

DASHBOARD_HTML = '''<!doctype html><html lang="nl"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Belle Flora SEO Optimizer</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;background:#0b1020;color:#eef}
.wrap{max-width:1100px;margin:28px auto;padding:0 16px}
.card{background:#121735;padding:20px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.35);margin-bottom:16px}
h1{margin:0 0 12px 0}
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
    <div>
      <label>Store domein</label>
      <input id="store" value="[[STORE]]" />
    </div>
    <div>
      <label>Model (server-side)</label>
      <input id="model" value="[[MODEL]]" />
    </div>
  </div>
  <div class="row">
    <div>
      <label>Shopify Access Token</label>
      <input id="token" placeholder="shpat_..." />
    </div>
    <div>
      <label>Optionele extra prompt</label>
      <input id="prompt" placeholder="Extra richtlijnen..." />
    </div>
  </div>

  <div class="row">
    <div>
      <label><input type="checkbox" id="txn"> Transactiefocus (koopwoorden in meta’s)</label>
    </div>
    <div>
      <label>USP’s voor meta’s (scheid met |)</label>
      <input id="txn_usps" placeholder="Gratis verzending vanaf €49|Vandaag besteld, snel in huis|30 dagen retour" value="[[TXNUSPS]]" />
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
    <button onclick="optimizeSelected()">Optimaliseer geselecteerde producten</button>
  </div>
</div>

<div class="card">
  <small>Live status (batch=[[BATCH]], delay=[[DELAY]]s, model=server-side)</small>
  <pre id="status">Klaar om te starten…</pre>
</div>

</div>
<script>
const qs = s => document.querySelector(s);
function setLog(t){qs('#status').textContent = t}
function addLog(t){qs('#status').textContent += '\\n' + t}

const DEFAULT_TXN_CHECKED = [[TXNCHECKED]]; // boolean literal (true/false)
window.addEventListener('DOMContentLoaded', () => {
  qs('#txn').checked = DEFAULT_TXN_CHECKED;
});

async function loadCollections(){
  setLog('Collecties laden…');
  const res = await fetch('/api/collections', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      store: qs('#store').value.trim(),
      token: qs('#token').value.trim()
    })
  });
  const data = await res.json();
  if(data.error){ setLog('❌ ' + data.error); return; }
  const sel = qs('#collections'); sel.innerHTML = '';
  (data.collections || []).forEach(c => {
    const opt = document.createElement('option');
    opt.value = String(c.id);
    opt.textContent = `${c.title} (#${c.id})`;
    sel.appendChild(opt);
  });
  qs('#cstatus').textContent = `${(data.collections||[]).length} collecties geladen`;
  addLog('Collecties geladen.');
}

async function optimizeSelected(){
  setLog('Start optimalisatie…');
  const ids = Array.from(qs('#collections').selectedOptions).map(o => o.value);
  const res = await fetch('/api/optimize', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      store: qs('#store').value.trim(),
      token: qs('#token').value.trim(),
      model: qs('#model').value.trim() || 'gpt-4o-mini',
      prompt: qs('#prompt').value,
      collection_ids: ids,
      txn: qs('#txn').checked,
      txn_usps: qs('#txn_usps').value
    })
  });
  const rd = res.body.getReader(); const dec = new TextDecoder();
  while(true){
    const {value, done} = await rd.read();
    if(done) break;
    addLog(dec.decode(value));
  }
}
</script>
</body></html>'''

# -------------------------------
# Routes
# -------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return Response(LOGIN_HTML, mimetype='text/html')
    if request.form.get('username') == ADMIN_USERNAME and request.form.get('password') == ADMIN_PASSWORD:
        session['logged_in'] = True
        return redirect('/dashboard')
    return Response(LOGIN_HTML, mimetype='text/html', status=401)


@app.route('/')
def root():
    if not session.get('logged_in'):
        return redirect('/login')
    return redirect('/dashboard')


@app.route('/dashboard')
@require_login
def dashboard():
    html = (DASHBOARD_HTML
            .replace('[[STORE]]', SHOPIFY_STORE_DOMAIN)
            .replace('[[MODEL]]', DEFAULT_MODEL)
            .replace('[[BATCH]]', str(BATCH_SIZE))
            .replace('[[DELAY]]', f"{DELAY_PER_PRODUCT:.1f}")
            .replace('[[TXNUSPS]]', " | ".join(TRANSACTIONAL_CLAIMS))
            .replace('[[TXNCHECKED]]', 'true' if TRANSACTIONAL_MODE else 'false'))
    return Response(html, mimetype='text/html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/api/collections', methods=['POST'])
@require_login
def api_collections():
    global SHOPIFY_STORE_DOMAIN  # belangrijk: vóór gebruik declareren

    payload = request.get_json(force=True)
    store = (payload.get('store') or SHOPIFY_STORE_DOMAIN).strip()
    token = (payload.get('token') or '').strip()

    SHOPIFY_STORE_DOMAIN = store  # update de globale store voor volgende calls

    if not token:
        return jsonify({'error': 'Geen Shopify token meegegeven.'}), 400

    customs = paged_shopify_get('/admin/api/2024-07/custom_collections.json', token)
    smarts = paged_shopify_get('/admin/api/2024-07/smart_collections.json', token)
    cols = [{'id': c['id'], 'title': c.get('title', '(zonder titel)')} for c in (customs + smarts)]
    return jsonify({'collections': cols})


@app.route('/api/optimize', methods=['POST'])
@require_login
def api_optimize():
    payload = request.get_json(force=True)
    store = (payload.get('store') or SHOPIFY_STORE_DOMAIN).strip()
    token = (payload.get('token') or '').strip()
    model = (payload.get('model') or DEFAULT_MODEL).strip()
    user_prompt_extra = (payload.get('prompt') or '').strip()
    collection_ids = payload.get('collection_ids') or []

    # Transactiefocus-opties
    if 'txn' in payload:
        txn_mode = bool(payload.get('txn'))
    else:
        txn_mode = TRANSACTIONAL_MODE
    txn_usps_input = payload.get('txn_usps') or ""
    txn_usps = [s.strip() for s in txn_usps_input.split('|') if s.strip()] or TRANSACTIONAL_CLAIMS

    if not token:
        return Response("Shopify token ontbreekt.\n", mimetype='text/plain', status=400)
    if not OPENAI_API_KEY:
        return Response("OPENAI_API_KEY ontbreekt in de server-omgeving.\n", mimetype='text/plain', status=500)

    sys_prompt = build_system_prompt(txn_mode=txn_mode, txn_usps=txn_usps)

    def generate():
        try:
            # 0) Toon (top) mapping – en de eerste 2 hoogte-kandidaten
            mm_dbg = _ensure_meta_map(token, store)
            if META_DEBUG_MAPPING:
                def _fmt(d): return f"{d['namespace']}.{d['key']} [{d['type']}]"
                h2 = ", ".join(map(_fmt, mm_dbg.get("height_candidates", [])[:2])) or "-"
                d1 = ", ".join(map(_fmt, mm_dbg.get("diam_candidates", [])[:1])) or "-"
                yield f"Metafields mapping → Hoogte-kandidaten: {h2}; Diameter-kandidaat: {d1}\n"

            # 1) Product-IDs verzamelen
            all_product_ids: List[int] = []
            if collection_ids:
                for cid in collection_ids:
                    collects = paged_shopify_get('/admin/api/2024-07/collects.json', token, params={'collection_id': cid})
                    pids = [int(c['product_id']) for c in collects]
                    all_product_ids.extend(pids)
                yield f"Collecties: {len(collection_ids)} geselecteerd – {len(all_product_ids)} producten gevonden\n"
            else:
                prods = paged_shopify_get('/admin/api/2024-07/products.json', token)
                all_product_ids = [int(p['id']) for p in prods]
                yield f"Geen collectie geselecteerd – hele shop: {len(all_product_ids)} producten gevonden\n"

            yield f"Instellingen: batch={BATCH_SIZE}, delay={DELAY_PER_PRODUCT:.1f}s, model={model} (server-side), transactiefocus={'aan' if txn_mode else 'uit'}\n"

            # 2) In batches productdetails ophalen
            processed = 0
            for i in range(0, len(all_product_ids), BATCH_SIZE):
                batch_ids = all_product_ids[i:i + BATCH_SIZE]
                ids_param = ','.join(map(str, batch_ids))
                url = f'https://{store}/admin/api/2024-07/products.json'
                r = shopify_get_with_backoff(url, token, params={'ids': ids_param, 'limit': 250})
                prods = r.json().get('products', [])

                for p in prods:
                    pid = int(p['id'])
                    title = p.get('title', '') or ''
                    body_html = p.get('body_html', '') or ''
                    tags = p.get('tags', '') or ''

                    base_prompt = textwrap.dedent(f"""
                        Originele titel: {title}
                        Originele beschrijving (HTML toegestaan): {body_html}
                        Tags: {tags}

                        Taken:
                        1) Lever een nieuwe titel volgens het opgelegde TITELFORMAT.
                        2) Lever een gestandaardiseerde productbeschrijving (200–250 woorden) in schone HTML met exact:
                           <h3>Beschrijving</h3>
                           <p>…korte inleiding (2–3 zinnen)…</p>
                           <h3>Eigenschappen & behoeften</h3>
                           <p>☀ Lichtbehoefte: …</p>
                           <p>∿ Waterbehoefte: …</p>
                           <p>⌂ Standplaats: …</p>
                           <p>☠ Giftigheid: …</p>
                           (géén bullets, géén <ul>/<li>; gebruik uitsluitend <h3> en <p> in dit blok)
                        3) Lever een Meta title (max 60 tekens).
                        4) Lever een Meta description (max 155 tekens).

                        Output EXACT in dit formaat:
                        Nieuwe titel: …

                        Beschrijving: … (HTML)

                        Meta title: …
                        Meta description: …
                    """).strip()

                    final_prompt = (user_prompt_extra + "\n\n" + base_prompt).strip() if user_prompt_extra else base_prompt

                    try:
                        yield f"→ #{pid}: AI-tekst genereren...\n"
                        out = openai_chat_with_backoff(sys_prompt, final_prompt, model=model, temperature=DEFAULT_TEMPERATURE)
                        pieces = split_ai_output(out)

                        # 4a) Shopify: titel/HTML/SEO updaten
                        _ = shopify_graphql_update_product(
                            store_domain=store,
                            access_token=token,
                            product_id_int=pid,
                            new_title=pieces['title'] or title,
                            new_desc_html=pieces['body_html'] or body_html,
                            seo_title=pieces['meta_title'],
                            seo_desc=pieces['meta_description'],
                        )

                        # 4b) Metafields bijwerken – ALTIJD schrijven als we een waarde konden parsen
                        try:
                            parsed = parse_dimensions_from_text(pieces['title'] or title, pieces['body_html'] or body_html)
                            to_write: Dict[str, str] = {}
                            if parsed.get("height_cm"):
                                to_write["height_cm"] = parsed["height_cm"]
                            if parsed.get("pot_diameter_cm"):
                                to_write["pot_diameter_cm"] = parsed["pot_diameter_cm"]

                            if to_write:
                                written = shopify_set_product_metafields(token, store, pid, to_write)
                                wlog = []
                                if written.get("height"): wlog.append("hoogte→ " + ", ".join(written["height"]))
                                if written.get("diam"):   wlog.append("diameter→ " + ", ".join(written["diam"]))
                                yield f"   • Metafields gezet: {to_write}  ({'; '.join(wlog) or 'geen matches'})\n"
                            else:
                                yield "   • Geen hoogte/diameter herkend in titel/body\n"
                        except Exception as me:
                            yield f"   • Metafields overslaan (fout): {me}\n"

                        processed += 1
                        short_title = (pieces['title'] or title)[:120]
                        yield f"✅ #{pid} bijgewerkt: {short_title}\n"

                    except Exception as e:
                        yield f"❌ OpenAI/Shopify-fout bij product #{pid}: {e}\n"

                    time.sleep(DELAY_PER_PRODUCT)

                yield f"-- Batch klaar ({len(prods)} producten) --\n"

            yield f"\nKlaar. Totaal bijgewerkt: {processed}.\n"
        except Exception as e:
            yield f"⚠️ Beëindigd met fout: {e}\n"

    return Response(generate(), mimetype='text/plain')


# -------------------------------
# Health
# -------------------------------

@app.route('/healthz')
def healthz():
    return "ok", 200


# -------------------------------
# Main (lokaal)
# -------------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    app.run(host='0.0.0.0', port=port, debug=False)
