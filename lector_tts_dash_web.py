##############################################################################
#  lector_tts_dash_web.py  — v5.1 (2025-06-23) – dark-theme web edition      #
##############################################################################
#  ✨ WHAT'S NEW                                                           ✨ #
#  • CYBORG Bootswatch theme (dark, modern)                                #
#  • Sleek Montserrat font & gradient buttons via embedded CSS             #
#  • Sticky top navbar with quick links                                    #
#  • Controls grouped inside a responsive <Card> for cleaner layout        #
#  • No logic changed – drop-in replacement for v5.0                       #
##############################################################################

import os, io, re, base64, tempfile, threading, pathlib, itertools, warnings
from typing import List, Optional

import dash, dash_bootstrap_components as dbc
from dash import dcc, html, Input, Output, State, no_update

import pyttsx3
from gtts import gTTS
from deep_translator import GoogleTranslator
from langdetect import detect, LangDetectException

# ── optional / fallback imports ────────────────────────────────────────────
try:
    import spacy  # spaCy ≥3.7 with en_core_web_sm / es_core_news_sm installed
    _NLP_EN = spacy.load("en_core_web_sm")
    _NLP_ES = spacy.load("es_core_news_sm")
except (ImportError, OSError):
    spacy, _NLP_EN, _NLP_ES = None, None, None
    warnings.warn("spaCy not found. Falling back to regex-only name protection.  →  pip install spacy>=3.7 en-core-web-sm es-core-news-sm")

# optional PDF support ------------------------------------------------------

def _safe_pdf_extract(raw: bytes) -> str:
    try:
        from pdfminer.high_level import extract_text as _extract
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as fp:
            fp.write(raw)
            fp.flush()
            return _extract(fp.name)
    except Exception:
        return ""

# ── configuration -----------------------------------------------------------
VOICE_OPTIONS   = {"US English": "Zira"}
DEFAULT_RATE    = 175
HIGHLIGHT_STYLE = {"backgroundColor": "#ffe46b", "borderRadius": "4px"}

# ── globals ----------------------------------------------------------------
WORDS: List[str] = []
WORD_IDX: int    = -1
READING: bool    = False
ENG: Optional[pyttsx3.Engine] = None

# ── constants --------------------------------------------------------------
TAG      = "[["
TAG_RE   = re.compile(r"\[\[\s*(\d+)\s*]]")
CAP_PAT  = r"\b[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü]{2,}(?:\s+[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü]{2,})*"
STOP_TOKENS = {
    "Hola", "Te", "La", "El", "Los", "Las", "Un", "Una",
    "Buenos", "Buenas", "Por", "Sin", "Con"
}

# ── helpers ----------------------------------------------------------------

def _is_sentence_start(token: str, text: str) -> bool:
    pattern = rf"(?:^|[.!?]\s+){re.escape(token)}\b"
    return re.search(pattern, text) is not None


def _protect_entities(text: str, lang: str) -> tuple[str, dict[str, str]]:
    """Replace PERSON/ORG/PRODUCT entities & capitalised tokens with TAGs."""
    protected: list[str] = []

    # 1) spaCy NER ----------------------------------------------------------
    if spacy and ((lang == "en" and _NLP_EN) or (lang == "es" and _NLP_ES)):
        nlp = _NLP_EN if lang == "en" else _NLP_ES
        protected += [
            ent.text for ent in nlp(text).ents
            if ent.label_ in {"PERSON", "ORG", "PRODUCT", "WORK_OF_ART", "GPE"}
        ]

    # 2) Regex heuristics ---------------------------------------------------
    for tok in re.findall(CAP_PAT, text):
        if tok in STOP_TOKENS:
            continue
        if (
            " " not in tok and text.count(tok) == 1 and
            (
                re.match(fr"^{re.escape(tok)}\b", text) or
                re.search(fr"[.!?]\s+{re.escape(tok)}\b", text)
            )
        ):
            continue
        protected.append(tok)

    protected = sorted(set(protected), key=len, reverse=True)
    tag_map   = {p: f"{TAG}{i}]]" for i, p in enumerate(protected)}
    temp = text
    for orig, tag in tag_map.items():
        temp = temp.replace(orig, tag)
    return temp, tag_map


def _restore_entities(text: str, tag_map: dict[str, str]) -> str:
    for orig, tag in tag_map.items():
        text = text.replace(tag, orig)
    return text


def smart_translate(text: str) -> str:
    if not text.strip():
        return text
    try:
        src_lang = "en" if detect(text).startswith("en") else "es"
    except LangDetectException:
        src_lang = "es"
    tgt_lang = "es" if src_lang == "en" else "en"

    temp, tag_map = _protect_entities(text, src_lang)
    sentences = re.split(r"(?<=[.!?])\s+", temp)
    translated_chunks: list[str] = []
    for s in sentences:
        if not s:
            continue
        translated_chunks.append(GoogleTranslator(source=src_lang, target=tgt_lang).translate(s))
    translated = " ".join(translated_chunks)
    translated = TAG_RE.sub(lambda m: f"{TAG}{m.group(1)}]]", translated)
    return _restore_entities(translated, tag_map)


