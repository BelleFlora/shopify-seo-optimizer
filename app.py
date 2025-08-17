# Shopify SEO Optimizer – Flask app (Render/Railway ready)
# NL UI • Login via env vars • Tokens via UI • Streaming voortgang
# Collectie-selectie • Batching • Snelle modus voor kleine sets

import os
import json
import time
import textwrap
import urllib.request
from typing import List

import requests
from flask import Flask, request, session, redirect, Response, jsonify

# -----------------------------------------------------------------------------
# Config uit omgeving (Render: Environment Variables)
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(32))

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "michiel")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "CHANGE_ME")  # zet dit in Render
SHOPIFY_STORE_DOMAIN = os.environ.get("SHOPIFY_STORE_DOMAIN", "your-store.myshopify.com")

# Batches / streaming / timeouts
BATCH_SIZE = 20  # standaard batchgrootte (wordt 1 in snelle modus)

# ——— Streaming & snelheid ———
SMALL_COLLECTION_THRESHOLD = 5       # snelle modus bij kleine sets
FAST_OPENAI_PRE_SLEEP = 0.2          # korte pauze vóór AI-call
FAST_PER_PRODUCT_SLEEP = 0.4         # pauze na elk product in snelle modus
FAST_PER_BATCH_SLEEP = 0.0           # pauze na batch in snelle modus
HEARTBEAT_EVERY_SEC = 5.0            # elke X sec een puntje als heartbeat tijdens AI-wachttijd

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def require_login(fn):
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/login")
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


