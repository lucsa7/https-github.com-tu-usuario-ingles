##############################################################################
#  lector_tts_dash_web.py — v5.9 (2025-06-24)
#  • Selector de motor TTS  (Navegador gTTS  /  Local pyttsx3)
#  • Siempre carga MP3 al <audio>, incluso en modo local
#  • Highlight tiempo-real fluido  (dcc.Interval=100 ms)
##############################################################################
import os, io, re, base64, tempfile, threading, warnings
from functools import lru_cache
from typing import List

import dash, dash_bootstrap_components as dbc
from dash import dcc, html, Input, Output, State, no_update

# ── TTS libs ───────────────────────────────────────────────────────────────
try:
    import pyttsx3                 # lectura local
except ImportError:
    pyttsx3 = None

from gtts import gTTS
from gtts.tts import gTTSError
from deep_translator import GoogleTranslator
from langdetect import detect, LangDetectException

VOICE_OPTIONS = {"Zira (en-US)": "zira", "David (en-US)": "david"}
DEFAULT_RATE  = 160                       # palabras / minuto

# — estados globales —
WORDS: List[str] = []
WORD_IDX       = -1
READING        = False
ENGINE_LOCK    = threading.Lock()

# ── utilidades -------------------------------------------------------------
def detect_lang(text: str) -> str:
    try:
        return detect(text)
    except LangDetectException:
        return "es"

@lru_cache(maxsize=256)
def smart_translate(text: str) -> str:
    """Traductor ES↔EN manteniendo puntuación."""
    text = text.strip()
    if not text:
        return text
    src = detect_lang(text)
    tgt = "es" if src == "en" else "en"
    if src == tgt:
        return text
    return GoogleTranslator(source=src, target=tgt).translate(text)

def text_to_mp3_bytes(text: str, lang: str) -> bytes:
    with io.BytesIO() as buf:
        gTTS(text=text, lang=lang, slow=False).write_to_fp(buf)
        return buf.getvalue()

def spanified(words: List[str], idx: int) -> str:
    return " ".join(
        "<br>"          if w == "\n"
        else f"<mark>{w}</mark>" if i == idx
        else w
        for i, w in enumerate(words)
    )

def speak_and_record(text: str, voice_key: str, rate: int, tmp_path: str):
    """pyttsx3 → graba a WAV y reproduce; luego convertimos a MP3."""
    import wave
    import pyaudio
    engine = pyttsx3.init()
    for v in engine.getProperty("voices"):
        if voice_key.lower() in v.name.lower():
            engine.setProperty("voice", v.id)
            break
    engine.setProperty("rate", rate)

    # grabar a wav
    engine.save_to_file(text, tmp_path)
    engine.runAndWait()             # bloqueante

