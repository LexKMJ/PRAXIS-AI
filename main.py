import os
import re
import json
import time
import html
import hashlib
import secrets
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, request, jsonify, Response, session, redirect
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from openai import OpenAI

# --- Cargar la clave desde el .env ---
load_dotenv(Path(__file__).parent / ".env", override=True)
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_KEY)

MODELO = "gpt-5-mini"

INSTRUCCIONES = """
Eres Praxis, un chatbot amable y cercano con dos modos.

MODO CHARLA: si la persona saluda, charla o hace preguntas generales, responde de forma natural y breve, como un chatbot normal.

MODO TUTOR: cuando la persona te plantee un problema o diga que está atascada, guíala paso a paso con estas reglas:

1. Al inicio, da una sola pista (conceptual, estratégica o de verificación), empezando por la más leve. No des pasos, cálculos, código ni la respuesta final. Termina con una pregunta que invite a intentar algo.

2. Cuando la persona responda a tu pista, di claramente si su respuesta está bien o mal.

3. Si está MAL: dile con amabilidad qué parte no cuadra, sin darle la corrección completa, y da una pista nueva y un poco más directa. Termina con una pregunta.

4. Si está BIEN: confírmalo, muestra la operación resuelta únicamente hasta el punto al que la persona ha llegado (con el desarrollo de ese tramo, ordenado y claro), y luego da una nueva pista para el siguiente paso, sin resolverlo. Termina con una pregunta.

5. Repite el ciclo de la regla 4 hasta que la persona llegue por sí misma al resultado final. Solo entonces confírmalo, muestra la operación completa de principio a fin y felicítala.

6. Nunca adelantes pasos que la persona no ha alcanzado, aunque insistan. Si piden la respuesta directa, anímala con calidez y dale otra pista.

FUENTES: cuando necesites datos, definiciones o hechos, busca en la web y apóyate únicamente en las fuentes que encuentres (universidades, artículos académicos y sitios gubernamentales). Si no encuentras una fuente confiable, dilo con honestidad en lugar de inventar. Cuando uses una fuente, menciónala al final del mensaje. Esto no cambia las reglas del modo tutor: las fuentes sirven para guiar con pistas, nunca para dar la solución completa antes de tiempo.

Responde en el idioma de la persona, por defecto en español, con mensajes breves y motivadores.
"""

# --- Solo se busca en estos sitios (universidades, artículos, gobierno) ---
# Importante: OpenAI solo acepta dominios completos (no "gov" ni "edu" sueltos).
DOMINIOS_PERMITIDOS = [
    # Gubernamentales
    "gob.mx", "inegi.org.mx", "conahcyt.mx", "gob.es", "gov.uk",
    "nasa.gov", "nih.gov", "ncbi.nlm.nih.gov", "noaa.gov", "nist.gov",
    "cdc.gov", "nsf.gov", "census.gov", "usgs.gov", "ed.gov",
    "who.int", "un.org", "unesco.org", "europa.eu", "cepal.org",
    # Universidades
    "unam.mx", "ipn.mx", "tec.mx", "uanl.mx", "udg.mx", "uam.mx", "colmex.mx",
    "mit.edu", "stanford.edu", "harvard.edu", "berkeley.edu", "yale.edu",
    "princeton.edu", "caltech.edu", "cmu.edu", "ox.ac.uk", "cam.ac.uk",
    "uchile.cl", "uba.ar", "usp.br", "unal.edu.co", "ucm.es", "ub.edu",
    # Artículos y revistas académicas
    "scielo.org", "redalyc.org", "dialnet.unirioja.es", "scholar.google.com",
    "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "jstor.org", "nature.com",
    "science.org", "sciencedirect.com", "springer.com", "wiley.com",
    "ieee.org", "acm.org", "researchgate.net",
]

app = Flask(__name__)

# --- Seguridad de las sesiones ---
# En Render define la variable de entorno SECRET_KEY con un texto largo y aleatorio.
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY:
    print("AVISO: define la variable de entorno SECRET_KEY (ver instrucciones).")
    if OPENAI_KEY:
        SECRET_KEY = hashlib.sha256(("praxis:" + OPENAI_KEY).encode()).hexdigest()
    else:
        SECRET_KEY = secrets.token_hex(32)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("RENDER")),   # solo https en Render
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# --- Base de datos ---
# En Render usa una base Postgres (variable DATABASE_URL).
# En tu computadora usa un archivo SQLite local (praxis.db).
uri = os.getenv("DATABASE_URL", "").strip()
if uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)
if not uri:
    uri = "sqlite:///" + str(Path(__file__).parent / "praxis.db")
app.config["SQLALCHEMY_DATABASE_URI"] = uri
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
db = SQLAlchemy(app)


class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False, index=True)
    nombre = db.Column(db.String(80), nullable=False)
    clave_hash = db.Column(db.String(300), nullable=False)


class Conversacion(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True)
    titulo = db.Column(db.String(120), nullable=False, default="Conversación")
    mensajes = db.Column(db.Text, nullable=False, default="[]")
    actualizado = db.Column(db.BigInteger, nullable=False, default=0)


with app.app_context():
    try:
        db.create_all()
    except Exception as error:   # por si dos procesos crean las tablas a la vez
        print("create_all:", error)