def shopify_headers(token: str):
    return {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _request_with_retry(
    method: str,
    url: str,
    headers=None,
    params=None,
    json=None,
    max_retries: int = 4,
    timeout: int = 60,
):
    """Retry helper met exponentiële backoff voor 429/5xx."""
    backoff = 1.0
    for attempt in range(max_retries):
        r = requests.request(method, url, headers=headers, params=params, json=json, timeout=timeout)
        if r.status_code < 400:
            return r
        if r.status_code == 429 or 500 <= r.status_code < 600:
            time.sleep(backoff)
            backoff *= 2
            continue
        r.raise_for_status()
    r.raise_for_status()
    return r  # type: ignore


def paged_shopify_get(path: str, token: str, limit: int = 250, params: dict | None = None):
    """Eenvoudige since_id paging over enkele endpoints."""
    params = dict(params or {})
    params["limit"] = min(limit, 250)
    since_id, out = 0, []
    while True:
        params["since_id"] = since_id
        url = f"https://{SHOPIFY_STORE_DOMAIN}{path}"
        r = _request_with_retry("GET", url, headers=shopify_headers(token), params=params)
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


def openai_chat(api_key: str, system_prompt: str, user_prompt: str, model: str = "gpt-4o-mini", temperature: float = 0.7):
    """Kleine wrapper rond OpenAI Chat Completions API."""
    url = "https://api.openai.com/v1/chat/completions"
    body = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    # retries (klein) voor netwerkfout
    backoff = 1.0
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                return payload["choices"][0]["message"]["content"]
        except Exception as e:
            time.sleep(backoff)
            backoff *= 2
            last = e
    raise RuntimeError(f"OpenAI call failed: {last}")


def shopify_graphql_update_product(
    store_domain: str,
    access_token: str,
    product_id_int: int,
    new_title: str,
    new_desc_html: str,
    seo_title: str,
    seo_desc: str,
):
    """Update titel, beschrijving en SEO via GraphQL productUpdate."""
    gid = f"gid://shopify/Product/{int(product_id_int)}"
    url = f"https://{store_domain}/admin/api/2025-01/graphql.json"
    mutation = """
    mutation productSeoAndDesc($input: ProductInput!) {
      productUpdate(input: $input) {
        product { id title seo { title description } }
        userErrors { field message }
      }
    }
    """
    variables = {
        "input": {
            "id": gid,
            "title": new_title,
            "descriptionHtml": new_desc_html,
            "seo": {"title": seo_title, "description": seo_desc},
        }
    }
    r = _request_with_retry(
        "POST",
        url,
        headers={"X-Shopify-Access-Token": access_token, "Content-Type": "application/json", "Accept": "application/json"},
        json={"query": mutation, "variables": variables},
    )
    data = r.json()
    if data.get("errors") or data.get("data", {}).get("productUpdate", {}).get("userErrors"):
        raise RuntimeError(f"Shopify GraphQL error: {data}")
    return data["data"]["productUpdate"]["product"]


def split_ai_output(text: str):
    """Probeer robuust de 4 velden uit de AI-output te trekken."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    blob = "\n".join(lines)

    def take(after):
        for a in after:
            if a.lower() in blob.lower():
                return a
        return None

    markers = {
        "title": take(["Nieuwe titel:", "Titel:", "SEO titel:", "Nieuwe SEO-titel:"]),
        "body": take(["Beschrijving:", "Body:", "Productbeschrijving:", "Gestandaardiseerde beschrijving:"]),
        "meta_title": take(["Meta title:", "SEO-meta title:", "Title tag:"]),
        "meta_desc": take(["Meta description:", "SEO-meta description:", "Description tag:"]),
    }

    title = body = meta_title = meta_desc = ""
    if all(markers.values()):
        def section(start_marker, end_markers):
            start = blob.lower().find(start_marker.lower())
            if start == -1:
                return ""
            start += len(start_marker)
            end_positions = []
            for m in end_markers:
                p = blob.lower().find(m.lower(), start)
                if p != -1:
                    end_positions.append(p)
            end = min(end_positions) if end_positions else len(blob)
            return blob[start:end].strip().strip("-:")

        title = section(markers["title"], [markers["body"], markers["meta_title"], markers["meta_desc"], "\n\n"])
        body = section(markers["body"], [markers["meta_title"], markers["meta_desc"], "\n\n"])
        meta_title = section(markers["meta_title"], [markers["meta_desc"], "\n\n"])
        meta_desc = section(markers["meta_desc"], ["\n\n"]) or meta_title
    else:
        parts = [p.strip() for p in blob.split("\n\n") if p.strip()]
        title = parts[0] if len(parts) > 0 else ""
        body = parts[1] if len(parts) > 1 else ""
        meta_title = parts[2] if len(parts) > 2 else title[:60]
        meta_desc = parts[3] if len(parts) > 3 else body[:155]

    return {
        "title": title.strip(),
        "body_html": body.strip(),
        "meta_title": meta_title.strip()[:60],
        "meta_description": meta_desc.strip()[:155],
    }

# -----------------------------------------------------------------------------
# HTML (login + dashboard)
# -----------------------------------------------------------------------------
INDEX_HTML = """<!doctype html><html lang="nl"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Shopify SEO Optimizer</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:24px;background:#0b1020;color:#eef}
.card{max-width:880px;margin:0 auto;background:#121735;padding:20px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
h1{margin-top:0}label{display:block;margin:12px 0 8px}
input{width:100%;padding:12px;border-radius:10px;border:1px solid #2a335a;background:#0f1430;color:#eef}
button{padding:12px 16px;border:0;border-radius:12px;background:#4f7dff;color:#fff;font-weight:600;cursor:pointer}
.muted{opacity:.85}
</style></head><body>
<div class="card">
  <h1>Shopify SEO Optimizer</h1>
  <p class="muted">Log in om door te gaan.</p>
  <form method="post" action="/login">
    <label>Gebruikersnaam</label>
    <input name="username" placeholder="michiel" required />
    <label>Wachtwoord</label>
    <input name="password" type="password" required />
    <div style="margin-top:12px"><button type="submit">Inloggen</button></div>
  </form>
</div>
</body></html>"""

DASHBOARD_HTML = """<!doctype html><html lang="nl"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SEO Optimizer – Dashboard</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:24px;background:#0b1020;color:#eef}
.card{max-width:980px;margin:0 auto;background:#121735;padding:20px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
h1{margin-top:0}label{display:block;margin:12px 0 8px}
input,textarea,select{width:100%;padding:12px;border-radius:10px;border:1px solid #2a335a;background:#0f1430;color:#eef}
button{padding:12px 16px;border:0;border-radius:12px;background:#4f7dff;color:#fff;font-weight:600;cursor:pointer}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.status{margin-top:14px;white-space:pre-wrap}
.pill{display:inline-block;padding:6px 10px;border-radius:999px;background:#243165;margin:6px 8px 0 0}
</style></head><body>
<div class="card">
  <h1>SEO Optimizer – Dashboard</h1>
  <div class="row">
    <div><label>Shopify store domein</label><input id="store" placeholder="{store}" value="{store}" /></div>
    <div><label>OpenAI API Key</label><input id="openai" placeholder="sk-..." /></div>
  </div>
  <div class="row">
    <div><label>Shopify Access Token</label><input id="token" placeholder="shpat_..." /></div>
    <div><label>Model (optioneel)</label><input id="model" placeholder="gpt-4o-mini" /></div>
  </div>
  <label>Aangepaste prompt (optioneel)</label>
  <textarea id="prompt" rows="6" placeholder="Herschrijf titel en beschrijving in het Nederlands... Maak ook meta title (<=60) en meta description (<=155)."></textarea>

  <div style="margin:12px 0">
    <button onclick="loadCollections()">Collecties laden</button>
    <span id="cstatus" class="pill">Nog niet geladen</span>
  </div>

  <label>Selecteer collecties</label>
  <select id="collections" multiple size="8" style="width:100%"></select>

  <div style="margin-top:16px"><button onclick="optimize()">Optimaliseer mijn producten</button></div>
  <pre id="status" class="status"></pre>
</div>

<script>
const qs = s => document.querySelector(s);
function set(t){ qs('#status').textContent = t; }
function add(t){ qs('#status').textContent += '\\n' + t; }

async function loadCollections(){
  set('Collecties laden...');
  const res = await fetch('/api/collections', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      store: qs('#store').value.trim(),
      token: qs('#token').value.trim()
    })
  });
  const data = await res.json();
  const sel = qs('#collections'); sel.innerHTML = '';
  (data.collections || []).forEach(c => {
    const opt = document.createElement('option');
    opt.value = c.id; opt.textContent = `${c.title} (#${c.id})`;
    sel.appendChild(opt);
  });
  qs('#cstatus').textContent = `${(data.collections || []).length} collecties geladen`;
  add('Collecties geladen.');
}

async function optimize(){
  const ids = Array.from(qs('#collections').selectedOptions).map(o => o.value);
  set('Start optimalisatie...');
  const res = await fetch('/api/optimize', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      store: qs('#store').value.trim(),
      token: qs('#token').value.trim(),
      openai: qs('#openai').value.trim(),
      model: qs('#model').value.trim() || 'gpt-4o-mini',
      prompt: qs('#prompt').value,
      collection_ids: ids
    })
  });
  const rd = res.body.getReader(); let dec = new TextDecoder();
  while(true){
    const {value, done} = await rd.read();
    if(done) break;
    add(dec.decode(value));
  }
}
</script>
</body></html>
"""

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return Response(INDEX_HTML, mimetype="text/html")
    if request.form.get("username") == ADMIN_USERNAME and request.form.get("password") == ADMIN_PASSWORD:
        session["logged_in"] = True
        return redirect("/dashboard")
    return Response(INDEX_HTML, mimetype="text/html", status=401)


@app.route("/")
def root():
    if not session.get("logged_in"):
        return redirect("/login")
    return redirect("/dashboard")


@app.route("/dashboard")
@require_login
def dashboard():
    html = DASHBOARD_HTML.replace("{store}", SHOPIFY_STORE_DOMAIN)
    return Response(html, mimetype="text/html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/api/collections", methods=["POST"])
@require_login
def api_collections():
    payload = request.get_json(force=True)
    store = payload.get("store") or SHOPIFY_STORE_DOMAIN
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
    store = payload.get("store") or SHOPIFY_STORE_DOMAIN
    token = payload.get("token")
    api_key = payload.get("openai")
    model = (payload.get("model") or "gpt-4o-mini").strip()
    user_prompt = (payload.get("prompt") or "").strip()
    collection_ids = payload.get("collection_ids") or []

    if not token or not api_key:
        return jsonify({"error": "OpenAI key en Shopify token zijn verplicht."}), 400

    def generate():
        try:
            all_product_ids: List[int] = []

            # 1) Producten verzamelen per collectie
            for cid in collection_ids:
                collects = paged_shopify_get(
                    "/admin/api/2024-07/collects.json",
                    token,
                    params={"collection_id": cid},
                )
                pids = [c["product_id"] for c in collects]
                all_product_ids.extend(pids)
                yield f"Collectie {cid}: {len(pids)} producten gevonden\n"

            if not collection_ids:
                yield "Geen collectie gekozen – hele shop optimaliseren.\n"
                products = paged_shopify_get("/admin/api/2024-07/products.json", token)
                all_product_ids = [p["id"] for p in products]

            total = len(all_product_ids)
            if total == 0:
                yield "Geen producten gevonden.\n"
                return

            # Snelle modus voor kleine sets
            use_fast = total <= SMALL_COLLECTION_THRESHOLD
            local_batch = 1 if use_fast else BATCH_SIZE
            per_product_sleep = FAST_PER_PRODUCT_SLEEP if use_fast else 1.2
            per_batch_sleep = FAST_PER_BATCH_SLEEP if use_fast else 3.0
            pre_openai_sleep = FAST_OPENAI_PRE_SLEEP if use_fast else 0.8

            yield f"Instellingen: batch={local_batch}, fast_mode={use_fast}\n"

            processed = 0
            for i in range(0, total, local_batch):
                batch_ids = all_product_ids[i : i + local_batch]
                ids_param = ",".join(map(str, batch_ids))
                url = f"https://{store}/admin/api/2024-07/products.json"
                r = _request_with_retry(
                    "GET",
                    url,
                    headers=shopify_headers(token),
                    params={"ids": ids_param, "limit": 250},
                )
                prods = r.json().get("products", [])

                for p in prods:
                    title = p.get("title", "")
                    body = p.get("body_html", "")
                    tags = p.get("tags", "")

                    sys = (
                        "Je bent een Nederlandstalige e-commerce SEO-copywriter. "
                        "Schrijf natuurlijk en klantgericht. Houd formatting eenvoudig (paragrafen, lijstjes)."
                    )
                    base_prompt = textwrap.dedent(
                        f"""
                        Originele titel: {title}
                        Originele beschrijving (HTML toegestaan): {body}
                        Tags: {tags}

                        Taken:
                        1) Nieuwe SEO-geoptimaliseerde titel
                        2) Gestandaardiseerde productbeschrijving (200–250 woorden)
                        3) Meta title (max 60 tekens)
                        4) Meta description (max 155 tekens)

                        Retourneer in dit formaat:
                        Nieuwe titel: …

                        Beschrijving: …

                        Meta title: …

                        Meta description: …
                        """
                    ).strip()
                    final_prompt = (user_prompt + "\n\n" + base_prompt).strip() if user_prompt else base_prompt

                    try:
                        # --- AI stap -------------------------------------------------
                        yield f"→ #{p['id']}: AI-tekst genereren...\n"
                        start = time.time()
                        time.sleep(pre_openai_sleep)

                        last_hb = start
                        out = None
                        while out is None:
                            try:
                                out = openai_chat(api_key, sys, final_prompt, model=model)
                            except Exception as oe:
                                # openai_chat heeft eigen retries; faalt het alsnog, toon fout
                                raise oe
                            finally:
                                now = time.time()
                                if now - last_hb >= HEARTBEAT_EVERY_SEC:
                                    yield "·\n"
                                    last_hb = now

                        pieces = split_ai_output(out)
                        yield f"   AI klaar voor #{p['id']}\n"

                        # SEO-fallbacks
                        seo_title = pieces.get("meta_title") or (pieces.get("title") or title)[:60]
                        seo_desc = pieces.get("meta_description") or (pieces.get("body_html") or body)[:155]

                        # --- Shopify update -----------------------------------------
                        yield f"   Shopify update voor #{p['id']}...\n"
                        _ = shopify_graphql_update_product(
                            store_domain=store,
                            access_token=token,
                            product_id_int=p["id"],
                            new_title=(pieces.get("title") or title),
                            new_desc_html=(pieces.get("body_html") or body),
                            seo_title=seo_title,
                            seo_desc=seo_desc,
                        )

                        processed += 1
                        yield f"✅ #{p['id']} bijgewerkt: {(pieces.get('title') or title)[:70]}\n"

                        time.sleep(per_product_sleep)

                    except Exception as e:
                        msg = str(e)
                        if "OpenAI" in msg:
                            yield f"❌ OpenAI-fout bij product #{p.get('id')}: {msg}\n"
                        elif "Shopify" in msg:
                            yield f"❌ Shopify-fout bij product #{p.get('id')}: {msg}\n"
                        else:
                            yield f"❌ Onbekende fout bij product #{p.get('id')}: {msg}\n"

                yield f"-- Batch klaar ({len(prods)} producten) --\n"
                if per_batch_sleep > 0:
                    time.sleep(per_batch_sleep)

            yield f"\nKlaar. Totaal bijgewerkt: {processed}.\n"

        except Exception as e:
            yield f"⚠️ Beëindigd met fout: {e}\n"

    # Streaming headers om buffering te vermijden (Render/Nginx)
    return Response(
        generate(),
        mimetype="text/plain",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# -----------------------------------------------------------------------------
# Einde bestand
# -----------------------------------------------------------------------------

