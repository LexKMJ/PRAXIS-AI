import os
from pathlib import Path
from flask import Flask, request, jsonify, Response
from dotenv import load_dotenv
from openai import OpenAI

# --- Cargar la clave desde el .env ---
load_dotenv(Path(__file__).parent / ".env", override=True)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())

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
DOMINIOS_PERMITIDOS = [
    # Gubernamentales
    "gob.mx", "inegi.org.mx", "sep.gob.mx", "conahcyt.mx", "gov",
    "nasa.gov", "nih.gov", "ncbi.nlm.nih.gov", "noaa.gov", "nist.gov",
    "who.int", "un.org", "unesco.org", "europa.eu",
    # Universidades
    "edu", "unam.mx", "ipn.mx", "itesm.mx", "uanl.mx", "udg.mx",
    "mit.edu", "stanford.edu", "harvard.edu", "berkeley.edu", "ox.ac.uk", "cam.ac.uk",
    # Artículos y revistas académicas
    "scielo.org", "redalyc.org", "dialnet.unirioja.es", "scholar.google.com",
    "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "jstor.org", "nature.com",
    "science.org", "sciencedirect.com", "springer.com", "wiley.com",
    "ieee.org", "acm.org", "researchgate.net",
]

app = Flask(__name__)

# --- La página web (HTML + CSS + JavaScript) ---
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
  #app {
    height: 100dvh; display: flex; flex-direction: column;
    padding-top: env(safe-area-inset-top, 0px);
  }
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 16px; border-bottom: 1px solid var(--border);
  }
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
    font-size: 15px; cursor: pointer; font-family: inherit;
  }
  .sugerencia:hover { background: var(--surface); }
  @media (max-width: 520px) { .sugerencias { grid-template-columns: 1fr; } }

  /* Mensajes */
  .fila { display: flex; gap: 12px; margin-bottom: 22px; }
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
  @media (prefers-reduced-motion: reduce) {
    .puntos span { animation: none; opacity: 0.6; }
    #chat { scroll-behavior: auto; }
  }

  /* Caja de escritura */
  .zona-entrada { padding: 8px 16px calc(14px + env(safe-area-inset-bottom, 0px)); }
  .barra { max-width: 760px; margin: 0 auto 8px; display: flex; }
  #pensar {
    display: inline-flex; align-items: center; gap: 7px;
    background: transparent; color: var(--muted); border: 1px solid var(--border);
    padding: 6px 12px; border-radius: 999px; font: inherit; font-size: 14px; cursor: pointer;
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
  button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .nota { text-align: center; color: var(--muted); font-size: 12px; margin: 8px 0 0; }

  /* Foto de perfil de Praxis (borra este bloque si aún no subes static/praxis.png) */
  .logo, .fila.bot .avatar {
    background: url("/static/praxis.png") center / cover no-repeat;
    color: transparent; font-size: 0;
  }
</style>
</head>
<body>
<div id="app">
  <header>
    <div class="marca"><div class="logo">P</div>Praxis</div>
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

<script>
  const chat = document.getElementById("chat");
  const lista = document.getElementById("lista");
  const formulario = document.getElementById("formulario");
  const texto = document.getElementById("texto");
  const enviar = document.getElementById("enviar");
  const nuevo = document.getElementById("nuevo");
  const pensar = document.getElementById("pensar");
  const bienvenida = document.getElementById("bienvenida");

  let mensajes = [];      // aquí se guarda la conversación
  let ocupado = false;
  let pensando = false;   // modo "Pensar a fondo"

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

  function agregar(rol, contenido) {
    if (bienvenida) bienvenida.style.display = "none";
    const fila = document.createElement("div");
    fila.className = "fila " + (rol === "user" ? "user" : "bot");
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

    agregar("user", pregunta);
    mensajes.push({ role: "user", content: pregunta });
    const etiqueta = pensando ? "Pensando a fondo…" : "";
    const espera = agregar("assistant",
      '<span class="estado"><span class="puntos"><span></span><span></span><span></span></span>' + etiqueta + '</span>');

    try {
      const r = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mensajes: mensajes, pensar: pensando })
      });
      const datos = await r.json();
      let html = "";
      if (datos.razonamiento) {
        html += '<details class="razon"><summary>Ver cómo pensó Praxis</summary><div>' +
                escapar(datos.razonamiento) + '</div></details>';
      }
      html += formatear(datos.respuesta);
      espera.innerHTML = html;
      if (r.ok) mensajes.push({ role: "assistant", content: datos.respuesta });
      else mensajes.pop();   // si falló, no guardamos ese turno
    } catch (error) {
      espera.textContent = "No se pudo conectar con el servidor. Revisa tu conexión e inténtalo de nuevo.";
      mensajes.pop();
    }
    ocupado = false;
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

  nuevo.addEventListener("click", () => {
    if (ocupado) return;
    mensajes = [];
    lista.querySelectorAll(".fila").forEach((f) => f.remove());
    bienvenida.style.display = "";
    texto.focus();
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js");
  }
</script>
</body>
</html>
"""

# --- Ruta que muestra la página ---
@app.route("/")
def inicio():
    return PAGINA


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


# --- Ruta que recibe los mensajes y llama a OpenAI ---
@app.route("/chat", methods=["POST"])
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