# --- Utilidades de sesión ---
def usuario_actual():
    uid = session.get("uid")
    return db.session.get(Usuario, uid) if uid else None


def login_api(f):
    @wraps(f)
    def envoltura(*args, **kwargs):
        if not usuario_actual():
            return jsonify(error="Inicia sesión para continuar.", respuesta="Inicia sesión para continuar."), 401
        return f(*args, **kwargs)
    return envoltura


# Límite sencillo de intentos (en memoria) contra contraseñas adivinadas por fuerza bruta
_INTENTOS = {}

def _ip():
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()

def demasiados_intentos(clave, maximo, ventana=600):
    ahora = time.time()
    recientes = [t for t in _INTENTOS.get(clave, []) if ahora - t < ventana]
    _INTENTOS[clave] = recientes
    return len(recientes) >= maximo

def registrar_intento(clave):
    _INTENTOS.setdefault(clave, []).append(time.time())


@app.after_request
def sin_cache(resp):
    # Que el navegador no guarde páginas privadas (así el botón "atrás" no las muestra tras cerrar sesión)
    if not request.path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# --- La página de inicio de sesión ---
LOGIN = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Praxis · Iniciar sesión</title>
<link rel="icon" href="/static/praxis.png">
<meta name="theme-color" content="#4f46e5">
<style>
  :root {
    --bg: #ffffff; --surface: #f4f4f8; --border: #e4e4ec; --text: #1c1b29;
    --muted: #6b6a80; --accent: #4f46e5; --error: #c62828;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #14131f; --surface: #1e1d2d; --border: #2d2c40; --text: #ececf4;
      --muted: #9a99b3; --accent: #8b85ff; --error: #ff8a80;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100dvh; display: grid; place-items: center; padding: 20px;
    background: var(--bg); color: var(--text);
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; line-height: 1.5;
  }
  .tarjeta { width: 100%; max-width: 380px; text-align: center; }
  .logo {
    width: 56px; height: 56px; border-radius: 17px; background: #4f46e5; color: #fff;
    display: grid; place-items: center; font-weight: 700; font-size: 28px; margin: 0 auto 18px;
    background: url("/static/praxis.png") center / cover no-repeat #4f46e5;
  }
  h1 { margin: 0 0 6px; font-size: 24px; font-weight: 650; }
  .sub { margin: 0 0 24px; color: var(--muted); font-size: 15px; }
  form { text-align: left; display: flex; flex-direction: column; gap: 14px; }
  label { display: flex; flex-direction: column; gap: 6px; font-size: 14px; font-weight: 600; }
  label[hidden] { display: none; }
  input {
    font: inherit; font-weight: 400; padding: 12px 14px; border-radius: 12px;
    border: 1px solid var(--border); background: var(--surface); color: var(--text);
  }
  input:focus { outline: 2px solid var(--accent); outline-offset: 1px; border-color: var(--accent); }
  #error { min-height: 20px; margin: 0; font-size: 14px; color: var(--error); }
  #ir {
    font: inherit; font-weight: 600; padding: 12px; border: 0; border-radius: 12px;
    background: var(--accent); color: #fff; cursor: pointer;
  }
  #ir:disabled { opacity: 0.55; cursor: wait; }
  #ir:focus-visible, #cambiar:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .cambio { margin: 20px 0 0; font-size: 14px; color: var(--muted); }
  #cambiar { font: inherit; background: none; border: 0; padding: 0; color: var(--accent); font-weight: 600; cursor: pointer; }
</style>
</head>
<body>
<main class="tarjeta">
  <div class="logo"></div>
  <h1 id="titulo">Inicia sesión</h1>
  <p class="sub" id="sub">Entra para continuar tus conversaciones con Praxis.</p>
  <form id="form" novalidate>
    <label id="l-nombre" hidden>Nombre
      <input id="nombre" maxlength="40" autocomplete="name">
    </label>
    <label>Correo electrónico
      <input id="email" type="email" autocomplete="email" maxlength="200" required>
    </label>
    <label>Contraseña
      <input id="clave" type="password" autocomplete="current-password" maxlength="128" required>
    </label>
    <p id="error" role="alert"></p>
    <button id="ir" type="submit">Entrar</button>
  </form>
  <p class="cambio"><span id="txt-cambio">¿Aún no tienes cuenta?</span> <button type="button" id="cambiar">Crear cuenta</button></p>
</main>
<script>
  let modo = "login";   // "login" o "registro"
  const $ = (id) => document.getElementById(id);

  function aplicarModo() {
    const reg = modo === "registro";
    $("titulo").textContent = reg ? "Crea tu cuenta" : "Inicia sesión";
    $("sub").textContent = reg ? "Tus conversaciones quedarán guardadas en tu cuenta."
                               : "Entra para continuar tus conversaciones con Praxis.";
    $("l-nombre").hidden = !reg;
    $("clave").autocomplete = reg ? "new-password" : "current-password";
    $("ir").textContent = reg ? "Crear cuenta" : "Entrar";
    $("txt-cambio").textContent = reg ? "¿Ya tienes cuenta?" : "¿Aún no tienes cuenta?";
    $("cambiar").textContent = reg ? "Inicia sesión" : "Crear cuenta";
    $("error").textContent = "";
  }
  $("cambiar").addEventListener("click", () => {
    modo = modo === "login" ? "registro" : "login";
    aplicarModo();
  });

  $("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const reg = modo === "registro";
    const cuerpo = { email: $("email").value.trim(), clave: $("clave").value };
    if (reg) cuerpo.nombre = $("nombre").value.trim();
    $("error").textContent = "";
    $("ir").disabled = true;
    try {
      const r = await fetch(reg ? "/api/registro" : "/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cuerpo)
      });
      const d = await r.json();
      if (r.ok) { location.href = "/"; return; }
      $("error").textContent = d.error || "No se pudo completar la solicitud.";
    } catch (err) {
      $("error").textContent = "No se pudo conectar con el servidor. Inténtalo de nuevo.";
    }
    $("ir").disabled = false;
  });
