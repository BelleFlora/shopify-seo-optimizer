# app.py
import os, re, textwrap, time, traceback, secrets
from typing import Any, Dict, Optional
import requests
from flask import Flask, request, jsonify, Response

# Flask setup
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", secrets.token_hex(16))

# --- Config ---
OPENAI_API_KEY   = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL     = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMP      = float(os.environ.get("OPENAI_TEMP", "0.7"))
BATCH_SIZE       = int(os.environ.get("BATCH_SIZE", "8"))
DELAY_PER_PRODUCT= float(os.environ.get("DELAY_SECONDS", "0.5"))

META_TITLE_LIMIT = 60
META_DESC_LIMIT  = 155
BRAND_NAME       = "Belle Flora"
META_SUFFIX      = f"| {BRAND_NAME}"

TRANSACTIONAL_MODE = os.environ.get("TRANSACTIONAL_MODE", "true").lower() in ("1","true","yes")

USP_LIST = [
    "Gratis verzending vanaf €49",
    "Binnen 3 werkdagen geleverd",
    "Soepel retourbeleid",
    "Europese kwekers",
    "Top kwaliteit",
]
USP_STR = " | ".join(USP_LIST)

# Heroicons inline (SVG)
HEROICON_SIZE = int(os.environ.get("HEROICON_SIZE", "20"))
def heroicon(name:str)->str:
    paths={
        "sun":'<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 4.5v-2.25m0 19.5V19.5m8.485-7.485h2.25m-19.5 0H4.5m12.02-7.515l1.59-1.59m-13.64 13.64l1.59-1.59m0-12.05-1.59-1.59m13.64 13.64-1.59-1.59M12 6.75a5.25 5.25 0 100 10.5 5.25 5.25 0 000-10.5z"/>',
        "droplet":'<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 21a7.5 7.5 0 007.5-7.5c0-2.904-1.718-5.348-3.636-7.485A32.95 32.95 0 0012 3a32.95 32.95 0 00-3.864 3.015C6.218 8.152 4.5 10.596 4.5 13.5A7.5 7.5 0 0012 21z"/>',
        "home":'<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M2.25 12l8.955-8.955c.44-.44 1.15-.44 1.59 0L21.75 12M4.5 9.75V21h15V9.75"/>',
        "exclamation-triangle":'<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v3.75m0 3.75h.007M2.243 18h19.514c1.322 0 2.082-1.44 1.35-2.571L13.35 3.43c-.66-1.028-2.04-1.028-2.7 0L.893 15.43C.161 16.56.921 18 2.243 18z"/>'
    }
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{HEROICON_SIZE}" height="{HEROICON_SIZE}" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="vertical-align:middle;margin-right:6px;opacity:.9">{paths[name]}</svg>'

ICON_MAP={"licht":heroicon("sun"),"water":heroicon("droplet"),
          "plaats":heroicon("home"),"giftig":heroicon("exclamation-triangle")}

# Naamkoppeling
NAME_MAP = {
    "Paradijsvogelplant":"Strelitzia",
    "Flamingoplant":"Anthurium",
    "Slaapplant":"Calathea",
    "Gatenplant":"Monstera",
    "Olifantsoor":"Alocasia",
    "Hartbladige klimmer":"Philodendron",
    "Vrouwentong":"Sanseveria",
    "Vioolbladplant":"Fycus Lyrata",
    "Drakenboom":"Dracaena",
    "ZZ-Plant":"Zamioculcas Zamiifoli",
}

# --- Utilities ---
def _s(x: Any) -> str: return x if isinstance(x, str) else ("" if x is None else str(x))
def _safe_int_str(x: Any) -> Optional[str]:
    m = re.search(r"(\d{1,3})", _s(x)); return m.group(1) if m else None

# Shopify utils
def get_collections(store, token):
    url=f"https://{store}/admin/api/2024-04/custom_collections.json"
    r=requests.get(url,headers={"X-Shopify-Access-Token":token}); r.raise_for_status()
    return r.json().get("custom_collections",[])

def get_collection_products(store, token, coll_id):
    url=f"https://{store}/admin/api/2024-04/collections/{coll_id}/products.json?limit=250"
    r=requests.get(url,headers={"X-Shopify-Access-Token":token}); r.raise_for_status()
    return r.json().get("products",[])

def update_product_texts(store, token, pid, title, body, meta_t, meta_d):
    url=f"https://{store}/admin/api/2024-04/products/{pid}.json"
    payload={"product":{"id":pid,"title":title,"body_html":body,
        "metafields_global_title_tag":meta_t,"metafields_global_description_tag":meta_d}}
    r=requests.put(url,headers={"X-Shopify-Access-Token":token,"Content-Type":"application/json"},json=payload)
    r.raise_for_status()

