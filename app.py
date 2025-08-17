# app.py
# Belle Flora SEO Optimizer – Flask (Render/Railway ready)
# - Login via env vars
# - OpenAI key ALLEEN uit env (geen UI veld)
# - Collectie-selectie + batching + backoff
# - Netjes geformatteerde HTML-beschrijvingen
# - Shopify productUpdate (titel, descriptionHtml, SEO)

import os
import json
import time
import math
import html
import textwrap
import urllib.request
import urllib.error
from typing import Dict, Any, List

import requests
from flask import Flask, request, session, redirect, Response, jsonify

# -------------------------
# Config
# -------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(32))

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "michiel")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "CHANGE_ME")
SHOPIFY_STORE_DOMAIN = os.environ.get("SHOPIFY_STORE_DOMAIN", "your-store.myshopify.com")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")  # Optie 1: UIT ENV (veilig)
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "gpt-4o-mini")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "8"))
DELAY_SECONDS = float(os.environ.get("DELAY_SECONDS", "2.5"))  # pauze tussen producten

# -------------------------
# Helpers
# -------------------------
def require_login(fn):
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/login")
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


def shopify_headers(token: str) -> Dict[str, str]:
    return {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def paged_shopify_get(path: str, token: str, limit: int = 250, params: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    """ Paged GET helper voor Shopify REST endpoints. """
    params = dict(params or {})
    params["limit"] = min(limit, 250)
    since_id = 0
    out: List[Dict[str, Any]] = []
    while True:
        params["since_id"] = since_id
        url = f"https://{SHOPIFY_STORE_DOMAIN}{path}"
        r = requests.get(url, headers=shopify_headers(token), params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        key = None
        for k in ("custom_collections", "smart_collections", "products", "collects"):
            if k in data:
                key = k
                break
        if not key:
            break
        items = data.get(key, [])
        if not items:
            break
        out.extend(items)
        since_id = items[-1]["id"]
        if len(items) < params["limit"]:
            break
    return out


def product_graphql_update(token: str, product_id_int: int, new_title: str, new_desc_html: str,
                           seo_title: str, seo_desc: str) -> Dict[str, Any]:
    """Update titel, descriptionHtml en SEO via GraphQL productUpdate."""
    gid = f"gid://shopify/Product/{int(product_id_int)}"
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2025-01/graphql.json"
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
            "title": new_title,
            "descriptionHtml": new_desc_html,
            "seo": {"title": seo_title, "description": seo_desc}
        }
    }
    r = requests.post(url, headers=shopify_headers(token), json={"query": mutation, "variables": variables}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if data.get("errors") or data.get("data", {}).get("productUpdate", {}).get("userErrors"):
        raise RuntimeError(f"Shopify GraphQL error: {data}")
    return data["data"]["productUpdate"]["product"]


def ai_call_json(system_prompt: str, user_prompt: str, model: str) -> Dict[str, Any]:
    """Doet een OpenAI Chat Completions call en verwacht STRIKT JSON als antwoord."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY ontbreekt in environment variables.")

    url = "https://api.openai.com/v1/chat/completions"
    body = {
        "model": model,
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"}
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        return json.loads(content)  # strikt JSON


def build_user_prompt(title: str, body_html: str, tags: str, extra: str) -> str:
    """Bouwt de gebruikersprompt die terugkomt als gestructureerde JSON."""
    # Korte instructie om HTML te maken die mooi en eenvoudig is.
    rules = """
    Je schrijft in het Nederlands voor Belle Flora (webshop met kamer- en tuinplanten, levering aan huis).
    Optimaliseer voor SEO zonder onnatuurlijk te klinken. Gebruik de relevante keywords voor kamerplanten
    en tuinplanten (en alléén voor de collectie 'boeketten': bloemen/boeketten).
    Houd de opmaak schoon en crawlbaar: <p>, <ul>, <li>, <strong>, <em>, <h3>. Geen inline CSS, geen scripts,
    geen externe links. Gebruik korte paragrafen en bulletpoints waar nuttig.
    """
    schema = """
    Geef je antwoord ALLEEN als geldig JSON-object met exact deze velden:
    {
      "title": "nieuwe producttitel",
      "body_html": "<p>HTML...</p>",
      "meta_title": "max 60 tekens",
      "meta_description": "max 155 tekens"
    }
    """
    base = f"""
Originele titel: {title}
Originele beschrijving (HTML toegestaan): {body_html}
Tags: {tags}

Extra richtlijnen (optioneel):
{extra or "(geen)"}

Taken:
1) Schrijf een nieuwe, sterke SEO-titel.
2) Schrijf een nette HTML-beschrijving met:
   - korte intro
   - <h3>Eigenschappen</h3> + <ul> met 3–6 kernpunten (hoogte/standplaats/waterbehoefte/onderhoud/giftigheid enz.)
   - <h3>Verzorging</h3> + korte tips in <ul>
   - afsluitend <p> met bezorging aan huis door Belle Flora.
