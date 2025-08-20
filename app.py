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
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "gpt-4o-mini")
DEFAULT_TEMPERATURE = float(os.environ.get("DEFAULT_TEMPERATURE", 0.7))

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
    """Chat Completions via REST; geen 'openai' package nodig."""
    if not OPENAI_API_KEY:
        print("[OpenAI] OPENAI_API_KEY ontbreekt")
        return ""

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": DEFAULT_MODEL,               # bijv. gpt-4o-mini
        "temperature": DEFAULT_TEMPERATURE,
        "messages": [
            {"role": "system", "content": "Jij bent een e-commerce SEO specialist."},
            {"role": "user", "content": prompt},
        ],
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=120)
        r.raise_for_status()
        data = r.json()
        return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:
        print(f"[OpenAI REST error] {e}")
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
def dashboard():
    return Response("""<!doctype html>
<html lang="nl"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Belle Flora Optimizer – Dashboard</title>
<style>
:root{--bg:#0b1020;--card:#121735;--txt:#eaf0ff;--muted:#a9b1d6;--btn:#4f7dff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1100px;margin:28px auto;padding:0 16px}
.card{background:var(--card);border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.35);padding:20px;margin-bottom:16px}
h1{margin:0 0 8px 0}label{display:block;margin:10px 0 6px}
input,textarea,select{width:100%;padding:12px;border-radius:10px;border:1px solid #2a335a;background:#0f1430;color:var(--txt)}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
button{padding:12px 16px;border:0;border-radius:12px;background:var(--btn);color:#fff;font-weight:600;cursor:pointer}
.pill{display:inline-block;padding:6px 10px;border-radius:999px;background:#243165;margin-left:8px;font-size:12px}
pre{white-space:pre-wrap;max-height:360px;overflow:auto;background:#0f1430;border-radius:12px;padding:12px}
small{color:var(--muted)}
ul{margin:8px 0 0 16px;padding:0}
</style></head><body>
<div class="wrap">

  <div class="card">
    <h1>Belle Flora Optimizer</h1>
    <small>Snelle test & batch optimalisatie. Gebruik env-vars of vul store/token hier in.</small>
    <div class="row">
      <div>
        <label>Shopify Store domein <small>(optioneel – standaard via env)</small></label>
        <input id="store" placeholder="belle-flora-be.myshopify.com"/>
      </div>
      <div>
        <label>Shopify Access Token <small>(optioneel – standaard via env)</small></label>
        <input id="token" placeholder="shpat_..." />
      </div>
    </div>
    <div style="margin-top:8px">
      <button id="btnLoad" onclick="loadCollections()">Collecties laden</button>
      <span id="cstatus" class="pill">Nog niet geladen</span>
    </div>
  </div>

  <div class="card">
    <h2>Optimaliseer één product</h2>
    <div class="row">
      <div>
        <label>Product ID</label>
        <input id="pid" placeholder="bijv. 9075892486402"/>
      </div>
      <div>
        <label>Toepassen op Shopify?</label>
        <select id="applySingle">
          <option value="true">Ja – wijzigingen doorvoeren</option>
          <option value="false">Nee – alleen voorbeeld</option>
        </select>
      </div>
    </div>
    <div style="margin-top:10px">
      <button id="btnRun" onclick="optimizeOne()">Optimaliseer product</button>
    </div>
  </div>

  <div class="card">
    <h2>Batch optimalisatie (op basis van collecties)</h2>
    <div class="row">
      <div>
        <label>Beschikbare collecties <small>(meervoudige selectie)</small></label>
        <select id="collections" multiple size="10" style="height:220px;width:100%"></select>
      </div>
      <div>
        <label>Opties</label>
        <ul>
          <li><label><input type="checkbox" id="applyBatch" checked> Wijzigingen doorvoeren op Shopify</label></li>
          <li><label><input type="checkbox" id="stopOnError"> Stoppen bij eerste fout</label></li>
          <li><label><input type="checkbox" id="slowMode"> Pauze 2s per item (rustig)</label></li>
        </ul>
        <div style="margin-top:8px">
          <button id="btnBatch" onclick="runBatch()">Start batch</button>
          <button id="btnCancel" onclick="cancelBatch()" disabled>Annuleer</button>
        </div>
        <div style="margin-top:10px"><small id="batchHint">Selecteer 1..n collecties en klik Start.</small></div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Live status</h2>
    <pre id="log">Klaar om te starten…</pre>
  </div>

</div>
<script>
const qs = s => document.querySelector(s);
function setLog(t){qs('#log').textContent = t}
function addLog(t){qs('#log').textContent += '\\n' + t}

async function loadCollections(){
  setLog('Collecties laden…');
  try{
    const body = {};
    const store = qs('#store').value.trim();
    const token = qs('#token').value.trim();
    if(store) body.store = store;
    if(token) body.token = token;

    const res = await fetch('/api/collections', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    const data = await res.json().catch(()=>null);
    if(!res.ok){ addLog('❌ Fout ' + res.status + ': ' + (data && data.error || 'onbekend')); return; }
    const list = Array.isArray(data) ? data : (data.collections || data || []);
    const sel = qs('#collections'); sel.innerHTML = '';
    list.forEach(c => {
      const o = document.createElement('option');
      o.value = String(c.id); o.textContent = `${c.title} (#${c.id})`;
      sel.appendChild(o);
    });
    qs('#cstatus').textContent = `${list.length} collecties geladen`;
    addLog('✅ Collecties geladen.');
  }catch(e){ addLog('❌ Netwerkfout: ' + e.message); }
}

/* Single product */
async function optimizeOne(){
  const pid = qs('#pid').value.trim();
  if(!pid){ setLog('Geef een Product ID op.'); return; }
  setLog('Optimaliseren van product #' + pid + '…');
  try{
    const apply = qs('#applySingle').value === 'true';
    const body = { product_id: Number(pid), apply };
    const store = qs('#store').value.trim();
    const token = qs('#token').value.trim();
    if(store) body.store = store;
    if(token) body.token = token;

    const res = await fetch('/api/optimize', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    const txt = await res.text();
    try{ setLog(JSON.stringify(JSON.parse(txt), null, 2)); }
    catch(_){ setLog(txt); }
  }catch(e){ addLog('❌ Netwerkfout: ' + e.message); }
}

/* Batch */
let CANCEL=false;
function cancelBatch(){ CANCEL = true; qs('#btnCancel').disabled = true; addLog('⏹ Batch geannuleerd.'); }

async function runBatch(){
  const selected = Array.from(qs('#collections').selectedOptions).map(o => o.value);
  if(selected.length === 0){ setLog('Selecteer minimaal één collectie.'); return; }

  CANCEL = false; qs('#btnCancel').disabled = false;
  const apply = qs('#applyBatch').checked;
  const stopOnError = qs('#stopOnError').checked;
  const slow = qs('#slowMode').checked;

  const store = qs('#store').value.trim();
  const token = qs('#token').value.trim();

  let total=0, done=0, failed=0;
  setLog('Batch gestart…');

  for(const collId of selected){
    if(CANCEL) break;

    /* 1) product-id's ophalen voor de collectie */
    addLog(`→ Collectie #${collId}: product-id's ophalen…`);
    const body = { collection_id: Number(collId) };
    if(store) body.store = store;
    if(token) body.token = token;

    let prods = [];
    try{
      const r = await fetch('/api/collection_products', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(body)
      });
      const j = await r.json();
      if(!r.ok) throw new Error(j && j.error || ('HTTP '+r.status));
      prods = (j.product_ids || []);
      total += prods.length;
      addLog(`   • ${prods.length} producten gevonden`);
    }catch(e){
      addLog(`   ❌ Fout collectie ${collId}: ${e.message}`);
      if(stopOnError){ addLog('Batch gestopt.'); qs('#btnCancel').disabled = true; return; }
      continue;
    }

    /* 2) sequential optimize per product-id */
    for(const pid of prods){
      if(CANCEL) break;

      try{
        const payload = { product_id: pid, apply };
        if(store) payload.store = store;
        if(token) payload.token = token;

        const res = await fetch('/api/optimize', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify(payload)
        });
        const txt = await res.text();
        if(!res.ok){ throw new Error('HTTP '+res.status+' '+txt); }

        done++;
        const label = txt.startsWith('{') ? (JSON.parse(txt).result?.title || `#${pid}`) : `#${pid}`;
        addLog(`   ✅ ${done}/${total} bijgewerkt: ${label}`);
        if(slow) await new Promise(r=>setTimeout(r,2000));
      }catch(e){
        failed++;
        addLog(`   ❌ Fout bij product #${pid}: ${e.message}`);
        if(stopOnError){ addLog('Batch gestopt door stop-on-error.'); qs('#btnCancel').disabled = true; return; }
      }
    }
  }

  qs('#btnCancel').disabled = true;
  addLog(`Klaar. Successen: ${done}, Fouten: ${failed}, Totaal: ${total}.`);
}
</script>
</body></html>
""", mimetype="text/html")


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
from flask import request, jsonify, Response