# AI helpers
def _openai_chat(sys_prompt,user_prompt,model,temp):
    import openai; openai.api_key=OPENAI_API_KEY
    resp=openai.ChatCompletion.create(
        model=model,temperature=temp,
        messages=[{"role":"system","content":sys_prompt},{"role":"user","content":user_prompt}],
    )
    return resp["choices"][0]["message"]["content"]

def split_ai_output(txt:str)->Dict[str,str]:
    pieces={}
    m=re.search(r"Nieuwe titel:\s*(.*)",txt,re.I); 
    if m: pieces["title"]=m.group(1).strip()
    m=re.search(r"Beschrijving:(.*?)(Meta title:|Meta description:|$)",txt,re.I|re.S)
    if m: pieces["body_html"]=m.group(1).strip()
    m=re.search(r"Meta title:\s*(.*)",txt,re.I); 
    if m: pieces["meta_title"]=m.group(1).strip()
    m=re.search(r"Meta description:\s*(.*)",txt,re.I); 
    if m: pieces["meta_description"]=m.group(1).strip()
    return pieces

# Dimensions/pot parsing
def parse_dimensions(title,body):
    text=f"{title} {body}"
    h=re.search(r"(\d{1,3})\s*cm",text,re.I)
    d=re.search(r"[⊘Ø⌀]?\s*(\d{1,3})\s*cm",text,re.I)
    return {"height_cm": h.group(1) if h else None,"pot_diameter_cm": d.group(1) if d else None}

def detect_pot_presence(title,body): return "pot" in (title+body).lower()

# Heroicons inject
def inject_heroicons(body_html:str)->str:
    if not body_html: return body_html
    out=body_html
    def _inj(label,icon,html):
        rx=re.compile(rf'(<p[^>]*>\s*)(?:(?:<img[^>]*>|\W|\s)*?)'
                      rf'(?:(?:<strong>)\s*)?{label}\s*:?(\s*)(.*?)(</p>)',re.I|re.S)
        def repl(m): return f"{m.group(1)}{icon}<strong>{label}</strong>:{m.group(2)}{m.group(3)}{m.group(4)}"
        return rx.sub(repl,html)
    for k,ic in ICON_MAP.items(): out=_inj(k,ic,out)
    return out

# Title/meta helpers
def enforce_title_name_map(title:str,nmap:Dict[str,str])->str:
    for nl,lat in nmap.items():
        if nl.lower() in title.lower() and lat.lower() not in title.lower():
            return f"{nl} / {lat} " + title
    return title

def normalize_title(title,dims,pot_color,pot_present):
    t=title
    if dims.get("height_cm"): t+=f" – ↕{dims['height_cm']}cm"
    if dims.get("pot_diameter_cm"): t+=f" – ⌀{dims['pot_diameter_cm']}cm"
    if pot_present: t+=" – in pot"
    return t

def finalize_meta_title(meta_t,fallback_title,limit,brand,suffix):
    base=_s(meta_t) or fallback_title
    full=f"{base} {suffix}"
    if len(full)<=limit: return full
    keep=limit-len(suffix)-1
    trunc=full[:keep].rsplit(" ",1)[0]
    return trunc+" "+suffix

def finalize_meta_desc(meta_d,body,fallback_title,limit,txn:bool):
    base=_s(meta_d) or re.sub("<[^>]+>","",body) or fallback_title
    if txn: base = f"{base}. {USP_STR}"
    if len(base)<=limit: return base
    return base[:limit].rsplit(" ",1)[0]

# --- Flask routes ---
@app.route("/")
def dashboard():
    return DASHBOARD_HTML.replace("[[TXNCHECKED]]","checked" if TRANSACTIONAL_MODE else "")

@app.route("/api/collections",methods=["POST"])
def api_collections():
    data=request.get_json(force=True)
    return jsonify(get_collections(data["store"],data["token"]))