def detect_lang(text: str) -> str:
    try:
        code = detect(text)
    except LangDetectException:
        code = "es"
    return "en" if code.startswith("en") else "es"


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
        return "\n".join(teletype.extractText(p) for p in load(io.BytesIO(raw)).getElementsByType(odt_text.P))
    if ext == ".pdf":
        txt = _safe_pdf_extract(raw)
        if txt:
            return txt
    raise ValueError("Extensión no soportada → usa .txt / .docx / .odt / .pdf")


def text_to_mp3_bytes(text: str, lang="en") -> bytes:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
        gTTS(text=text, lang=lang).save(fp.name)
        fp.seek(0)
        data = fp.read()
    os.remove(fp.name)
    return data


def spanified(words: List[str], idx: int):
    spans = []
    for i, w in enumerate(words):
        style = HIGHLIGHT_STYLE if i == idx else {}
        spans.extend((html.Span(w, style=style), html.Span(" ")))
    return spans

# ── threaded TTS -----------------------------------------------------------

def speak(text: str, voice_key: str, rate: int):
    global WORD_IDX, READING, ENG
    ENG = pyttsx3.init()
    for v in ENG.getProperty("voices"):
        if voice_key.lower() in v.name.lower():
            ENG.setProperty("voice", v.id)
            break
    ENG.setProperty("rate", int(rate))

    def on_word(_, __, ___):
        global WORD_IDX
        WORD_IDX += 1

    ENG.connect("started-word", on_word)
    READING, WORD_IDX = True, -1
    ENG.say(text)
    ENG.runAndWait()
    READING, ENG = False, None

# ── Dash app --------------------------------------------------------------

external_stylesheets = [
    dbc.themes.CYBORG,  # dark Bootswatch theme
    "https://fonts.googleapis.com/css2?family=Montserrat:wght@300;500;700&display=swap",
]
app = dash.Dash(__name__, external_stylesheets=external_stylesheets, title="TTS Translator")
server = app.server

# ── inject custom CSS ------------------------------------------------------
app.index_string = (
    "<!DOCTYPE html>\n" +
    "<html>\n    <head>{%metas%}\n        <title>{%title%}</title>{%favicon%}{%css%}\n        <style>body{font-family:'Montserrat',sans-serif;} .gradient-btn{background-image:linear-gradient(45deg,#ff4b2b,#ff416c);border:none;} .gradient-btn:hover{filter:brightness(1.1);} </style>\n    </head>\n    <body class='bg-dark text-light'>\n        <nav class='navbar navbar-dark bg-danger sticky-top'><div class='container-fluid'><span class='navbar-brand mb-0 h1'>🗣️ TTS Translator</span></div></nav>\n        <div class='container-fluid pt-4'>{%app_entry%}</div>\n        <footer class='text-center text-secondary py-4'><small>© 2025 STA methodologies · <a href='https://www.instagram.com/profesorlucianosacaba' class='link-secondary'>Instagram</a></small></footer>{%config%}{%scripts%}{%renderer%}\n    </body></html>"
)

controls_card = dbc.Card(
    dbc.CardBody([
        dbc.Textarea(id="text-input", placeholder="Escribe o sube un documento…",
                     style={"width": "100%", "height": 200, "fontSize": 20}),
        dcc.Upload(id="upload-doc", multiple=False,
                   children=html.Div("📄 Arrastra o haz clic para subir archivo"),
                   style={"width": "100%", "height": 60, "lineHeight": "60px",
                          "borderWidth": 1, "borderStyle": "dashed", "borderRadius": 5,
                          "textAlign": "center", "marginTop": 10}),
        dcc.Checklist(id="translate-toggle",
                      options=[{"label": " Traducir antes de leer", "value": "ON"}],
                      value=["ON"], className="mt-3"),
        dcc.Dropdown(id="voice-selector", options=[{"label": k, "value": v} for k, v in VOICE_OPTIONS.items()],
                     value="Zira", placeholder="Elige la voz", className="mt-3"),
        html.Div([
            html.Label("Velocidad de lectura (palabras/min):"),
            dcc.Slider(id="rate-slider", min=80, max=260, step=5, value=DEFAULT_RATE,
                       tooltip={"placement": "bottom"})
        ], className="mt-4"),
        html.Div([
            html.Button("🔊 Leer", id="speak-btn", n_clicks=0, className="btn gradient-btn me-2 text-white"),
            html.Button("⏹️ Stop", id="stop-btn", n_clicks=0, className="btn btn-warning me-2"),
            html.Button("⬇️ MP3", id="download-btn", n_clicks=0, className="btn btn-secondary me-2"),
            html.Button("📥 Texto", id="download-txt-btn", n_clicks=0, className="btn btn-info")
        ], className="mt-4")
    ]),
    className="shadow-lg border-0 bg-dark text-light"
)