# Helper: eenvoudige Shopify GET met backoff (gebruik evt. je bestaande helper)
def _shopify_get(url, token, params=None, timeout=60):
    import time, requests
    for i in range(3):
        r = requests.get(url, headers={"X-Shopify-Access-Token": token}, params=params or {}, timeout=timeout)
        if r.status_code == 429 and i < 2:
            time.sleep(2 ** i)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r

@app.route("/api/collection_products", methods=["POST"])
def api_collection_products():
    """
    Body JSON: { "collection_id": 1234567890, "store": "...(opt)", "token": "...(opt)" }
    Response: { "collection_id": ..., "count": N, "product_ids": [ ... ] }
    """
    try:
        data = request.get_json(force=True) or {}
        store = (data.get("store") or os.environ.get("SHOPIFY_STORE_DOMAIN") or "").strip()
        token = (data.get("token") or os.environ.get("SHOPIFY_ACCESS_TOKEN") or "").strip()
        coll_id = data.get("collection_id")
        if not (store and token and coll_id):
            return jsonify({"error": "store/token/collection_id vereist"}), 400

        # paginate collects → product_ids
        product_ids, since_id = [], 0
        while True:
            r = _shopify_get(
                f"https://{store}/admin/api/2024-07/collects.json",
                token,
                params={"collection_id": coll_id, "limit": 250, "since_id": since_id},
                timeout=60
            )
            js = r.json()
            collects = js.get("collects", []) or []
            if not collects:
                break
            product_ids.extend([int(c["product_id"]) for c in collects])
            since_id = collects[-1]["id"]
            if len(collects) < 250:
                break

        product_ids = sorted(set(product_ids))
        return jsonify({"collection_id": coll_id, "count": len(product_ids), "product_ids": product_ids})

    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", 502)
        return jsonify({"error": f"Shopify HTTP {code}: {getattr(e.response, 'text', str(e))}"}), code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