</script>
</body>
</html>
"""

# --- La página del chat (HTML + CSS + JavaScript) ---
# Es un string "raw" (r""") para que los \n del JavaScript no se rompan.
PAGINA = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Praxis</title>
<link rel="manifest" href="/manifest.json">
<link rel="icon" href="/static/praxis.png">
<meta name="theme-color" content="#4f46e5">
<link rel="apple-touch-icon" href="/static/icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<style>
  :root {
    --bg: #ffffff;
    --surface: #f4f4f8;
    --border: #e4e4ec;
    --text: #1c1b29;
    --muted: #6b6a80;
    --accent: #4f46e5;
    --accent-soft: #eceafd;
    --user-bg: #4f46e5;
    --user-text: #ffffff;
    --ancho-lateral: 280px;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #14131f;
      --surface: #1e1d2d;
      --border: #2d2c40;
      --text: #ececf4;
      --muted: #9a99b3;
      --accent: #8b85ff;
      --accent-soft: #26244a;
      --user-bg: #4f46e5;
      --user-text: #ffffff;
    }
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    font-size: 16px; line-height: 1.55;
  }
  button { font-family: inherit; }
  button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  #app { height: 100dvh; display: flex; overflow: hidden; }

  /* ---------- Barra lateral con el historial ---------- */
  #lateral {
    width: var(--ancho-lateral); flex: none; display: flex; flex-direction: column;
    background: var(--surface); border-right: 1px solid var(--border);
    padding: calc(12px + env(safe-area-inset-top, 0px)) 12px calc(12px + env(safe-area-inset-bottom, 0px));
    transition: transform 0.3s ease, margin-left 0.3s ease;
  }
  #velo { display: none; }
  .btn-nueva {
    display: flex; align-items: center; gap: 8px; width: 100%;
    background: var(--bg); color: var(--text); border: 1px solid var(--border);
    padding: 10px 12px; border-radius: 12px; font-size: 15px; cursor: pointer;
  }
  .btn-nueva:hover { border-color: var(--accent); }
  .btn-nueva svg { width: 16px; height: 16px; color: var(--accent); }
  .titulo-lista { margin: 18px 6px 8px; font-size: 13px; color: var(--muted); }
  #historial { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
  #vacio { color: var(--muted); font-size: 14px; padding: 6px; }

  .item {
    display: flex; align-items: center; border-radius: 10px;
    max-height: 48px; overflow: hidden;
    transition: max-height 0.28s ease, opacity 0.28s ease, background 0.15s;
  }
  .item:hover { background: var(--border); }
  .item.activo { background: var(--accent-soft); }
  .item.nuevo { animation: entraItem 0.35s ease; }
  .item.saliendo { max-height: 0; opacity: 0; }
  @keyframes entraItem {
    from { opacity: 0; transform: translateX(-14px); max-height: 0; }
    to { opacity: 1; transform: none; max-height: 48px; }
  }
  .item .abrir {
    flex: 1; min-width: 0; text-align: left; background: transparent; border: 0;
    color: var(--text); padding: 10px; font-size: 15px; cursor: pointer;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .item.activo .abrir { color: var(--accent); font-weight: 600; }
  .item .borrar {
    flex: none; width: 32px; height: 32px; margin-right: 4px; border: 0; border-radius: 8px;
    background: transparent; color: var(--muted); font-size: 18px; cursor: pointer; line-height: 1;
  }
  .item .borrar:hover { background: var(--bg); color: #d93a3a; }
  @media (hover: hover) {
    .item .borrar { opacity: 0; transition: opacity 0.15s; }
    .item:hover .borrar, .item:focus-within .borrar { opacity: 1; }
  }
  .borrar-todo {
    margin-top: 6px; background: transparent; border: 0; color: var(--muted);
    font-size: 13px; cursor: pointer; padding: 8px 6px; text-align: left;
  }
  .borrar-todo:hover { color: #d93a3a; }

  /* Cuenta de la persona */
  .usuario {
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    margin-top: 6px; padding-top: 12px; border-top: 1px solid var(--border);
  }
  .usuario .nombre {
    font-size: 14px; font-weight: 600; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  #salir {
    flex: none; background: transparent; border: 1px solid var(--border); color: var(--text);
    border-radius: 10px; padding: 6px 10px; font-size: 13px; cursor: pointer;
  }
  #salir:hover { background: var(--bg); }

  /* Escritorio: la barra se oculta deslizándola hacia la izquierda */
  @media (min-width: 861px) {
    #app:not(.abierto) #lateral { margin-left: calc(var(--ancho-lateral) * -1); }
  }
  /* Celular: la barra es un panel que se desliza sobre el chat */
  @media (max-width: 860px) {
    #lateral {
      position: fixed; top: 0; bottom: 0; left: 0; z-index: 30;
      width: min(var(--ancho-lateral), 85vw); transform: translateX(-100%);
      box-shadow: 0 0 30px rgba(0, 0, 0, 0.25);
    }
    #app.abierto #lateral { transform: none; }
    #velo {
      display: block; position: fixed; inset: 0; z-index: 20; background: rgba(0, 0, 0, 0.45);
      opacity: 0; pointer-events: none; transition: opacity 0.3s ease;
    }
    #app.abierto #velo { opacity: 1; pointer-events: auto; }
  }

  /* ---------- Zona principal ---------- */
  #principal {
    flex: 1; min-width: 0; display: flex; flex-direction: column;
    padding-top: env(safe-area-inset-top, 0px);
  }
  header {
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    padding: 10px 16px; border-bottom: 1px solid var(--border);
  }
  .izq { display: flex; align-items: center; gap: 10px; }
  #menu {
    width: 36px; height: 36px; border: 1px solid var(--border); border-radius: 10px;
    background: transparent; color: var(--text); cursor: pointer; display: grid; place-items: center;
  }
  #menu:hover { background: var(--surface); }
  #menu svg { width: 18px; height: 18px; }
  .marca { display: flex; align-items: center; gap: 10px; font-weight: 650; font-size: 18px; }
  .logo {
    width: 30px; height: 30px; border-radius: 9px; background: var(--accent);
    color: #fff; display: grid; place-items: center; font-weight: 700; font-size: 16px;
  }
  #nuevo {
    background: transparent; color: var(--text); border: 1px solid var(--border);
    padding: 7px 12px; border-radius: 10px; font-size: 14px; cursor: pointer;
  }
  #nuevo:hover { background: var(--surface); }

  #chat { flex: 1; overflow-y: auto; scroll-behavior: smooth; }
  .columna { width: 100%; max-width: 760px; margin: 0 auto; padding: 24px 16px; }

  /* Pantalla de bienvenida */
  #bienvenida { text-align: center; padding-top: 12vh; }
  #bienvenida .logo { width: 52px; height: 52px; border-radius: 16px; font-size: 26px; margin: 0 auto 16px; }
  #bienvenida h1 { margin: 0 0 6px; font-size: 26px; font-weight: 650; }
  #bienvenida p { margin: 0 auto 28px; color: var(--muted); max-width: 440px; }
  .sugerencias { display: grid; gap: 10px; grid-template-columns: 1fr 1fr; max-width: 560px; margin: 0 auto; }
  .sugerencia {
    text-align: left; padding: 12px 14px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); border-radius: 14px;
    font-size: 15px; cursor: pointer;
  }
  .sugerencia:hover { background: var(--surface); }
  @media (max-width: 520px) { .sugerencias { grid-template-columns: 1fr; } }

  /* Mensajes */
  .fila { display: flex; gap: 12px; margin-bottom: 22px; }
  .fila.anim { animation: aparece 0.32s ease; }
  @keyframes aparece { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
  .fila.user { justify-content: flex-end; }
  .fila.user .burbuja {
    background: var(--user-bg); color: var(--user-text);
    padding: 10px 16px; border-radius: 20px 20px 6px 20px; max-width: 85%;
  }
  .fila.bot .avatar {
    flex: none; width: 30px; height: 30px; border-radius: 9px; background: var(--accent);
    color: #fff; display: grid; place-items: center; font-weight: 700; font-size: 15px; margin-top: 2px;
  }
  .fila.bot .burbuja { flex: 1; min-width: 0; padding-top: 3px; }
  .burbuja { white-space: pre-wrap; overflow-wrap: anywhere; }
  .burbuja code {
    background: var(--surface); border: 1px solid var(--border);
    padding: 1px 6px; border-radius: 6px; font-size: 0.92em;
    font-family: ui-monospace, Menlo, Consolas, monospace;
  }
  .burbuja a { color: var(--accent); }

  /* Razonamiento desplegable */
  details.razon {
    white-space: normal; margin-bottom: 10px; border: 1px solid var(--border);
    border-radius: 12px; background: var(--surface); padding: 8px 12px; font-size: 14px;
  }
  details.razon summary { cursor: pointer; color: var(--muted); }
  details.razon div { margin-top: 8px; color: var(--muted); white-space: pre-wrap; }

  /* Indicador "pensando" */
  .estado { display: inline-flex; align-items: center; gap: 10px; color: var(--muted); font-size: 15px; }
  .puntos { display: inline-flex; gap: 5px; padding: 8px 0; }
  .puntos span {
    width: 7px; height: 7px; border-radius: 50%; background: var(--muted);
    animation: salto 1.2s infinite ease-in-out;
  }
  .puntos span:nth-child(2) { animation-delay: 0.15s; }
  .puntos span:nth-child(3) { animation-delay: 0.3s; }
  @keyframes salto { 0%, 60%, 100% { opacity: 0.3; transform: translateY(0); }
                     30% { opacity: 1; transform: translateY(-4px); } }

  /* Caja de escritura */
  .zona-entrada { padding: 8px 16px calc(14px + env(safe-area-inset-bottom, 0px)); }
  .barra { max-width: 760px; margin: 0 auto 8px; display: flex; }
  #pensar {
    display: inline-flex; align-items: center; gap: 7px;
    background: transparent; color: var(--muted); border: 1px solid var(--border);
    padding: 6px 12px; border-radius: 999px; font-size: 14px; cursor: pointer;
  }
  #pensar svg { width: 16px; height: 16px; }
  #pensar:hover { background: var(--surface); }
  #pensar[aria-pressed="true"] {
    background: var(--accent-soft); color: var(--accent); border-color: var(--accent);
  }
  #formulario {
    max-width: 760px; margin: 0 auto; display: flex; align-items: flex-end; gap: 8px;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 24px; padding: 8px 8px 8px 18px;
  }
  #formulario:focus-within { border-color: var(--accent); }
  #texto {
    flex: 1; border: 0; outline: 0; background: transparent; color: var(--text);
    font: inherit; resize: none; max-height: 180px; padding: 7px 0; line-height: 1.45;
  }
  #texto::placeholder { color: var(--muted); }
  #enviar {
    flex: none; width: 38px; height: 38px; border: 0; border-radius: 50%;
    background: var(--accent); color: #fff; cursor: pointer;
    display: grid; place-items: center;
  }
  #enviar:disabled { opacity: 0.35; cursor: not-allowed; }
  #enviar svg { width: 18px; height: 18px; }
  .nota { text-align: center; color: var(--muted); font-size: 12px; margin: 8px 0 0; }

  /* Foto de perfil de Praxis (borra este bloque si aún no subes static/praxis.png) */
  .logo, .fila.bot .avatar {
    background: url("/static/praxis.png") center / cover no-repeat;
    color: transparent; font-size: 0;
  }

  /* Respeta a quien prefiere menos movimiento */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; }
    #chat { scroll-behavior: auto; }
    .puntos span { opacity: 0.6; }
  }
