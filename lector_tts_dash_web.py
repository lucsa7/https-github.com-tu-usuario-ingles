##############################################################################
# lector_tts_dash_web.py — v5.5 (2025-06-24) cloud-safe + gTTS 429 fix       #
##############################################################################
# • Tema oscuro CYBORG (Bootswatch) + Montserrat                              #
# • pyttsx3 offline si existe ― si no, gTTS con rotación de dominio + caché   #
# • Manejo de errores 429, MP3/TXT download y reproducción directa            #
##############################################################################

import os, io, re, base64, tempfile, threading, pathlib, warnings, time
from functools import lru_cache
from typing import List, Optional

import dash, dash_bootstrap_components as dbc
from dash import dcc, html, Input, Output, State, no_update

# ── TTS libs ────────────────────────────────────────────────────────────────
try:
    import pyttsx3                      # disponible en tu PC (Windows/macOS)
except ImportError:
    pyttsx3 = None                      # falta en Render

from gtts import gTTS                   # online fallback
from gtts.tts import gTTSError          # excepción propia
from deep_translator import GoogleTranslator
from langdetect import detect, LangDetectException

# ── NLP opcional (spaCy) ────────────────────────────────────────────────────
try:
    import spacy
    _NLP_EN = spacy.load("en_core_web_sm")
    _NLP_ES = spacy.load("es_core_news_sm")
except (ImportError, OSError):
    spacy, _NLP_EN, _NLP_ES = None, None, None
    warnings.warn("spaCy no disponible; se usará heurística regex.")

# ── Config ──────────────────────────────────────────────────────────────────
VOICE_OPTIONS   = {"US English": "Zira"}
DEFAULT_RATE    = 175
HIGHLIGHT_STYLE = {"backgroundColor": "#ffe46b", "borderRadius": "4px"}

TAG, TAG_RE   = "[[", re.compile(r"\[\[\s*(\d+)\s*]]")
CAPITAL_PAT   = r"\b[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü]{2,}(?:\s+[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü]{2,})*"
STOP_TOKENS   = {"Hola","Te","La","El","Los","Las","Un","Una",
                 "Buenos","Buenas","Por","Sin","Con"}

WORDS: List[str] = []
WORD_IDX: int    = -1
READING: bool    = False
ENG: Optional["pyttsx3.Engine"] = None

# ── helpers ─────────────────────────────────────────────────────────────────
def _safe_pdf_extract(raw: bytes) -> str:
    try:
        from pdfminer.high_level import extract_text
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as fp:
            fp.write(raw); fp.flush()
            return extract_text(fp.name)
    except Exception:
        return ""

def _protect_entities(text: str, lang: str) -> tuple[str, dict[str, str]]:
    protected: list[str] = []
    if spacy and ((lang=="en" and _NLP_EN) or (lang=="es" and _NLP_ES)):
        nlp = _NLP_EN if lang=="en" else _NLP_ES
        protected += [e.text for e in nlp(text).ents
                      if e.label_ in {"PERSON","ORG","PRODUCT","WORK_OF_ART","GPE"}]
    for tok in re.findall(CAPITAL_PAT, text):
        if tok not in STOP_TOKENS:
            protected.append(tok)
    protected = sorted(set(protected), key=len, reverse=True)
    tag_map   = {p: f"{TAG}{i}]]" for i, p in enumerate(protected)}
    tmp = text
    for o, t in tag_map.items():
        tmp = tmp.replace(o, t)
    return tmp, tag_map

def _restore_entities(text: str, tag_map: dict[str, str]) -> str:
    for o, t in tag_map.items():
        text = text.replace(t, o)
    return text

def smart_translate(text: str) -> str:
    if not text.strip():
        return text
    try:
        src = "en" if detect(text).startswith("en") else "es"
    except LangDetectException:
        src = "es"
    tgt = "es" if src == "en" else "en"

    tmp, mp = _protect_entities(text, src)
    sents = re.split(r"(?<=[.!?])\s+", tmp)
    translated = " ".join(GoogleTranslator(source=src, target=tgt).translate(s)
                          for s in sents if s)
    translated = TAG_RE.sub(lambda m: f"{TAG}{m.group(1)}]]", translated)
    return _restore_entities(translated, mp)

def detect_lang(text: str) -> str:
    try:
        return "en" if detect(text).startswith("en") else "es"
    except LangDetectException:
        return "es"

def extract_text(contents: str, filename: str) -> str:
    header, b64 = contents.split(",", 1)
    raw = base64.b64decode(b64)
    ext = pathlib.Path(filename).suffix.lower()
    if ext == ".txt":
        return raw.decode("utf-8", errors="ignore")
    if ext == ".docx":
        from docx import Document
        return "\n".join(p.text for p in Document(io.BytesIO(raw)).paragraphs)
    if ext == ".odt":
        from odf import text as odt_text, teletype
        from odf.opendocument import load
        return "\n".join(teletype.extractText(p)
                         for p in load(io.BytesIO(raw)).getElementsByType(odt_text.P))
    if ext == ".pdf":
        txt = _safe_pdf_extract(raw)
        if txt: return txt
    raise ValueError("Extensión no soportada")

