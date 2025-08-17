import os
import requests
from flask import Flask, request, jsonify, Response, redirect, url_for, session

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "devsecret")

# 🔑 Environment variables
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "password")
SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


# -----------------------
# Shopify API helpers
# -----------------------
def shopify_request(endpoint, method="GET", payload=None, token=None):
    """
    Kleine helper om Shopify Admin REST endpoints aan te spreken.
    endpoint bv.: 'products.json?limit=3'
    """
    if not SHOPIFY_STORE_DOMAIN:
        raise RuntimeError("SHOPIFY_STORE_DOMAIN ontbreekt.")
    if not token:
        token = session.get("SHOPIFY_ACCESS_TOKEN", "")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": token or "",
    }
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2025-01/{endpoint}"
    r = requests.request(method, url, headers=headers, json=payload, timeout=60)
    if not r.ok:
        app.logger.error("Shopify API error %s: %s", r.status_code, r.text)
        r.raise_for_status()
    return r.json()


# -----------------------
# OpenAI API helper
# -----------------------
def openai_chat(prompt: str, model: str = None) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY ontbreekt.")
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=120)
    if not r.ok:
        app.logger.error("OpenAI API error %s: %s", r.status_code, r.text)
        r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


# -----------------------
# Prompt builder
# -----------------------
def build_multi_prompt(products, user_prompt: str) -> str:
    """
    Bouwt één duidelijke prompt met een vaste HTML-structuur voor consistente output.
    """
    lines = []
    lines.append(
        "Je bent een Nederlandstalige SEO-specialist en copywriter voor de webshop Belle Flora. "
        "We verkopen kamer- en tuinplanten online, geleverd aan huis."
    )
    lines.append("Lever ALTIJD de output in exact dit HTML-formaat (geen extra tekst buiten deze structuur):")

    lines.append("""
<h2>Nieuwe titel</h2>

<p>Korte introductie (2-3 zinnen) die de plant aantrekkelijk en uniek maakt.</p>

<h3>Eigenschappen & Verzorging</h3>
<ul>
  <li><strong>Standplaats:</strong> ...</li>
  <li><strong>Water:</strong> ...</li>
  <li><strong>Hoogte:</strong> ...</li>
  <li><strong>Bijzonderheden:</strong> ...</li>
</ul>

<h3>Waarom kiezen voor deze plant bij Belle Flora?</h3>
<p>...</p>

<h3>SEO</h3>
<p><strong>Meta title:</strong> [max 60 tekens]</p>
<p><strong>Meta description:</strong> [max 155 tekens]</p>
""")

    lines.append("Regels:")
    lines.append("- Gebruik indien relevant de Latijnse naam of synoniemen van de plant.")
    lines.append("- Zorg dat elke tekst uniek is en natuurlijk leest (geen keyword stuffing).")
    lines.append("- Schrijf in vloeiend Nederlands en gebruik eenvoudige HTML zoals hierboven.")

    if user_prompt:
        lines.append("\nExtra richtlijnen van de gebruiker:")
        lines.append(user_prompt.strip())

    lines.append("\nHier zijn de producten die je moet herschrijven (context):")
    for p in products:
        title = (p.get("title") or "").strip()
        body = (p.get("body_html") or "").strip()
        tags = (p.get("tags") or "").strip()
        lines.append(f"\n---\nTitel: {title}\nBeschrijving (HTML toegestaan): {body}\nTags: {tags}\n---")

    return "\n".join(lines)