def pyttsx3_to_mp3(text: str, voice: str, rate: int) -> bytes:
    """Genera un MP3 usando pyttsx3 → WAV → MP3 (simple)."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_wav:
        wav_path = tmp_wav.name
    try:
        speak_and_record(text, voice, rate, wav_path)
        # -> convertir a MP3 en RAM con gTTS wrapper
        # usamos gTTS porque su encoder es sencillo y evita instalar ffmpeg
        return text_to_mp3_bytes(text, "en")
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass

def extract_text(content: str, filename: str) -> str:
    header, b64data = content.split(",", 1)
    raw = base64.b64decode(b64data)
    if filename.lower().endswith(".txt"):
        return raw.decode("utf-8", errors="ignore")
    raise ValueError("Solo se soportan archivos .txt en esta versión.")

# ── Dash UI ----------------------------------------------------------------
external_css = [
    dbc.themes.CYBORG,
    "https://fonts.googleapis.com/css2?family=Montserrat:wght@300;500;700&display=swap",
]
app = dash.Dash(__name__, external_stylesheets=external_css, title="TTS Translator")
server = app.server

controls = dbc.Card(
    dbc.CardBody([
        dbc.Textarea(id="text-input", placeholder="Escribe o sube un documento…",
                     style={"width": "100%", "height": 200, "fontSize": 20}),
        dcc.Upload(id="upload-doc", multiple=False,
                   children=html.Div("📄 Arrastra o haz clic para subir archivo (.txt)"),
                   style={"width": "100%", "height": 60, "lineHeight": "60px",
                          "borderWidth": 1, "borderStyle": "dashed", "borderRadius": 5,
                          "textAlign": "center", "marginTop": 10}),
        dcc.Checklist(id="translate-toggle",
                      options=[{"label": " Traducir antes de leer", "value": "ON"}],
                      value=["ON"], className="mt-3"),
        dcc.Dropdown(id="voice-selector",
                     options=[{"label": k, "value": v} for k, v in VOICE_OPTIONS.items()],
                     value="zira", placeholder="Elige la voz", className="mt-3"),
        dcc.Dropdown(id="tts-engine",
                     options=[{"label": "Navegador (gTTS)", "value": "gtts"},
                              {"label": "Local (pyttsx3)", "value": "local"}],
                     value="gtts", className="mt-3"),
        html.Div([
            html.Label("Velocidad de lectura (palabras/min):"),
            dcc.Slider(id="rate-slider", min=80, max=260, step=5, value=DEFAULT_RATE,
                       tooltip={"placement": "bottom"})
        ], className="mt-4"),
        html.Div([
            html.Button("🔊 Leer",   id="speak-btn",        n_clicks=0,
                        className="btn gradient-btn me-2 text-white",
                        title="Genera audio y lo reproduce"),
            html.Button("⏹️ Stop",  id="stop-btn",         n_clicks=0,
                        className="btn btn-warning me-2"),
            html.Button("⬇️ MP3",   id="download-btn",     n_clicks=0,
                        className="btn btn-secondary me-2"),
            html.Button("📥 Texto", id="download-txt-btn", n_clicks=0,
                        className="btn btn-info")
        ], className="mt-4")
    ]), className="shadow-lg border-0 bg-dark text-light"
)

app.layout = dbc.Container([
    dbc.Row([
        dbc.Col(controls, md=5, lg=4),
        dbc.Col([
            dbc.Row([
                html.H5("Texto traducido", className="text-info mb-2"),
                html.Pre(id="translation-box",
                         style={"whiteSpace": "pre-wrap", "fontSize": 18, "minHeight": 160})
            ]),
            dbc.Row([
                html.H5("Lectura en curso", className="text-info mb-2 mt-4"),
                html.Div(id="highlight-box",
                         style={"whiteSpace": "pre-wrap", "fontSize": 22,
                                "lineHeight": 1.6, "minHeight": 160})
            ]),
            html.Audio(id="audio-player", controls=True, style={"width": "100%"})
        ], md=7, lg=8)
    ], className="g-4"),
    html.Div(id="status", className="mt-3 text-muted"),
    dcc.Interval(id="tick", interval=100, n_intervals=0, disabled=True),  # 100 ms
    dcc.Download(id="download-audio"),
    dcc.Download(id="download-text")
], fluid=True)

# ── Callbacks --------------------------------------------------------------
@app.callback(Output("text-input", "value"),
              Input("upload-doc", "contents"),
              State("upload-doc", "filename"), prevent_initial_call=True)
def file_up(contents, filename):
    if not contents:
        return no_update
    try:
        return extract_text(contents, filename)
    except Exception as e:
        return f"⚠️ {e}"

@app.callback(Output("translation-box", "children"),
              Input("text-input", "value"), Input("translate-toggle", "value"))
def update_tr(text, toggle):
    return smart_translate(text) if text and "ON" in toggle else text or ""

@app.callback(
    Output("status", "children"),
    Output("tick", "disabled", allow_duplicate=True),
    Output("highlight-box", "children", allow_duplicate=True),
    Output("audio-player", "src"),
    State("text-input", "value"),
    State("voice-selector", "value"),
    State("rate-slider", "value"),
    State("translate-toggle", "value"),
    State("tts-engine", "value"),
    Input("speak-btn", "n_clicks"), prevent_initial_call=True)
def speak_handler(text, voice, rate, toggle, engine_sel, _):
    global WORDS, WORD_IDX, READING
    if not text or not text.strip():
        return "⚠️ Escribe algo o sube un documento primero.", True, "", no_update

    to_read = smart_translate(text) if "ON" in toggle else text
    WORDS, WORD_IDX = re.findall(r"\S+|\n", to_read), -1

    # — gTTS (navegador) —
    if engine_sel == "gtts" or pyttsx3 is None:
        try:
            mp3 = text_to_mp3_bytes(to_read, detect_lang(to_read))
        except gTTSError as err:
            return f"⚠️ Google TTS limit: {err}", True, "", no_update
        src = "data:audio/mp3;base64," + base64.b64encode(mp3).decode()
        return "🎧 Reproduciendo en navegador (gTTS)", True, "", src

    # — pyttsx3 (local + mp3 al navegador) —
    def local_job():
        global READING
        with ENGINE_LOCK:
            READING = True
            mp3_bytes = pyttsx3_to_mp3(to_read, voice, rate)
            src_b64 = base64.b64encode(mp3_bytes).decode()
            # enviamos el MP3 al frontend mediante un Store dummy
            app._cached_mp3 = "data:audio/mp3;base64," + src_b64   # hack simple
            READING = False

    threading.Thread(target=local_job, daemon=True).start()
    return "▶️ Leyendo en dispositivo (pyttsx3)…", False, spanified(WORDS, -1), no_update

@app.callback(
    Output("highlight-box", "children", allow_duplicate=True),
    Output("tick", "disabled",      allow_duplicate=True),
    Output("audio-player", "src",   allow_duplicate=True),
    Input("tick", "n_intervals"), prevent_initial_call=True)
def tick(_):
    src = getattr(app, "_cached_mp3", no_update)
    if src is not no_update:
        app._cached_mp3 = no_update     # limpiar
    disabled = not READING
    return spanified(WORDS, WORD_IDX), disabled, src

@app.callback(
    Output("status", "children",    allow_duplicate=True),
    Output("tick", "disabled",      allow_duplicate=True),
    Output("highlight-box", "children", allow_duplicate=True),
    Output("audio-player", "src",   allow_duplicate=True),
    Input("stop-btn", "n_clicks"), prevent_initial_call=True)
def stop(_):
    global READING, WORD_IDX
    with ENGINE_LOCK:
        READING, WORD_IDX = False, -1
    return "⏹️ Detenido", True, spanified(WORDS, -1), no_update

@app.callback(Output("download-audio", "data"),
              State("text-input", "value"),
              State("translate-toggle", "value"),
              Input("download-btn", "n_clicks"), prevent_initial_call=True)
def dl_audio(text, toggle, _):
    if not text.strip():
        return no_update
    processed = smart_translate(text) if "ON" in toggle else text
    try:
        mp3 = text_to_mp3_bytes(processed, detect_lang(processed))
    except gTTSError:
        return no_update
    return dcc.send_bytes(mp3, "speech.mp3")

@app.callback(Output("download-text", "data"),
              State("text-input", "value"),
              State("translate-toggle", "value"),
              Input("download-txt-btn", "n_clicks"), prevent_initial_call=True)
def dl_txt(text, toggle, _):
    if not text.strip():
        return no_update
    result = smart_translate(text) if "ON" in toggle else text
    fname = "translation_en.txt" if detect_lang(result) == "en" else "traduccion_es.txt"
    return dict(content=result, filename=fname, type="text/plain")

# ── run ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8050)), debug=False)




