</style>
</head>
<body>
<div id="app">

  <aside id="lateral" aria-label="Historial de conversaciones">
    <button class="btn-nueva" id="nueva-lateral" type="button">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
      Nueva conversación
    </button>
    <div class="titulo-lista">Historial</div>
    <div id="historial">
      <div id="vacio">Aquí aparecerán tus conversaciones.</div>
    </div>
    <button class="borrar-todo" id="borrar-todo" type="button">Borrar todo el historial</button>
    <div class="usuario">
      <span class="nombre">__NOMBRE__</span>
      <button id="salir" type="button">Cerrar sesión</button>
    </div>
  </aside>
  <div id="velo"></div>

  <div id="principal">
    <header>
      <div class="izq">
        <button id="menu" type="button" aria-label="Mostrar u ocultar el historial">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
        </button>
        <div class="marca"><div class="logo">P</div>Praxis</div>
      </div>
      <button id="nuevo" type="button">Nueva conversación</button>
    </header>

    <div id="chat">
      <div class="columna" id="lista">
        <div id="bienvenida">
          <div class="logo">P</div>
          <h1>¿En qué te ayudo hoy?</h1>
          <p>Charlo contigo y, si te atascas con un problema, te doy pistas para que lo resuelvas tú.</p>
          <div class="sugerencias">
            <button class="sugerencia" type="button">Estoy atascado con un problema de matemáticas</button>
            <button class="sugerencia" type="button">Mi código no funciona y no sé por qué</button>
            <button class="sugerencia" type="button">Ayúdame a entender un tema nuevo</button>
            <button class="sugerencia" type="button">Solo quiero platicar un rato</button>
          </div>
        </div>
      </div>
    </div>

    <div class="zona-entrada">
      <div class="barra">
        <button id="pensar" type="button" aria-pressed="false" title="Praxis razona con más calma. Tarda más en responder.">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.2 1 2V17h6v-.3c0-.8.4-1.5 1-2A7 7 0 0 0 12 2z"/></svg>
          Pensar a fondo
        </button>
      </div>
      <form id="formulario">
        <textarea id="texto" rows="1" placeholder="Escribe tu mensaje..." autocomplete="off"></textarea>
        <button id="enviar" type="submit" aria-label="Enviar" disabled>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
        </button>
      </form>
      <p class="nota">Praxis puede equivocarse. Verifica la información importante.</p>
    </div>
  </div>