3) Maak een meta title (<=60) en meta description (<=155).

{rules}

{schema}
"""
    return textwrap.dedent(base).strip()


def clamp(s: str, maxlen: int) -> str:
    s = (s or "").strip()
    return s[:maxlen]


def backoff_sleep(attempt: int, base_delay: float = 2.0, jitter: float = 0.3):
    """Exponentiële backoff met lichte jitter."""
    delay = base_delay * (2 ** (attempt - 1))
    delay = delay * (1.0 + (jitter * (0.5 - os.urandom(1)[0] / 255)))  # random +-15%
    time.sleep(max(0.5, min(delay, 20.0)))


# -------------------------
# UI
# -------------------------
LOGIN_HTML = """<!doctype html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Inloggen – Belle Flora SEO Optimizer</title>
<style>
body{font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#0b1020;color:#eef;margin:0}
.card{max-width:880px;margin:40px auto;background:#121735;padding:24px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
label{display:block;margin:12px 0 6px}input{width:100%;padding:12px;border-radius:10px;border:1px solid #2a335a;background:#0f1430;color:#eef}
button{padding:12px 16px;border:0;border-radius:12px;background:#4f7dff;color:#fff;font-weight:600;cursor:pointer}
</style></head>
<body><div class="card">
<h1>Inloggen</h1>
<form method="post" action="/login">
<label>Gebruikersnaam</label><input name="username" required>
<label>Wachtwoord</label><input name="password" type="password" required>
<div style="margin-top:12px"><button type="submit">Login</button></div>
</form>
</div></body></html>
"""

DASHBOARD_HTML = """<!doctype html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Belle Flora SEO Optimizer</title>
<style>
body{font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#0b1020;color:#eef;margin:0}
.card{max-width:1100px;margin:24px auto;background:#121735;padding:24px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
h1{margin:0 0 8px} .muted{opacity:.85}
label{display:block;margin:12px 0 6px}
input,textarea,select{width:100%;padding:12px;border-radius:10px;border:1px solid #2a335a;background:#0f1430;color:#eef}
button{padding:12px 16px;border:0;border-radius:12px;background:#4f7dff;color:#fff;font-weight:600;cursor:pointer}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.status{margin-top:14px;white-space:pre-wrap;background:#0f1430;padding:12px;border-radius:10px;border:1px solid #2a335a;min-height:80px}
.pill{display:inline-block;padding:6px 10px;border-radius:999px;background:#243165;margin-left:10px}
.small{font-size:12px;opacity:.8}
</style></head>
<body><div class="card">
<h1>Belle Flora SEO Optimizer</h1>
<p class="muted small">Store: <strong>{store}</strong> • Model: <strong>{model}</strong> (server-side)</p>

<div class="row">
  <div>
    <label>Shopify Access Token</label>
    <input id="token" placeholder="shpat_..." />
  </div>
  <div>
    <label>Optionele extra prompt</label>
    <input id="extra" placeholder="Extra richtlijnen..." />
  </div>
</div>

<div style="margin:12px 0">
  <button onclick="loadCollections()">Collecties laden</button>
  <span id="cstatus" class="pill">Nog niet geladen</span>
</div>

<label>Selecteer collecties</label>
<select id="collections" multiple size="10"></select>

<div style="margin-top:16px">
  <button onclick="optimize()">Optimaliseer geselecteerde producten</button>
</div>

<pre id="status" class="status"></pre>

</div>
<script>
const qs = s => document.querySelector(s);
function setStatus(t){ qs('#status').textContent = t }
function addStatus(t){ qs('#status').textContent += '\\n' + t }

async function loadCollections(){
  setStatus('Collecties laden...');
  const token = qs('#token').value.trim();
  if(!token){ setStatus('Geef eerst je Shopify token in.'); return }
  const res = await fetch('/api/collections', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ token })
  });
  const data = await res.json();
  if(data.error){ setStatus('❌ ' + data.error); return }
  const sel = qs('#collections'); sel.innerHTML='';
  (data.collections || []).forEach(c => {
    const opt = document.createElement('option');
    opt.value = c.id; opt.textContent = `${c.title} (#${c.id})`;
    sel.appendChild(opt);
  });
  qs('#cstatus').textContent = `${(data.collections||[]).length} collecties geladen`;
  addStatus('Collecties geladen.');
}

async function optimize(){
  const token = qs('#token').value.trim();
  const extra = qs('#extra').value.trim();
  const ids = Array.from(qs('#collections').selectedOptions).map(o => o.value);
  if(!token){ setStatus('Geef eerst je Shopify token in.'); return }
  setStatus('Start optimalisatie...');

  const res = await fetch('/api/optimize', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ token, collection_ids: ids, extra })
  });

  const rd = res.body.getReader(); let dec = new TextDecoder();
  while(true){
    const {value, done} = await rd.read();
    if(done) break;
    addStatus(dec.decode(value));
  }
}
</script>
</body></html>
"""


# -------------------------
# Routes
# -------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return Response(LOGIN_HTML, mimetype="text/html")
    if request.form.get("username") == ADMIN_USERNAME and request.form.get("password") == ADMIN_PASSWORD:
        session["logged_in"] = True
        return redirect("/dashboard")
    return Response(LOGIN_HTML, mimetype="text/html", status=401)


@app.route("/")
def root():
    if not session.get("logged_in"):
        return redirect("/login")
    return redirect("/dashboard")


@app.route("/dashboard")
@require_login
def dashboard():
    html = DASHBOARD_HTML.replace("{store}", SHOPIFY_STORE_DOMAIN).replace("{model}", DEFAULT_MODEL)
    return Response(html, mimetype="text/html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/api/collections", methods=["POST"])
@require_login
def api_collections():
    payload = request.get_json(force=True)
    token = payload.get("token")
    if not token:
        return jsonify({"error": "Geen Shopify token meegegeven."}), 400

    customs = paged_shopify_get("/admin/api/2024-07/custom_collections.json", token)
    smarts = paged_shopify_get("/admin/api/2024-07/smart_collections.json", token)
    cols = [{"id": c["id"], "title": c.get("title", "(zonder titel)")} for c in (customs + smarts)]
    return jsonify({"collections": cols})


@app.route("/api/optimize", methods=["POST"])
@require_login
def api_optimize():
    payload = request.get_json(force=True)
    token = payload.get("token", "").strip()
    extra = (payload.get("extra") or "").strip()
    collection_ids = payload.get("collection_ids") or []

    if not token:
        return jsonify({"error": "Shopify token ontbreekt."}), 400
    if not OPENAI_API_KEY:
        return jsonify({"error": "OPENAI_API_KEY ontbreekt op de server."}), 500

    def generate():
        try:
            # Verzamel product-ids
            all_product_ids: List[int] = []
            if collection_ids:
                for cid in collection_ids:
                    collects = paged_shopify_get(
                        "/admin/api/2024-07/collects.json", token, params={"collection_id": cid}
                    )
                    pids = [c["product_id"] for c in collects]
                    all_product_ids.extend(pids)
                yield f"Collecties: {len(collection_ids)} geselecteerd – {len(all_product_ids)} producten gevonden\n"
            else:
                prods = paged_shopify_get("/admin/api/2024-07/products.json", token)
                all_product_ids = [p["id"] for p in prods]
                yield f"Geen collecties geselecteerd – hele shop ({len(all_product_ids)} producten)\n"

            total = len(all_product_ids)
            if total == 0:
                yield "Niets te doen.\n"
                return

            batch_size = max(1, BATCH_SIZE)
            yield f"Instellingen: batch={batch_size}, delay={DELAY_SECONDS:.1f}s, model={DEFAULT_MODEL} (server-side)\n"

            processed = 0
            for i in range(0, total, batch_size):
                batch_ids = all_product_ids[i:i + batch_size]
                ids_param = ",".join(map(str, batch_ids))
                url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2024-07/products.json"
                r = requests.get(
                    url, headers=shopify_headers(token), params={"ids": ids_param, "limit": 250}, timeout=60
                )
                r.raise_for_status()
                prods = r.json().get("products", [])

                for p in prods:
                    pid = p["id"]
                    title = p.get("title", "")
                    body_html = p.get("body_html", "") or ""
                    tags = p.get("tags", "") or ""

                    yield f"\n→ #{pid}: AI-tekst genereren...\n"

                    # AI call met backoff
                    attempts = 0
                    ai_ok = False
                    while attempts < 4 and not ai_ok:
                        attempts += 1
                        try:
                            sys_prompt = (
                                "Je bent een ervaren Nederlandstalige SEO-copywriter voor een plantenwebshop."
                                " Schrijf helder, vriendelijk en informatief; optimaliseer subtiel voor SEO."
                            )
                            user_prompt = build_user_prompt(title, body_html, tags, extra)
                            result = ai_call_json(sys_prompt, user_prompt, DEFAULT_MODEL)

                            new_title = clamp(result.get("title", "") or title, 140)
                            seo_title = clamp(result.get("meta_title", "") or new_title, 60)
                            seo_desc = clamp(result.get("meta_description", "") or "", 155)

                            # Zorg dat body_html geldig blijft (geen scripts/styles, simpele tags)
                            body = (result.get("body_html") or "").strip()
                            if not body:
                                # Fallback: maak iets eenvoudigs
                                safe_intro = html.escape(title)
                                body = f"<p>{safe_intro}</p>"

                            # Update product via GraphQL met backoff
                            shopify_attempts = 0
                            while shopify_attempts < 4:
                                shopify_attempts += 1
                                try:
                                    product_graphql_update(
                                        token=token,
                                        product_id_int=pid,
                                        new_title=new_title,
                                        new_desc_html=body,
                                        seo_title=seo_title,
                                        seo_desc=seo_desc,
                                    )
                                    ai_ok = True
                                    break
                                except requests.HTTPError as e:
                                    status = e.response.status_code if e.response is not None else 0
                                    if status == 429 or status >= 500:
                                        yield f"  • Shopify {status}, backoff (poging {shopify_attempts})...\n"
                                        backoff_sleep(shopify_attempts, base_delay=2.0)
                                        continue
                                    raise
                            if ai_ok:
                                processed += 1
                                yield f"✅ #{pid} bijgewerkt: {new_title[:70]}\n"
                            else:
                                yield f"❌ Shopify update bleef falen voor #{pid}\n"

                        except urllib.error.HTTPError as e:
                            if e.code == 429:
                                yield f"  • OpenAI 429: backoff (poging {attempts})...\n"
                                backoff_sleep(attempts, base_delay=3.0)
                                continue
                            raise
                        except Exception as e:
                            yield f"❌ OpenAI/parse fout bij #{pid}: {e}\n"
                            break  # niet eindeloos blijven proberen

                    # Pauze tussen producten
                    time.sleep(max(0.0, DELAY_SECONDS))

                done = min(i + batch_size, total)
                yield f"-- Batch klaar ({done}/{total}) --\n"

            yield f"\nKlaar. Totaal bijgewerkt: {processed}/{total}.\n"

        except Exception as e:
            yield f"⚠️ Gestopt met fout: {e}\n"

    return Response(generate(), mimetype="text/plain")


# -------------------------
# WSGI
# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