# ── gTTS con caché + rotación de dominio ───────────────────────────────────
@lru_cache(maxsize=256)
def _mp3_cached(text: str, lang: str) -> bytes:
    """Devuelve MP3 usando gTTS, probando varios dominios y cacheando."""
    last_err = None
    for tld in ("com", "co.uk", "com.au", "ca"):
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
                gTTS(text=text, lang=lang, tld=tld).save(fp.name)
                fp.seek(0)
                data = fp.read()
            os.remove(fp.name)
            return data
        except gTTSError as e:          # 429, etc.
            last_err = e
            time.sleep(1.2)             # back-off breve
    raise last_err                      # si fallaron todos los dominios

def text_to_mp3_bytes(text: str, lang="en") -> bytes:
    return _mp3_cached(text, lang)      # simple wrapper (p/ coherencia)

def spanified(words: List[str], idx: int):
    out=[]
    for i,w in enumerate(words):
        style=HIGHLIGHT_STYLE if i==idx else {}
        out.extend((html.Span(w,style=style), html.Span(" ")))
    return out

# ── pyttsx3 (solo local) ───────────────────────────────────────────────────
def speak_local(text: str, voice_key: str, rate: int):
    global WORD_IDX, READING, ENG
    if pyttsx3 is None: return
    ENG = pyttsx3.init()
    for v in ENG.getProperty("voices"):
        if voice_key.lower() in v.name.lower():
            ENG.setProperty("voice", v.id); break
    ENG.setProperty("rate", int(rate))
    ENG.connect("started-word",
                lambda *_: globals().__setitem__("WORD_IDX", WORD_IDX+1))
    READING, WORD_IDX = True, -1
    ENG.say(text); ENG.runAndWait()
    READING, ENG = False, None

# ── Dash UI ────────────────────────────────────────────────────────────────
external_css = [
    dbc.themes.CYBORG,
    "https://fonts.googleapis.com/css2?family=Montserrat:wght@300;500;700&display=swap"
]
app = dash.Dash(__name__, external_stylesheets=external_css, title="TTS Translator")
server = app.server

app.index_string = (
    "<!DOCTYPE html><html><head>{%metas%}<title>{%title%}</title>{%favicon%}{%css%}"
    "<style>body{font-family:'Montserrat',sans-serif}"
    ".gradient-btn{background-image:linear-gradient(45deg,#ff4b2b,#ff416c);border:none}"
    ".gradient-btn:hover{filter:brightness(1.1)}</style></head>"
    "<body class='bg-dark text-light'><nav class='navbar navbar-dark bg-danger sticky-top'>"
    "<div class='container-fluid'><span class='navbar-brand mb-0 h1'>🗣️ TTS Translator</span>"
    "</div></nav><div class='container-fluid pt-4'>{%app_entry%}</div>"
    "<footer class='text-center text-secondary py-4'><small>© 2025 STA methodologies</small>"
    "</footer>{%config%}{%scripts%}{%renderer%}</body></html>"
)

controls = dbc.Card(dbc.CardBody([
    dbc.Textarea(id="text-input", placeholder="Escribe o sube un documento…",
                 style={"width":"100%","height":200,"fontSize":20}),
    dcc.Upload(id="upload-doc", multiple=False,
               children=html.Div("📄 Arrastra o haz clic para subir archivo"),
               style={"width":"100%","height":60,"lineHeight":"60px",
                      "borderWidth":1,"borderStyle":"dashed","borderRadius":5,
                      "textAlign":"center","marginTop":10}),
    dcc.Checklist(id="translate-toggle",
                  options=[{"label":" Traducir antes de leer","value":"ON"}],
                  value=["ON"], className="mt-3"),
    dcc.Dropdown(id="voice-selector",
                 options=[{"label":k,"value":v} for k,v in VOICE_OPTIONS.items()],
                 value="Zira", placeholder="Elige la voz", className="mt-3"),
    html.Div([
        html.Label("Velocidad de lectura (palabras/min):"),
        dcc.Slider(id="rate-slider", min=80,max=260,step=5,value=DEFAULT_RATE,
                   tooltip={"placement":"bottom"})
    ], className="mt-4"),
    html.Div([
        html.Button("🔊 Leer", id="speak-btn", n_clicks=0,
                    className="btn gradient-btn me-2 text-white"),
        html.Button("⏹️ Stop", id="stop-btn", n_clicks=0,
                    className="btn btn-warning me-2"),
        html.Button("⬇️ MP3", id="download-btn", n_clicks=0,
                    className="btn btn-secondary me-2"),
        html.Button("📥 Texto", id="download-txt-btn", n_clicks=0,
                    className="btn btn-info")
    ], className="mt-4")
]), className="shadow-lg border-0 bg-dark text-light")