</div>

<script>
  const app = document.getElementById("app");
  const chat = document.getElementById("chat");
  const lista = document.getElementById("lista");
  const formulario = document.getElementById("formulario");
  const texto = document.getElementById("texto");
  const enviar = document.getElementById("enviar");
  const nuevoBtn = document.getElementById("nuevo");
  const nuevaLateral = document.getElementById("nueva-lateral");
  const pensar = document.getElementById("pensar");
  const bienvenida = document.getElementById("bienvenida");
  const historial = document.getElementById("historial");
  const vacio = document.getElementById("vacio");
  const menu = document.getElementById("menu");
  const velo = document.getElementById("velo");
  const borrarTodo = document.getElementById("borrar-todo");
  const salir = document.getElementById("salir");

  let convs = [];          // todas las conversaciones: {id, titulo, mensajes, actualizado}
  let actual = null;       // conversación abierta (null = pantalla de bienvenida)
  let nuevoId = null;      // para animar el elemento recién creado en la lista
  let ocupado = false;
  let pensando = false;    // modo "Pensar a fondo"

  /* ---------- Guardado en tu cuenta (servidor) ---------- */
  function aLogin() { location.href = "/login"; }

  async function cargar() {
    try {
      const r = await fetch("/api/convs");
      if (r.status === 401) { aLogin(); return; }
      const d = await r.json();
      if (Array.isArray(d)) convs = d;
    } catch (e) { convs = []; }
  }
  function guardar(conv) {
    fetch("/api/convs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(conv)
    }).then((r) => { if (r.status === 401) aLogin(); }).catch(() => {});
  }

  /* ---------- Formato de texto ---------- */
  function escapar(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  // Formato mínimo y seguro: enlaces, **negrita** y `código`
  function formatear(s) {
    return escapar(s)
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  }
  function htmlBot(contenido, razon) {
    let html = "";
    if (razon) {
      html += '<details class="razon"><summary>Ver cómo pensó Praxis</summary><div>' +
              escapar(razon) + '</div></details>';
    }
    return html + formatear(contenido);
  }

  /* ---------- Mensajes en pantalla ---------- */
  function agregar(rol, contenido, animar) {
    bienvenida.style.display = "none";
    const fila = document.createElement("div");
    fila.className = "fila " + (rol === "user" ? "user" : "bot") + (animar ? " anim" : "");
    if (rol !== "user") {
      const av = document.createElement("div");
      av.className = "avatar";
      av.textContent = "P";
      fila.appendChild(av);
    }
    const b = document.createElement("div");
    b.className = "burbuja";
    if (rol === "user") b.textContent = contenido;
    else b.innerHTML = contenido;
    fila.appendChild(b);
    lista.appendChild(fila);
    chat.scrollTop = chat.scrollHeight;
    return b;
  }
  function limpiarMensajes() {
    lista.querySelectorAll(".fila").forEach((f) => f.remove());
  }

  /* ---------- Historial (barra lateral) ---------- */
  function renderLista() {
    historial.querySelectorAll(".item").forEach((i) => i.remove());
    vacio.style.display = convs.length ? "none" : "";
    [...convs].sort((a, b) => b.actualizado - a.actualizado).forEach((c) => {
      const item = document.createElement("div");
      item.className = "item" + (actual && actual.id === c.id ? " activo" : "") +
                       (c.id === nuevoId ? " nuevo" : "");
      const ab = document.createElement("button");
      ab.className = "abrir";
      ab.type = "button";
      ab.textContent = c.titulo;
      ab.title = c.titulo;
      ab.addEventListener("click", () => abrir(c.id));
      const bo = document.createElement("button");
      bo.className = "borrar";
      bo.type = "button";
      bo.textContent = "×";
      bo.setAttribute("aria-label", "Eliminar conversación");
      bo.addEventListener("click", (e) => { e.stopPropagation(); borrar(c.id, item); });
      item.appendChild(ab);
      item.appendChild(bo);
      historial.appendChild(item);
    });
    nuevoId = null;
  }

  function abrir(id) {
    if (ocupado) return;
    const c = convs.find((x) => x.id === id);
    if (!c) return;
    actual = c;
    limpiarMensajes();
    c.mensajes.forEach((m) => {
      if (m.role === "user") agregar("user", m.content, false);
      else agregar("assistant", htmlBot(m.content, m.razon), false);
    });
    renderLista();
    cerrarMovil();
    chat.scrollTop = chat.scrollHeight;
  }

  function nueva() {
    if (ocupado) return;
    actual = null;
    limpiarMensajes();
    bienvenida.style.display = "";
    renderLista();
    cerrarMovil();
    texto.focus();
  }

  function borrar(id, item) {
    if (ocupado) return;
    item.classList.add("saliendo");
    setTimeout(() => {
      convs = convs.filter((c) => c.id !== id);
      fetch("/api/convs/" + encodeURIComponent(id), { method: "DELETE" }).catch(() => {});
      if (actual && actual.id === id) { nueva(); } else { renderLista(); }
    }, 280);
  }

  /* ---------- Abrir y cerrar la barra lateral ---------- */
  function cerrarMovil() {
    if (window.innerWidth <= 860) app.classList.remove("abierto");
  }
  menu.addEventListener("click", () => app.classList.toggle("abierto"));
  velo.addEventListener("click", () => app.classList.remove("abierto"));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") cerrarMovil();
  });
  borrarTodo.addEventListener("click", () => {
    if (ocupado || convs.length === 0) return;
    if (!confirm("¿Borrar todas tus conversaciones? Esta acción no se puede deshacer.")) return;
    historial.querySelectorAll(".item").forEach((i) => i.classList.add("saliendo"));
    setTimeout(() => {
      convs = [];
      fetch("/api/convs", { method: "DELETE" }).catch(() => {});
      nueva();
    }, 280);
  });
  salir.addEventListener("click", async () => {
    try { await fetch("/api/salir", { method: "POST" }); } catch (e) {}
    aLogin();
  });

  /* ---------- Enviar mensajes ---------- */
  function actualizarBoton() {
    enviar.disabled = ocupado || texto.value.trim() === "";
  }
  function autoAltura() {
    texto.style.height = "auto";
    texto.style.height = Math.min(texto.scrollHeight, 180) + "px";
  }

  async function mandar(pregunta) {
    if (!pregunta || ocupado) return;
    ocupado = true;
    texto.value = "";
    autoAltura();
    actualizarBoton();

    // Si no hay conversación abierta, se crea una nueva en el historial
    if (!actual) {
      actual = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        titulo: pregunta.length > 40 ? pregunta.slice(0, 40) + "…" : pregunta,
        mensajes: [],
        actualizado: Date.now()
      };
      convs.push(actual);
      nuevoId = actual.id;
    }
    const conv = actual;
    conv.mensajes.push({ role: "user", content: pregunta });
    conv.actualizado = Date.now();
    guardar(conv);
    renderLista();

    agregar("user", pregunta, true);
    const etiqueta = pensando ? "Pensando a fondo…" : "";
    const espera = agregar("assistant",
      '<span class="estado"><span class="puntos"><span></span><span></span><span></span></span>' + etiqueta + '</span>', true);

    try {
      const r = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mensajes: conv.mensajes.map((m) => ({ role: m.role, content: m.content })),
          pensar: pensando
        })
      });
      if (r.status === 401) { aLogin(); return; }
      const datos = await r.json();
      espera.innerHTML = htmlBot(datos.respuesta, datos.razonamiento);
      if (r.ok) {
        conv.mensajes.push({ role: "assistant", content: datos.respuesta, razon: datos.razonamiento || "" });
      } else {
        conv.mensajes.pop();   // si falló, no guardamos ese turno
      }
    } catch (error) {
      espera.textContent = "No se pudo conectar con el servidor. Revisa tu conexión e inténtalo de nuevo.";
      conv.mensajes.pop();
    }
    conv.actualizado = Date.now();
    guardar(conv);
    ocupado = false;
    renderLista();
    actualizarBoton();
    chat.scrollTop = chat.scrollHeight;
    texto.focus();
  }

  formulario.addEventListener("submit", (e) => {
    e.preventDefault();
    mandar(texto.value.trim());
  });

  // Enter envía, Shift+Enter hace salto de línea
  texto.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      formulario.requestSubmit();
    }
  });
  texto.addEventListener("input", () => { autoAltura(); actualizarBoton(); });

  pensar.addEventListener("click", () => {
    pensando = !pensando;
    pensar.setAttribute("aria-pressed", String(pensando));
    texto.focus();
  });

  document.querySelectorAll(".sugerencia").forEach((btn) => {
    btn.addEventListener("click", () => mandar(btn.textContent));
  });
  nuevoBtn.addEventListener("click", nueva);
  nuevaLateral.addEventListener("click", nueva);

  /* ---------- Inicio ---------- */
  if (window.innerWidth > 860) app.classList.add("abierto");  // en escritorio empieza abierta
  cargar().then(renderLista);

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js");
  }
