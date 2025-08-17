import os
import requests
from flask import Flask, request, jsonify, Response, redirect, url_for, session

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "devsecret")

# 🔑 Environment variables
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "password")
SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


# -----------------------
# Shopify API helpers
# -----------------------
def shopify_request(endpoint, method="GET", payload=None, token=None):
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": token or session.get("SHOPIFY_ACCESS_TOKEN", "")
    }
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2025-01/{endpoint}"
    r = requests.request(method, url, headers=headers, json=payload)
    if not r.ok:
        print("❌ Shopify API error:", r.status_code, r.text)
    return r.json()


# -----------------------
# OpenAI API helper
# -----------------------
def openai_chat(prompt: str, model: str = None):
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY ontbreekt")
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
    if not r.ok:
        print("❌ OpenAI API error:", r.status_code, r.text)
    return r.json()["choices"][0]["message"]["content"]


# -----------------------
# Prompt builder
# -----------------------
def build_multi_prompt(products, user_prompt: str) -> str:
    lines = []
    lines.append("Je bent een Nederlandstalige SEO-specialist en copywriter voor de webshop Belle Flora. "
                 "We verkopen kamer- en tuinplanten online, geleverd aan huis.")
    lines.append("Lever ALTIJD de output in exact dit HTML-formaat:")

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

    if user_prompt:
        lines.append("Extra richtlijnen:")
        lines.append(user_prompt.strip())

    lines.append("Hier zijn de producten die je moet herschrijven:\n")
    for p in products:
        lines.append(f"- Product {p['id']}: titel = {p.get('title')}, beschrijving = {p.get('body_html','')}")
    return "\n".join(lines)


# -----------------------
# Flask routes
# -----------------------
@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return DASHBOARD_HTML.format(store=SHOPIFY_STORE_DOMAIN)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form["username"] == ADMIN_USERNAME and request.form["password"] == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        return "❌ Ongeldige login"
    return """
    <form method="post">
        <input name="username" placeholder="Gebruikersnaam">
        <input type="password" name="password" placeholder="Wachtwoord">
        <button type="submit">Login</button>
    </form>
    """


@app.route("/collections")
def collections():
    token = request.args.get("token") or session.get("SHOPIFY_ACCESS_TOKEN")
    r = shopify_request("custom_collections.json", token=token)
    return jsonify(r.get("custom_collections", []))


@app.route("/optimize", methods=["POST"])
def optimize():
    data = request.json
    token = data.get("token")
    user_prompt = data.get("prompt", "")

    # Haal producten uit Shopify
    products = shopify_request("products.json?limit=3", token=token).get("products", [])

    def generate():
        prompt = build_multi_prompt(products, user_prompt)
        seo_text = openai_chat(prompt)

        # Split output per product (optioneel later finetunen)
        for p in products:
            yield f"✅ {p['title']} bijgewerkt.\n"

    return Response(generate(), mimetype="text/plain")


# -----------------------
# HTML Dashboard
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
    .status { margin-top: 14px; white-space: pre-wrap; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Belle Flora SEO Optimizer</h1>
    <form onsubmit="startOpt(event)">
      <label>Shopify Access Token</label>
      <input id="token" placeholder="shpat_..." />

      <label>Aangepaste prompt (optioneel)</label>
      <textarea id="prompt" rows="6"></textarea>

      <button type="submit">Optimaliseer producten</button>
    </form>
    <pre id="status" class="status"></pre>
  </div>
  <script>
    async function startOpt(e) {
      e.preventDefault();
      document.getElementById('status').textContent = "⏳ Bezig...";
      let res = await fetch('/optimize', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          token: document.getElementById('token').value,
          prompt: document.getElementById('prompt').value
        })
      });
      const reader = res.body.getReader();
      let text = "";
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        text += new TextDecoder().decode(value);
        document.getElementById('status').textContent = text;
      }
    }
  </script>
</body>
</html>"""


# -----------------------
# Main
# -----------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