app.layout = dbc.Container([
    dbc.Row([
        dbc.Col(controls, md=5, lg=4),
        dbc.Col([
            dbc.Row([html.H5("Texto traducido", className="text-info mb-2"),
                     html.Pre(id="translation-box",
                              style={"whiteSpace":"pre-wrap","fontSize":18,
                                     "minHeight":160})]),
            dbc.Row([html.H5("Lectura en curso", className="text-info mb-2 mt-4"),
                     html.Div(id="highlight-box",
                              style={"whiteSpace":"pre-wrap","fontSize":22,
                                     "lineHeight":1.6,"minHeight":160})]),
            html.Audio(id="audio-player", controls=True, style={"width":"100%"})
        ], md=7, lg=8)
    ], className="g-4"),
    html.Div(id="status", className="mt-3 text-muted"),
    dcc.Interval(id="tick", interval=120, n_intervals=0, disabled=True),
    dcc.Download(id="download-audio"),
    dcc.Download(id="download-text")
], fluid=True)

# ── Callbacks ──────────────────────────────────────────────────────────────
@app.callback(Output("text-input","value"),
              Input("upload-doc","contents"),
              State("upload-doc","filename"),
              prevent_initial_call=True)
def file_up(c,f):
    if not c: return no_update
    try: return extract_text(c,f)
    except Exception as e: return f"⚠️ {e}"

@app.callback(Output("translation-box","children"),
              Input("text-input","value"), Input("translate-toggle","value"))
def update_tr(text,toggle):
    return smart_translate(text) if text and "ON" in toggle else text or ""

@app.callback(
    Output("status","children"),
    Output("tick","disabled", allow_duplicate=True),
    Output("highlight-box","children", allow_duplicate=True),
    Output("audio-player","src"),
    State("text-input","value"),
    State("voice-selector","value"),
    State("rate-slider","value"),
    State("translate-toggle","value"),
    Input("speak-btn","n_clicks"),
    prevent_initial_call=True)
def speak_handler(text, voice, rate, toggle, _):
    global WORDS, WORD_IDX
    if not text or not text.strip():
        return "⚠️ Escribe algo o sube un documento primero.", True, "", no_update
    to_read = smart_translate(text) if "ON" in toggle else text
    WORDS, WORD_IDX = re.findall(r"\S+|\n", to_read), -1

    # ── gTTS (Render/cloud) ────────────────────────────────────────────────
    if pyttsx3 is None:
        try:
            mp3 = text_to_mp3_bytes(to_read, detect_lang(to_read))
        except gTTSError as err:
            return f"⚠️ Google TTS limit: {err}", True, "", no_update
        src = "data:audio/mp3;base64," + base64.b64encode(mp3).decode()
        return "▶️ Reproduciendo (gTTS)", True, "", src

    # ── pyttsx3 (local) ────────────────────────────────────────────────────
    threading.Thread(target=speak_local,
                     args=(to_read, voice, rate), daemon=True).start()
    return f"▶️ Leyendo – voz: {voice} @ {rate} wpm", False, spanified(WORDS,-1), no_update

@app.callback(
    Output("highlight-box","children", allow_duplicate=True),
    Output("tick","disabled", allow_duplicate=True),
    Output("audio-player","src", allow_duplicate=True),
    Input("tick","n_intervals"), prevent_initial_call=True)
def tick(_):
    return (spanified(WORDS, WORD_IDX), False, no_update) if READING else (no_update, True, no_update)

@app.callback(
    Output("status","children", allow_duplicate=True),
    Output("tick","disabled", allow_duplicate=True),
    Output("highlight-box","children", allow_duplicate=True),
    Output("audio-player","src", allow_duplicate=True),
    Input("stop-btn","n_clicks"), prevent_initial_call=True)
def stop(_):
    global READING, WORD_IDX
    if READING and ENG: ENG.stop()
    READING, WORD_IDX=False,-1
    return "⏹️ Detenido", True, spanified(WORDS,-1), no_update

@app.callback(Output("download-audio","data"),
              State("text-input","value"),
              State("translate-toggle","value"),
              Input("download-btn","n_clicks"),
              prevent_initial_call=True)
def dl_audio(text,toggle,_):
    if not text.strip(): return no_update
    processed = smart_translate(text) if "ON" in toggle else text
    try:
        mp3 = text_to_mp3_bytes(processed, detect_lang(processed))
    except gTTSError as err:
        return no_update                 # silencioso; ya se mostró error arriba
    return dcc.send_bytes(mp3, "speech.mp3")

@app.callback(Output("download-text","data"),
              State("text-input","value"),
              State("translate-toggle","value"),
              Input("download-txt-btn","n_clicks"),
              prevent_initial_call=True)
def dl_txt(text,toggle,_):
    if not text.strip(): return no_update
    result = smart_translate(text) if "ON" in toggle else text
    fname = "translation_en.txt" if detect_lang(result)=="en" else "traduccion_es.txt"
    return dict(content=result, filename=fname, type="text/plain")
# ── run ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8050)), debug=False)

















