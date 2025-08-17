# Shopify SEO Optimizer – Minimal Flask app (Render/Railway ready)
# NL UI • Login via env vars • Tokens via UI • 0.0.0.0:$PORT • Collectie-selectie • Batching

import os, json, textwrap
from flask import Flask, request, session, redirect, Response, jsonify
import requests
import urllib.request

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', os.urandom(32))

ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'michiel')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'CHANGE_ME')  # Verander in Render
SHOPIFY_STORE_DOMAIN = os.environ.get('SHOPIFY_STORE_DOMAIN', 'your-store.myshopify.com')

def require_login(fn):
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect('/login')
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper

def openai_chat(api_key, system_prompt, user_prompt, model='gpt-4o-mini', temperature=0.7):
    url = 'https://api.openai.com/v1/chat/completions'
    body = {
        'model': model, 'temperature': temperature,
        'messages': [{'role':'system','content':system_prompt},{'role':'user','content':user_prompt}],
    }
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={
        'Authorization': f'Bearer {api_key}', 'Content-Type':'application/json'
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
        return payload['choices'][0]['message']['content']

def shopify_headers(token):
    return {'X-Shopify-Access-Token': token, 'Content-Type':'application/json', 'Accept':'application/json'}

def paged_shopify_get(path, token, limit=250, params=None):
    params = dict(params or {})
    params['limit'] = min(limit, 250)
    since_id, out = 0, []
    while True:
        params['since_id'] = since_id
        url = f'https://{SHOPIFY_STORE_DOMAIN}{path}'
        r = requests.get(url, headers=shopify_headers(token), params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        key = None
        for k in ('custom_collections','smart_collections','products','collects'):
            if k in data: key = k; break
        if not key: break
        items = data.get(key, [])
        if not items: break
        out.extend(items)
        since_id = items[-1]['id']
        if len(items) < params['limit']: break
    return out

def shopify_graphql_update_product(store_domain, access_token, product_id_int,
                                   new_title, new_desc_html, seo_title, seo_desc):
    """
    Updatet producttitel, beschrijving (HTML) en SEO (title/description) via GraphQL productUpdate.
    Vereist Admin API scope: write_products (en read_products als je eerst leest).
    """
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
            "title": new_title,
            "descriptionHtml": new_desc_html,
            "seo": {"title": seo_title, "description": seo_desc}
        }
    }
    r = requests.post(
        url,
        headers={
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={"query": mutation, "variables": variables},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("errors") or (data.get("data", {}).get("productUpdate", {}).get("userErrors")):
        raise RuntimeError(f"Shopify GraphQL error: {data}")
    return data["data"]["productUpdate"]["product"]

def split_ai_output(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    blob = '\n'.join(lines)
    def take(after):
        for a in after:
            if a.lower() in blob.lower(): return a
        return None
    markers = {
        'title': take(['Nieuwe titel:','Titel:','SEO titel:','Nieuwe SEO-titel:']),
        'body': take(['Beschrijving:','Body:','Productbeschrijving:','Gestandaardiseerde beschrijving:']),
        'meta_title': take(['Meta title:','SEO-meta title:','Title tag:']),
        'meta_desc': take(['Meta description:','SEO-meta description:','Description tag:']),
    }
    title=body=meta_title=meta_desc=''
    if all(markers.values()):
        def section(start_marker, end_markers):
            start = blob.lower().find(start_marker.lower())
            if start == -1: return ''
            start += len(start_marker)
            end_positions = []
            for m in end_markers:
                p = blob.lower().find(m.lower(), start)
                if p != -1: end_positions.append(p)
            end = min(end_positions) if end_positions else len(blob)
            return blob[start:end].strip().strip('-:')
        title = section(markers['title'], [markers['body'], markers['meta_title'], markers['meta_desc'], '\n\n'])
        body = section(markers['body'], [markers['meta_title'], markers['meta_desc'], '\n\n'])
        meta_title = section(markers['meta_title'], [markers['meta_desc'], '\n\n'])
        meta_desc = section(markers['meta_desc'], ['\n\n']) or meta_title
    else:
        parts = [p.strip() for p in blob.split('\n\n') if p.strip()]
        title = parts[0] if len(parts)>0 else ''
        body = parts[1] if len(parts)>1 else ''
        meta_title = parts[2] if len(parts)>2 else title[:60]
        meta_desc = parts[3] if len(parts)>3 else body[:155]
    return {
        'title': title.strip(),
        'body_html': body.strip(),
        'meta_title': meta_title.strip()[:60],
        'meta_description': meta_desc.strip()[:155],
    }

# ------------------ HTML views ------------------

INDEX_HTML = '''<!doctype html><html lang="nl"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>Shopify SEO Optimizer</title><style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:24px;background:#0b1020;color:#eef}.card{max-width:880px;margin:0 auto;background:#121735;padding:20px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.35)}h1{margin-top:0}label{display:block;margin:12px 0 8px}input,textarea,select{width:100%;padding:12px;border-radius:10px;border:1px solid #2a335a;background:#0f1430;color:#eef}button{padding:12px 16px;border:0;border-radius:12px;background:#4f7dff;color:white;font-weight:600;cursor:pointer}.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}.muted{opacity:.85}.status{margin-top:14px;white-space:pre-wrap}</style></head><body><div class="card"><h1>Shopify SEO Optimizer</h1><p class="muted">Log in om door te gaan.</p><form method="post" action="/login"><label>Gebruikersnaam</label><input name="username" placeholder="michiel" required /><label>Wachtwoord</label><input name="password" type="password" required /><div style="margin-top:12px"><button type="submit">Inloggen</button></div></form></div></body></html>'''

DASHBOARD_HTML = '''<!doctype html><html lang="nl"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>SEO Optimizer – Dashboard</title><style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:24px;background:#0b1020;color:#eef}.card{max-width:980px;margin:0 auto;background:#121735;padding:20px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.35)}h1{margin-top:0}label{display:block;margin:12px 0 8px}input,textarea,select{width:100%;padding:12px;border-radius:10px;border:1px solid #2a335a;background:#0f1430;color:#eef}button{padding:12px 16px;border:0;border-radius:12px;background:#4f7dff;color:white;font-weight:600;cursor:pointer}.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}.status{margin-top:14px;white-space:pre-wrap}.pill{display:inline-block;padding:6px 10px;border-radius:999px;background:#243165;margin:6px 8px 0 0}</style></head><body><div class="card"><h1>SEO Optimizer – Dashboard</h1><div class="row"><div><label>Shopify store domein</label><input id="store" placeholder="{store}" value="{store}" /></div><div><label>OpenAI API Key</label><input id="openai" placeholder="sk-..." /></div></div><div class="row"><div><label>Shopify Access Token</label><input id="token" placeholder="shpat_..." /></div><div><label>Model (optioneel)</label><input id="model" placeholder="gpt-4o-mini" /></div></div><label>Aangepaste prompt (optioneel)</label><textarea id="prompt" rows="6" placeholder="Herschrijf titel en beschrijving in het Nederlands... Maak ook meta title (<=60) en meta description (<=155)."></textarea><div style="margin:12px 0"><button onclick="loadCollections()">Collecties laden</button><span id="cstatus" class="pill">Nog niet geladen</span></div><label>Selecteer collecties</label><select id="collections" multiple size="8"></select><div style="margin-top:16px"><button onclick="optimize()">Optimaliseer mijn producten</button></div><pre id="status" class="status"></pre></div><script>
const qs=s=>document.querySelector(s);
function set(t){qs('#status').textContent=t}
function add(t){qs('#status').textContent+='\\n'+t}
async function loadCollections(){
  set('Collecties laden...');
  const res = await fetch('/api/collections',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({store:qs('#store').value.trim(),token:qs('#token').value.trim()})});
  const data = await res.json(); const sel = qs('#collections'); sel.innerHTML='';
  (data.collections||[]).forEach(c=>{const opt=document.createElement('option'); opt.value=c.id; opt.textContent=`${c.title} (#${c.id})`; sel.appendChild(opt);});
  qs('#cstatus').textContent=`${(data.collections||[]).length} collecties geladen`; add('Collecties geladen.');
}
async function optimize(){
  const ids = Array.from(qs('#collections').selectedOptions).map(o=>o.value);
  set('Start optimalisatie...');
  const res = await fetch('/api/optimize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({store:qs('#store').value.trim(),token:qs('#token').value.trim(),openai:qs('#openai').value.trim(),model:qs('#model').value.trim()||'gpt-4o-mini',prompt:qs('#prompt').value,collection_ids:ids})});
  const rd = await res.body.getReader(); let dec = new TextDecoder();
  while(true){ const {value,done} = await rd.read(); if(done) break; add(dec.decode(value)); }
}
</script></body></html>'''

# ------------------ Routes ------------------

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'GET':
        return Response(INDEX_HTML, mimetype='text/html')
    if request.form.get('username') == ADMIN_USERNAME and request.form.get('password') == ADMIN_PASSWORD:
        session['logged_in'] = True
        return redirect('/dashboard')
    return Response(INDEX_HTML, mimetype='text/html', status=401)

@app.route('/')
def root():
    if not session.get('logged_in'):
        return redirect('/login')
    return redirect('/dashboard')

@app.route('/dashboard')
@require_login
def dashboard():
    html = DASHBOARD_HTML.replace('{store}', SHOPIFY_STORE_DOMAIN)
    return Response(html, mimetype='text/html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/api/collections', methods=['POST'])
@require_login
def api_collections():
    payload = request.get_json(force=True)
    store = payload.get('store') or SHOPIFY_STORE_DOMAIN
    token = payload.get('token')
    global SHOPIFY_STORE_DOMAIN
    SHOPIFY_STORE_DOMAIN = store
    if not token:
        return jsonify({'error':'Geen Shopify token meegegeven.'}), 400
    customs = paged_shopify_get('/admin/api/2024-07/custom_collections.json', token)
    smarts  = paged_shopify_get('/admin/api/2024-07/smart_collections.json',  token)
    cols = [{'id': c['id'], 'title': c.get('title','(zonder titel)')} for c in (customs+smarts)]
    return jsonify({'collections': cols})

# ------------------ Optimize endpoint ------------------

@app.route('/api/optimize', methods=['POST'])
@require_login
def api_optimize():
    payload = request.get_json(force=True)
    store = payload.get('store') or SHOPIFY_STORE_DOMAIN
    token = payload.get('token')
    api_key = payload.get('openai')
    model = payload.get('model') or 'gpt-4o-mini'
    user_prompt = (payload.get('prompt') or '').strip()
    collection_ids = payload.get('collection_ids') or []

   
