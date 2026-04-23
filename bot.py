import os
import re
import time
import asyncio
import threading
import tempfile
import requests
import edge_tts
from http.server import HTTPServer, BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Voces naturales y profesionales
VOICE_ES = "es-CO-SalomeNeural"   # Español colombiano, profesional
VOICE_EN = "en-US-JennyNeural"    # Inglés americano, natural y profesional

SYSTEM_PROMPT = """Eres un tutor de inglés amigable, paciente y motivador llamado "Tutor AI". 
Tu misión es ayudar a hispanohablantes a aprender inglés americano de forma práctica y divertida.

REGLAS:
- Siempre responde en ESPAÑOL, pero usa el inglés americano para enseñar.
- Corrige los errores del usuario con amabilidad, sin hacerlos sentir mal.
- Cuando el usuario escriba o hable en inglés, corrígelo si hay errores y explica por qué.
- Usa emojis para hacer las conversaciones más amenas.
- Adapta el nivel de dificultad al usuario (principiante, intermedio o avanzado).
- Si el usuario comete un error, muestra la forma correcta: ❌ Error → ✅ Correcto.
- Celebra los logros del usuario con entusiasmo.
- Mantén las respuestas concisas (máximo 200 palabras).
- Enfócate en inglés americano: vocabulario, expresiones y pronunciación americanas.

Al final de cada corrección, incluye una sección así:
🔊 PRONUNCIA: [escribe aquí SOLO la frase correcta en inglés que el usuario debe practicar]

Ejemplo:
🔊 PRONUNCIA: I want to go to the store

TEMAS QUE PUEDES ENSEÑAR:
- Vocabulario cotidiano americano
- Gramática básica y avanzada
- Frases útiles para viajes, trabajo, etc.
- Pronunciación americana
- Conversación simulada
- Verbos irregulares
- Tiempos verbales
"""

user_histories = {}


# --- Servidor web para Render ---
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


# --- Funciones de Telegram ---
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


def send_voice_file(chat_id, filepath):
    with open(filepath, "rb") as audio:
        requests.post(
            f"{TELEGRAM_BASE}/sendVoice",
            data={"chat_id": chat_id},
            files={"voice": audio}
        )


# --- Text to Speech con Edge TTS ---
async def text_to_speech_async(text, voice, output_path):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def speak(chat_id, text, voice):
    """Genera audio con Edge TTS y lo envía por Telegram."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            output_path = f.name

        asyncio.run(text_to_speech_async(text, voice, output_path))
        send_voice_file(chat_id, output_path)
        os.unlink(output_path)
    except Exception as e:
        print(f"Error en TTS: {e}")


def extract_english_phrase(reply):
    """Extrae la frase en inglés marcada con 🔊 PRONUNCIA: ..."""
    match = re.search(r"🔊 PRONUNCIA:\s*(.+)", reply)
    if match:
        return match.group(1).strip()
    return None


# --- Transcripción con Groq Whisper ---
def transcribe_voice(file_id):
    try:
        file_info = requests.get(f"{TELEGRAM_BASE}/getFile?file_id={file_id}").json()
        file_path = file_info["result"]["file_path"]
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
        audio_data = requests.get(file_url).content

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name

        with open(temp_path, "rb") as audio_file:
            response = requests.post(
                GROQ_WHISPER_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": ("audio.ogg", audio_file, "audio/ogg")},
                data={"model": "whisper-large-v3", "language": "en"}
            )
        os.unlink(temp_path)

        data = response.json()
        print(f"Whisper: {data}")
        return data.get("text", "")
    except Exception as e:
        print(f"Error transcribiendo: {e}")
        return ""


# --- Groq Chat ---
def ask_groq(history):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    response = requests.post(
        GROQ_CHAT_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "max_tokens": 500
        }
    )
    data = response.json()
    if "choices" in data:
        return data["choices"][0]["message"]["content"]
    elif "error" in data:
        return f"Error: {data['error'].get('message', 'Error desconocido')}"
    return "Respuesta inesperada."


# --- Procesador de mensajes ---
def process_message(chat_id, user_id, text, is_voice=False):
    if user_id not in user_histories:
        user_histories[user_id] = []

    history = user_histories[user_id]

    if is_voice:
        content = f"[El usuario envió un mensaje de VOZ en inglés. Transcripción: '{text}']. Corrígelo si hay errores y felicítalo si estuvo bien."
    else:
        content = text

    history.append({"role": "user", "content": content})
    send_typing(chat_id)
    reply = ask_groq(history)
    history.append({"role": "assistant", "content": reply})
    user_histories[user_id] = history[-20:]

    # Enviar respuesta en texto
    send_message(chat_id, reply)

    # Enviar respuesta en español con voz
    reply_clean = re.sub(r"🔊 PRONUNCIA:.*", "", reply).strip()
    if reply_clean:
        speak(chat_id, reply_clean, VOICE_ES)

    # Enviar pronunciación en inglés americano si hay frase para practicar
    english_phrase = extract_english_phrase(reply)
    if english_phrase:
        send_message(chat_id, f"🇺🇸 Así se pronuncia en inglés americano:")
        speak(chat_id, english_phrase, VOICE_EN)


# --- Manejador de updates de Telegram ---
def handle_update(update):
    if "message" not in update:
        return

    message = update["message"]
    chat_id = message["chat"]["id"]
    user_id = str(chat_id)

    # Mensaje de VOZ
    if "voice" in message:
        send_message(chat_id, "🎤 Escuché tu mensaje, analizando...")
        file_id = message["voice"]["file_id"]
        transcription = transcribe_voice(file_id)

        if transcription:
            send_message(chat_id, f"📝 Escuché: _{transcription}_")
            process_message(chat_id, user_id, transcription, is_voice=True)
        else:
            send_message(chat_id, "No pude entender el audio. Intenta de nuevo.")
        return

    # Mensaje de TEXTO
    text = message.get("text", "")
    if not text:
        return

    if text == "/start":
        user_histories[user_id] = []
        send_message(chat_id,
            "Hola! Soy tu Tutor de Ingles Americano AI 🎓🇺🇸\n\n"
            "Puedes:\n"
            "🎤 Enviarme mensajes de VOZ en ingles y te corrijo\n"
            "📝 Escribirme en espanol y te enseno como decirlo\n"
            "✍️ Escribirme en ingles y te corrijo los errores\n"
            "📚 Pedir ejercicios con /ejercicio\n\n"
            "Escucharas la pronunciacion correcta en ingles americano!\n\n"
            "Por donde quieres empezar? 😊"
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
            "/help - Ver esta ayuda\n\n"
            "Tambien puedes enviarme mensajes de VOZ 🎤"
        )
        return

    if text == "/ejercicio":
        text = "Dame un ejercicio corto y practico de ingles americano para practicar ahora."

    process_message(chat_id, user_id, text, is_voice=False)


def main():
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    print("Servidor web iniciado!")
    print("Bot iniciado con Edge TTS!")

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
