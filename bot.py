import os
import time
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"

SYSTEM_PROMPT = """Eres un tutor de inglés amigable, paciente y motivador llamado "Tutor AI". 
Tu misión es ayudar a hispanohablantes a aprender inglés de forma práctica y divertida.

REGLAS:
- Siempre responde en ESPAÑOL, pero usa el inglés para enseñar.
- Corrige los errores del usuario con amabilidad, sin hacerlos sentir mal.
- Cuando el usuario escriba en inglés, corrígelo si hay errores y explica por qué.
- Usa emojis para hacer las conversaciones más amenas.
- Adapta el nivel de dificultad al usuario (detecta si es principiante, intermedio o avanzado).
- Da ejercicios prácticos cuando sea apropiado.
- Si el usuario comete un error, muestra la forma correcta con formato: Error -> Correcto.
- Celebra los logros del usuario con entusiasmo.
- Mantén las respuestas concisas (máximo 200 palabras) para no abrumar al usuario.

TEMAS QUE PUEDES ENSEÑAR:
- Vocabulario cotidiano
- Gramatica basica y avanzada
- Frases utiles para viajes, trabajo, etc.
- Conversacion simulada
- Verbos irregulares
- Tiempos verbales
"""

user_histories = {}


# --- Servidor web para que Render no apague el servicio ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot activo!")

    def log_message(self, format, *args):
        pass  # Silencia los logs del servidor


def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


# --- Funciones del bot ---
def send_message(chat_id, text):
    requests.post(f"{TELEGRAM_BASE}/sendMessage", json={
        "chat_id": chat_id,
        "text": text
    })


def send_typing(chat_id):
    requests.post(f"{TELEGRAM_BASE}/sendChatAction", json={
        "chat_id": chat_id,
        "action": "typing"
    })


def ask_gemini(history):
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": history
    }
    response = requests.post(GEMINI_URL, json=payload)
    data = response.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return "Lo siento, hubo un error. Por favor intenta de nuevo."


def handle_update(update):
    if "message" not in update:
        return

    message = update["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if not text:
        return

    user_id = str(chat_id)

    if text == "/start":
        user_histories[user_id] = []
        send_message(chat_id,
            "Hola! Soy tu Tutor de Ingles AI\n\n"
            "Estoy aqui para ayudarte a aprender ingles.\n\n"
            "Puedes:\n"
            "- Escribirme en espanol y te enseno como decirlo en ingles\n"
            "- Escribirme en ingles y te corrijo si hay errores\n"
            "- Pedirme ejercicios con /ejercicio\n\n"
            "Por donde quieres empezar?"
        )
        return

    if text == "/reset":
        user_histories[user_id] = []
        send_message(chat_id, "Conversacion reiniciada! Que quieres aprender hoy?")
        return

    if text == "/help":
        send_message(chat_id,
            "Comandos disponibles:\n\n"
            "/start - Iniciar el bot\n"
            "/reset - Borrar historial\n"
            "/ejercicio - Recibir un ejercicio\n"
            "/help - Ver esta ayuda"
        )
        return

    if text == "/ejercicio":
        text = "Dame un ejercicio corto y practico de ingles para practicar ahora."

    if user_id not in user_histories:
        user_histories[user_id] = []

    history = user_histories[user_id]
    history.append({"role": "user", "parts": [{"text": text}]})

    send_typing(chat_id)
    reply = ask_gemini(history)

    history.append({"role": "model", "parts": [{"text": reply}]})
    user_histories[user_id] = history[-20:]

    send_message(chat_id, reply)


def main():
    # Inicia el servidor web en un hilo separado
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    print("Servidor web iniciado!")

    # Inicia el bot
    print("Bot iniciado!")
    offset = 0

    while True:
        try:
            response = requests.get(
                f"{TELEGRAM_BASE}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35
            )
            data = response.json()

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                handle_update(update)

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