@app.route("/api/optimize",methods=["POST"])
def api_optimize():
    data=request.get_json(force=True)
    store,token,colls=data["store"],data["token"],data["collection_ids"]
    model=data.get("model",OPENAI_MODEL); user_prompt=data.get("prompt","")
    txn=data.get("txn",TRANSACTIONAL_MODE); job_id=data.get("job_id","job")

    def stream():
        yield " \n"; yield f"Job: {job_id}\n"
        for coll in colls:
            try: prods=get_collection_products(store,token,coll)
            except Exception as e:
                yield f"❌ Collectie {coll} fout: {e}\n"; continue
            yield f"-- Collectie {coll}: {len(prods)} producten --\n"
            for p in prods:
                pid=int(p["id"]); title=_s(p["title"]); body=_s(p.get("body_html",""))
                base_prompt=f"Originele titel: {title}\nOriginele beschrijving: {body}\nTaken: verbeter teksten..."
                try: ai_raw=_openai_chat("Je bent een SEO copywriter",base_prompt,model,OPENAI_TEMP); pieces=split_ai_output(ai_raw)
                except Exception as e: yield f"❌ OpenAI fout {pid}: {e}\n"; continue
                try:
                    title_ai=enforce_title_name_map(_s(pieces.get("title")) or title,NAME_MAP)
                    body_ai=_s(pieces.get("body_html")) or body
                    dims=parse_dimensions(title_ai,body_ai); pot_present=detect_pot_presence(title_ai,body_ai)
                    final_title=normalize_title(title_ai,dims,None,pot_present)
                    final_body=inject_heroicons(body_ai)
                    final_meta_title=finalize_meta_title(pieces.get("meta_title"),final_title,META_TITLE_LIMIT,BRAND_NAME,META_SUFFIX)
                    final_meta_desc=finalize_meta_desc(pieces.get("meta_description"),final_body,final_title,META_DESC_LIMIT,txn)
                    update_product_texts(store,token,pid,final_title,final_body,final_meta_title,final_meta_desc)
                    yield f"✅ {pid} bijgewerkt: {final_title}\n"
                except Exception as e: yield f"❌ Product {pid} fout: {e}\n"
                time.sleep(DELAY_PER_PRODUCT)
            yield f"-- Collectie {coll} klaar --\n"
    return Response(stream(),mimetype="text/plain",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# --- HTML Dashboard ---
DASHBOARD_HTML="""
<!doctype html><html><head><meta charset=utf-8>
<title>SEO Optimizer</title></head>
<body>
<h1>Belle Flora Optimizer</h1>
<div>
  <label>Store <input id=store></label>
  <label>Token <input id=token></label>
  <button onclick="loadCollections()">Collecties laden</button>
</div>
<select id=collections multiple size=6 style="width:300px"></select>
<div>
  <label><input type="checkbox" id="txn" [[TXNCHECKED]]> Transactiefocus</label>
</div>
<button id=btnRun onclick="optimizeSelected()">Optimaliseer</button>
<button id=btnCancel onclick="cancelJob()" disabled>Annuleer</button>
<pre id=log>Klaar om te starten…</pre>
<script>
function qs(s){return document.querySelector(s);}
function setLog(t){qs('#log').textContent=t;}
function addLog(t){qs('#log').textContent+="\\n"+t;}
window.onerror=function(msg,src,line,col,err){addLog("❌ JS-fout: "+msg+" @"+line+":"+col);};
async function loadCollections(){
  setLog("Collecties laden…");
  try{
    let res=await fetch('/api/collections',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({store:qs('#store').value.trim(),token:qs('#token').value.trim()})});
    if(!res.ok){addLog("❌ Fout "+res.status);return;}
    let data=await res.json();
    let sel=qs('#collections'); sel.innerHTML="";
    data.forEach(c=>{let o=document.createElement('option');o.value=c.id;o.textContent=c.title;sel.appendChild(o);});
    addLog("✅ "+data.length+" collecties geladen");
  }catch(e){addLog("❌ Netwerkfout "+e.message);}
}
let abortCtrl=null, RUN=false;
async function optimizeSelected(){
  if(RUN) return; RUN=true; abortCtrl=new AbortController();
  setLog("Start optimalisatie…");
  let ids=Array.from(qs('#collections').selectedOptions).map(o=>o.value);
  let res=await fetch('/api/optimize',{method:'POST',signal:abortCtrl.signal,headers:{'Content-Type':'application/json'},
    body:JSON.stringify({store:qs('#store').value.trim(),token:qs('#token').value.trim(),collection_ids:ids,txn:qs('#txn').checked})});
  if(!res.ok){addLog("❌ "+res.status);RUN=false;return;}
  let reader=res.body.getReader(); let dec=new TextDecoder();
  while(true){let {value,done}=await reader.read(); if(done)break; addLog(dec.decode(value));}
  RUN=false;
}
function cancelJob(){if(abortCtrl){abortCtrl.abort();addLog("⏹ Job geannuleerd.");}}
</script>
</body></html>
"""

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