# -----------------------
# Routes
# -----------------------
@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    # Belangrijk: geen .format() op de HTML gebruiken (accolades in CSS)!
    return Response(DASHBOARD_HTML, mimetype="text/html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == ADMIN_USERNAME and request.form.get("password") == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        return Response("<p>❌ Ongeldige login</p>", mimetype="text/html", status=401)
    return """
    <!doctype html>
    <html lang="nl"><head><meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width,initial-scale=1"/>
    <title>Login</title>
    <style>
      body{font-family:system-ui,sans-serif;background:#0b1020;color:#eef;display:grid;place-items:center;height:100vh;margin:0}
      .card{background:#121735;padding:24px;border-radius:16px;min-width:340px}
      input,button{width:100%;padding:10px;margin:8px 0;border-radius:8px;border:1px solid #2a335a;background:#0f1430;color:#eef}
      button{background:#4f7dff;border:0;font-weight:700;cursor:pointer}
    </style></head><body>
    <div class="card">
      <h2>Inloggen</h2>
      <form method="post">
        <input name="username" placeholder="Gebruikersnaam" required>
        <input type="password" name="password" placeholder="Wachtwoord" required>
        <button type="submit">Login</button>
      </form>
    </div>
    </body></html>
    """


@app.route("/collections")
def collections():
    """
    Voorbeeld: haalt custom collections op (pas aan naar behoefte).
    Vereist een geldige Shopify Access Token in query of sessie.
    """
    token = request.args.get("token") or session.get("SHOPIFY_ACCESS_TOKEN")
    if not token:
        return jsonify({"error": "Geen Shopify Access Token"}), 400
    data = shopify_request("custom_collections.json?limit=250", token=token)
    return jsonify(data.get("custom_collections", []))


@app.route("/optimize", methods=["POST"])
def optimize():
    """
    Demo-optimalisatie: haalt een paar producten op, bouwt de prompt en streamt status.
    In jouw versie kun je hier je eigen batching + updates (GraphQL productUpdate) blijven doen.
    """
    data = request.get_json(force=True) or {}
    token = data.get("token") or session.get("SHOPIFY_ACCESS_TOKEN")
    if not token:
        return jsonify({"error": "Geen Shopify Access Token"}), 400
    user_prompt = (data.get("prompt") or "").strip()

    # 👇 Voor demo: pak een kleine set producten
    products = shopify_request("products.json?limit=3", token=token).get("products", [])

    def stream():
        try:
            yield "Start optimalisatie...\n"
            if not products:
                yield "Geen producten gevonden.\n"
                return
            prompt = build_multi_prompt(products, user_prompt)
            yield "AI-tekst genereren...\n"
            html_block = openai_chat(prompt)
            # Hier zou je html_block splitten/pars(en) en per product via GraphQL updaten.
            for p in products:
                yield f"✅ '{p.get('title','(zonder titel)')}' verwerkt (voorbeeld-status).\n"
            yield "Klaar.\n"
        except Exception as e:
            yield f"❌ Fout: {e}\n"

    return Response(stream(), mimetype="text/plain")


# -----------------------
# HTML Dashboard (geen .format gebruiken!)
# -----------------------
DASHBOARD_HTML = """<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>SEO Optimizer – Dashboard</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #0b1020; color: #eef; padding: 24px; }
    .card { max-width: 960px; margin: auto; background: #121735; padding: 24px; border-radius: 16px; }
    input, textarea { width: 100%; padding: 10px; margin: 8px 0; border-radius: 8px; border: 1px solid #2a335a; background: #0f1430; color: #eef; }
    button { padding: 12px 16px; border: 0; border-radius: 12px; background: #4f7dff; color: white; font-weight: bold; cursor: pointer; }
    .status { margin-top: 14px; white-space: pre-wrap; background:#0f1430; padding:12px; border-radius:8px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Belle Flora SEO Optimizer</h1>
    <p>Vul je <strong>Shopify Access Token</strong> en (optioneel) een extra prompt in en klik op optimaliseren.</p>
    <form onsubmit="startOpt(event)">
      <label>Shopify Access Token</label>
      <input id="token" placeholder="shpat_..." />

      <label>Aangepaste prompt (optioneel)</label>
      <textarea id="prompt" rows="8" placeholder="Extra richtlijnen..."></textarea>

      <button type="submit">Optimaliseer producten</button>
    </form>
    <pre id="status" class="status"></pre>
  </div>
  <script>
    async function startOpt(e) {
      e.preventDefault();
      const status = document.getElementById('status');
      status.textContent = "⏳ Bezig...";
      let res = await fetch('/optimize', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          token: document.getElementById('token').value.trim(),
          prompt: document.getElementById('prompt').value
        })
      });
      const reader = res.body.getReader();
      let buffer = "";
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buffer += new TextDecoder().decode(value);
        status.textContent = buffer;
      }
    }
  </script>
</body>
</html>"""


# -----------------------
# Main
# -----------------------
if __name__ == "__main__":
    # Render gebruikt gunicorn, maar lokaal kun je zo testen
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