app.layout = dbc.Container([
    dbc.Row([
        dbc.Col(controls_card, md=5, lg=4),
        dbc.Col([
            dbc.Row([
                html.H5("Texto traducido", className="text-info mb-2"),
                html.Pre(id="translation-box", style={"whiteSpace": "pre-wrap", "fontSize": 18, "minHeight": 160})
            ]),
            dbc.Row([
                html.H5("Lectura en curso", className="text-info mb-2 mt-4"),
                html.Div(id="highlight-box", style={"whiteSpace": "pre-wrap", "fontSize": 22, "lineHeight": 1.6, "minHeight": 160})
            ])
        ], md=7, lg=8)
    ], className="g-4"),
    html.Div(id="status", className="mt-3 text-muted"),
    dcc.Interval(id="tick", interval=120, n_intervals=0, disabled=True),
    dcc.Download(id="download-audio"),
    dcc.Download(id="download-text"),
    html.Div(id="dummy", style={"display": "none"})
], fluid=True)

# ── callbacks (unchanged logic) -------------------------------------------

@app.callback(Output("text-input", "value"),
              Input("upload-doc", "contents"), State("upload-doc", "filename"),
              prevent_initial_call=True)
def on_file_up(contents, filename):
    if not contents:
        return no_update
    try:
        return extract_text(contents, filename)
    except Exception as e:
        return f"⚠️ Error leyendo archivo: {e}"

@app.callback(Output("translation-box", "children"),
              Input("text-input", "value"), Input("translate-toggle", "value"))
def on_text_change(text, toggle):
    if not text or "ON" not in toggle:
        return text or ""
    return smart_translate(text)

@app.callback(Output("status", "children"), Output("tick", "disabled", allow_duplicate=True),
              Output("highlight-box", "children", allow_duplicate=True),
              State("text-input", "value"), State("voice-selector", "value"), State("rate-slider", "value"),
              State("translate-toggle", "value"), Input("speak-btn", "n_clicks"),
              prevent_initial_call=True)
def on_speak(text, voice, rate, toggle, _n):
    global WORDS, WORD_IDX
    if not text or not text.strip():
        return "⚠️ Escribe algo o sube un documento primero.", True, ""
    to_read = smart_translate(text) if "ON" in toggle else text
    WORDS, WORD_IDX = re.findall(r"\S+|\n", to_read), -1
    threading.Thread(target=speak, args=(to_read, voice, rate), daemon=True).start()
    return f"▶️ Leyendo – voz: {voice} @ {rate} wpm", False, spanified(WORDS, -1)

@app.callback(Output("highlight-box", "children", allow_duplicate=True),
              Output("tick", "disabled", allow_duplicate=True),
              Input("tick", "n_intervals"), prevent_initial_call=True)
def on_tick(_):
    return (spanified(WORDS, WORD_IDX), False) if READING else (no_update, True)

@app.callback(Output("dummy", "children"), Input("rate-slider", "value"), prevent_initial_call=True)
def on_rate_change(rate):
    if READING and ENG:
        ENG.setProperty("rate", int(rate))
    return ""

@app.callback(Output("status", "children", allow_duplicate=True),
              Output("tick", "disabled", allow_duplicate=True),
              Output("highlight-box", "children", allow_duplicate=True),
              Input("stop-btn", "n_clicks"), prevent_initial_call=True)

def on_stop(_):
    global READING, WORD_IDX
    if READING and ENG:
        ENG.stop()
    READING, WORD_IDX = False, -1
    return "⏹️ Detenido", True, spanified(WORDS, -1)

@app.callback(Output("download-audio", "data"),
              State("text-input", "value"), State("translate-toggle", "value"),
              Input("download-btn", "n_clicks"), prevent_initial_call=True)

def on_dl_audio(text, toggle, _n):
    if not text or not text.strip():
        return no_update
    processed = smart_translate(text) if "ON" in toggle else text
    lang = detect_lang(processed)
    return dcc.send_bytes(text_to_mp3_bytes(processed, lang), "speech.mp3")

@app.callback(Output("download-text", "data"),
              State("text-input", "value"), State("translate-toggle", "value"),
              Input("download-txt-btn", "n_clicks"), prevent_initial_call=True)

def on_dl_text(text, toggle, _n):
    if not text or not text.strip():
        return no_update
    result = smart_translate(text) if "ON" in toggle else text
    fname = "translation_en.txt" if detect_lang(result) == "en" else "traduccion_es.txt"
    return dict(content=result, filename=fname, type="text/plain")

# ── run --------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8050)), debug=False)













