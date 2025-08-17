# Shopify SEO Optimizer – Render/Railway
- Web Service (Python/Flask)
- Login via env vars
- UI in NL met promptveld, collectie-selectie, batching

Deploy (Render)
1) New Web Service → link je repo
2) Build: pip install -r requirements.txt
3) Start: gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
4) Env vars: ADMIN_USERNAME, ADMIN_PASSWORD, SHOPIFY_STORE_DOMAIN, FLASK_SECRET
5) Health check path: /login
