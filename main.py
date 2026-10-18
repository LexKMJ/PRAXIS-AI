import os
import time
from collections import defaultdict
from pathlib import Path
from flask import Flask, request, jsonify, Response
from dotenv import load_dotenv
from openai import OpenAI

# --- Cargar la clave (del .env en tu PC, o de las variables de entorno en el hosting) ---
load_dotenv(Path(__file__).parent / ".env", override=True)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())

INSTRUCCIONES = """
Eres Praxis, un chatbot amable y cercano que tiene dos modos: si la persona saluda, charla o hace preguntas generales, responde de forma natural y breve como un chatbot normal; pero cuando te plantee un problema o diga que está atascada, actúa como tutor y da una sola pista a la vez (conceptual, estratégica o de verificación), empezando por la más leve, sin dar nunca pasos, cálculos, código ni la respuesta final aunque insistan, y terminando con una pregunta que invite a intentar algo; responde en el idioma de la persona, por defecto en español, con mensajes breves y motivadores.
"""

# --- Límites para cuidar tu saldo ---
MAX_MENSAJES = 20          # cuántos mensajes recientes se envían a la IA
MAX_CARACTERES = 1000      # largo máximo de cada mensaje
MAX_POR_HORA = 40          # mensajes por hora por persona (IP)
registro = defaultdict(list)

app = Flask(__name__)

PAGINA = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Praxis</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#060819">
<link rel="icon" href="/static/icon-192.png">
<link rel="apple-touch-icon" href="/static/icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<style>
  :root { --fondo:#060819; --panel:#0e1330; --borde:#1f2750; --texto:#f4f5ff;
          --azul:#3b82f6; --violeta:#8b5cf6; }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--fondo);
         color: var(--texto); display: flex; justify-content: center; }
  #app { width: 100%; max-width: 720px; height: 100dvh; display: flex; flex-direction: column;
         padding-top: env(safe-area-inset-top); padding-bottom: env(safe-area-inset-bottom); }
  header { display: flex; align-items: center; gap: 10px; padding: 12px 16px;
           border-bottom: 1px solid var(--borde); }
  header img { width: 36px; height: 36px; border-radius: 10px; }
  header h1 { font-size: 18px; margin: 0; flex: 1; }
  header button { background: none; border: 1px solid var(--borde); color: var(--texto);
                  border-radius: 10px; padding: 6px 10px; cursor: pointer; }
  #chat { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 82%; padding: 10px 14px; border-radius: 16px; white-space: pre-wrap;
         line-height: 1.45; overflow-wrap: anywhere; }
  .user { align-self: flex-end; color: white;
          background: linear-gradient(135deg, var(--azul), var(--violeta)); border-bottom-right-radius: 4px; }
  .bot { align-self: flex-start; background: var(--panel); border: 1px solid var(--borde);
         border-bottom-left-radius: 4px; }
  form { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--borde); }
  input { flex: 1; padding: 12px 14px; border: 1px solid var(--borde); border-radius: 14px;
          font-size: 16px; background: var(--panel); color: var(--texto); outline: none; }
  input:focus { border-color: var(--violeta); }
  #enviar { padding: 12px 18px; border: 0; border-radius: 14px; font-size: 16px; color: white;
            cursor: pointer; background: linear-gradient(135deg, var(--azul), var(--violeta)); }
  #enviar:disabled { opacity: .5; }
</style>
</head>
<body>
<div id="app">
  <header>
    <img src="/static/icon-192.png" alt="">
    <h1>Praxis</h1>
    <button id="nuevo" type="button">Nuevo chat</button>
  </header>
  <div id="chat"></div>
  <form id="formulario">
    <input id="texto" placeholder="Escribe tu mensaje..." autocomplete="off" maxlength="1000">
    <button id="enviar">Enviar</button>
  </form>
</div>
<script>
  const chat = document.getElementById("chat");
  const formulario = document.getElementById("formulario");
  const texto = document.getElementById("texto");
  const enviar = document.getElementById("enviar");
  let mensajes = [];

  function agregar(rol, contenido) {
    const div = document.createElement("div");
    div.className = "msg " + (rol === "user" ? "user" : "bot");
    div.textContent = contenido;
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    return div;
  }

  function iniciar() {
    chat.innerHTML = "";
    mensajes = [];
    agregar("assistant", "¡Hola! Soy Praxis, tu asistente y tutor. ¿En qué te ayudo?");
  }
  iniciar();
  document.getElementById("nuevo").addEventListener("click", iniciar);

  formulario.addEventListener("submit", async (e) => {
    e.preventDefault();
    const pregunta = texto.value.trim();
    if (!pregunta) return;
    texto.value = "";
    enviar.disabled = true;
    agregar("user", pregunta);
    mensajes.push({ role: "user", content: pregunta });
    const espera = agregar("assistant", "Pensando...");
    try {
      const r = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mensajes })
      });
      const datos = await r.json();
      espera.textContent = datos.respuesta;
      if (r.ok) mensajes.push({ role: "assistant", content: datos.respuesta });
    } catch (err) {
      espera.textContent = "Error de conexión con el servidor.";
    }
    enviar.disabled = false;
    texto.focus();
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js");
  }
</script>
</body>
</html>
"""


@app.route("/")
def inicio():
    return PAGINA


@app.route("/manifest.json")
def manifest():
    return jsonify(
        name="Praxis",
        short_name="Praxis",
        description="Tu chatbot y tutor personal con IA",
        lang="es",
        start_url="/",
        scope="/",
        display="standalone",
        background_color="#060819",
        theme_color="#060819",
        icons=[
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icon-maskable-512.png", "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ],
    )


@app.route("/sw.js")
def service_worker():
    return Response("self.addEventListener('fetch', () => {});",
                    mimetype="application/javascript")


@app.route("/chat", methods=["POST"])
def chat():
    # Límite de uso por persona
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    ahora = time.time()
    registro[ip] = [t for t in registro[ip] if ahora - t < 3600]
    if len(registro[ip]) >= MAX_POR_HORA:
        return jsonify(respuesta="Has llegado al límite de mensajes por hora. Vuelve a intentarlo más tarde."), 429
    registro[ip].append(ahora)

    # Limpiar y limitar lo que llega del navegador
    datos = request.get_json(silent=True) or {}
    mensajes = []
    for m in datos.get("mensajes", [])[-MAX_MENSAJES:]:
        rol = m.get("role")
        contenido = str(m.get("content", ""))[:MAX_CARACTERES]
        if rol in ("user", "assistant") and contenido:
            mensajes.append({"role": rol, "content": contenido})
    if not mensajes:
        return jsonify(respuesta="Escribe un mensaje primero."), 400

    try:
        r = client.responses.create(
            model="gpt-5-nano",
            instructions=INSTRUCCIONES,
            input=mensajes,
        )
        return jsonify(respuesta=r.output_text)
    except Exception as error:
        print("Error con OpenAI:", error)
        return jsonify(respuesta="Ocurrió un error al contactar a la IA. Inténtalo de nuevo."), 500


if __name__ == "__main__":
    app.run(debug=True)
