import os
import time
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
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
"""

user_histories = {}


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot activo!")

    def log_message(self, format, *args):
        pass


def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


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

    # Mostrar respuesta completa en logs para diagnosticar
    print(f"Gemini status: {response.status_code}")
    print(f"Gemini response: {data}")

    if "candidates" in data:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    elif "error" in data:
        error_msg = data["error"].get("message", "Error desconocido")
        print(f"Error de Gemini: {error_msg}")
        return f"Error de Gemini: {error_msg}"
    else:
        return "Respuesta inesperada de Gemini."


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
            "Puedes:\n"
            "- Escribirme en espanol y te enseno como decirlo en ingles\n"
            "- Escribirme en ingles y te corrijo si hay errores\n"
            "- Pedirme ejercicios con /ejercicio\n\n"
            "Por donde quieres empezar?"
        )
        return

    if text == "/reset":
        user_histories[user_id] = []
        send_message(chat_id, "Conversacion reiniciada!")
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
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    print("Servidor web iniciado!")
    print(f"Usando Gemini URL: {GEMINI_URL[:60]}...")

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