</script>
</body>
</html>
"""

# --- Rutas de páginas ---
@app.route("/")
def inicio():
    u = usuario_actual()
    if not u:
        return redirect("/login")
    return PAGINA.replace("__NOMBRE__", html.escape(u.nombre))


@app.route("/login")
def pagina_login():
    if usuario_actual():
        return redirect("/")
    return LOGIN


# --- Cuentas: registro, entrada y salida ---
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _iniciar_sesion(usuario):
    session.clear()
    session["uid"] = usuario.id
    session.permanent = True


@app.route("/api/registro", methods=["POST"])
def api_registro():
    d = request.get_json(silent=True) or {}
    clave_limite = "reg:" + _ip()
    if demasiados_intentos(clave_limite, 10):
        return jsonify(error="Demasiados intentos. Espera unos minutos e inténtalo de nuevo."), 429
    registrar_intento(clave_limite)

    nombre = str(d.get("nombre", "")).strip()[:40]
    email = str(d.get("email", "")).strip().lower()[:200]
    clave = str(d.get("clave", ""))
    if not nombre:
        return jsonify(error="Escribe tu nombre."), 400
    if not EMAIL_RE.match(email):
        return jsonify(error="Escribe un correo electrónico válido."), 400
    if len(clave) < 8:
        return jsonify(error="La contraseña debe tener al menos 8 caracteres."), 400
    if len(clave) > 128:
        return jsonify(error="La contraseña es demasiado larga (máximo 128 caracteres)."), 400
    if Usuario.query.filter_by(email=email).first():
        return jsonify(error="Ya existe una cuenta con ese correo."), 409

    usuario = Usuario(email=email, nombre=nombre, clave_hash=generate_password_hash(clave))
    db.session.add(usuario)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify(error="Ya existe una cuenta con ese correo."), 409
    _iniciar_sesion(usuario)
    return jsonify(ok=True)


@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.get_json(silent=True) or {}
    email = str(d.get("email", "")).strip().lower()[:200]
    clave = str(d.get("clave", ""))[:128]
    clave_limite = "login:" + _ip() + ":" + email
    if demasiados_intentos(clave_limite, 8):
        return jsonify(error="Demasiados intentos. Espera unos minutos e inténtalo de nuevo."), 429

    usuario = Usuario.query.filter_by(email=email).first()
    if not usuario or not check_password_hash(usuario.clave_hash, clave):
        registrar_intento(clave_limite)
        return jsonify(error="Correo o contraseña incorrectos."), 401
    _iniciar_sesion(usuario)
    return jsonify(ok=True)


@app.route("/api/salir", methods=["POST"])
def api_salir():
    session.clear()
    return jsonify(ok=True)


# --- Historial de conversaciones (guardado por cuenta) ---
ID_RE = re.compile(r"^[a-z0-9]{4,40}$")
MAX_CONVERSACIONES = 200

def limpiar_mensajes(lista):
    limpia = []
    if not isinstance(lista, list):
        return limpia
    for m in lista[:200]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            item = {"role": m["role"], "content": m["content"][:20000]}
            if m["role"] == "assistant" and isinstance(m.get("razon"), str) and m["razon"]:
                item["razon"] = m["razon"][:20000]
            limpia.append(item)
    return limpia


@app.route("/api/convs", methods=["GET"])
@login_api
def listar_convs():
    filas = (Conversacion.query.filter_by(usuario_id=session["uid"])
             .order_by(Conversacion.actualizado.desc()).limit(MAX_CONVERSACIONES).all())
    return jsonify([
        {"id": f.id, "titulo": f.titulo, "mensajes": json.loads(f.mensajes), "actualizado": f.actualizado}
        for f in filas
    ])


@app.route("/api/convs", methods=["POST"])
@login_api
def guardar_conv():
    d = request.get_json(silent=True) or {}
    cid = str(d.get("id", ""))
    if not ID_RE.match(cid):
        return jsonify(error="Identificador inválido."), 400
    try:
        actualizado = int(d.get("actualizado") or time.time() * 1000)
    except (TypeError, ValueError):
        actualizado = int(time.time() * 1000)

    fila = db.session.get(Conversacion, cid)
    if fila and fila.usuario_id != session["uid"]:
        return jsonify(error="No permitido."), 403
    if not fila:
        total = Conversacion.query.filter_by(usuario_id=session["uid"]).count()
        if total >= MAX_CONVERSACIONES:
            return jsonify(error="Llegaste al límite de conversaciones. Borra algunas."), 400
        fila = Conversacion(id=cid, usuario_id=session["uid"])
        db.session.add(fila)
    fila.titulo = (str(d.get("titulo") or "Conversación").strip() or "Conversación")[:120]
    fila.mensajes = json.dumps(limpiar_mensajes(d.get("mensajes")), ensure_ascii=False)
    fila.actualizado = actualizado
    db.session.commit()
    return jsonify(ok=True)


@app.route("/api/convs/<cid>", methods=["DELETE"])
@login_api
def borrar_conv(cid):
    Conversacion.query.filter_by(id=cid, usuario_id=session["uid"]).delete()
    db.session.commit()
    return jsonify(ok=True)


@app.route("/api/convs", methods=["DELETE"])
@login_api
def borrar_todas():
    Conversacion.query.filter_by(usuario_id=session["uid"]).delete()
    db.session.commit()
    return jsonify(ok=True)


# --- IA ---
def consultar(mensajes, pensar, con_resumen):
    """Llama a OpenAI. 'pensar' sube el esfuerzo de razonamiento."""
    razonamiento = {"effort": "high" if pensar else "low"}
    if con_resumen:
        razonamiento["summary"] = "auto"
    return client.responses.create(
        model=MODELO,
        instructions=INSTRUCCIONES,
        input=mensajes,
        tools=[{
            "type": "web_search",
            "filters": {"allowed_domains": DOMINIOS_PERMITIDOS},
        }],
        reasoning=razonamiento,
    )


def extraer_razonamiento(r):
    """Junta el resumen del razonamiento, si el modelo lo devolvió."""
    partes = []
    for item in getattr(r, "output", []) or []:
        if getattr(item, "type", "") == "reasoning":
            for s in getattr(item, "summary", None) or []:
                t = getattr(s, "text", "")
                if t:
                    partes.append(t)
    return "\n\n".join(partes)


# --- Ruta que recibe los mensajes y llama a OpenAI (solo con sesión iniciada) ---
@app.route("/chat", methods=["POST"])
@login_api
def chat():
    datos = request.get_json(silent=True) or {}
    pensar = bool(datos.get("pensar"))
    mensajes = datos.get("mensajes", [])
    # Solo aceptamos roles y textos válidos, y limitamos el historial
    mensajes = [
        {"role": m["role"], "content": str(m["content"])[:4000]}
        for m in mensajes
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ][-30:]
    if not mensajes:
        return jsonify(respuesta="No recibí ningún mensaje."), 400
    try:
        try:
            r = consultar(mensajes, pensar, con_resumen=pensar)
        except Exception as error:
            if not pensar:
                raise
            # El resumen del razonamiento puede no estar disponible en tu cuenta:
            # reintentamos sin él para que igual responda.
            print("Reintentando sin resumen de razonamiento:", error)
            r = consultar(mensajes, pensar, con_resumen=False)
        return jsonify(
            respuesta=r.output_text,
            razonamiento=extraer_razonamiento(r) if pensar else "",
        )
    except Exception as error:
        print("Error con OpenAI:", error)  # se ve en los logs del servidor
        return jsonify(respuesta="Ocurrió un error al contactar a la IA. Inténtalo de nuevo en un momento."), 500


@app.route("/manifest.json")
def manifest():
    return jsonify(
        name="Praxis",
        short_name="Praxis",
        start_url="/",
        display="standalone",
        background_color="#ffffff",
        theme_color="#4f46e5",
        icons=[
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    )


@app.route("/sw.js")
def service_worker():
    return Response("self.addEventListener('fetch', () => {});",
                    mimetype="application/javascript")


if __name__ == "__main__":
    app.run(debug=True)
