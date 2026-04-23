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

VOICE_EN = "en-US-JennyNeural"

SYSTEM_PROMPT = """Eres un tutor de inglés amigable, paciente y motivador llamado "Tutor AI". 
Tu misión es ayudar a hispanohablantes a aprender inglés americano.

REGLAS:
- Siempre responde en ESPAÑOL, pero usa el inglés americano para enseñar.
- Corrige los errores del usuario con amabilidad.
- Usa emojis para hacer las conversaciones más amenas.
- Adapta el nivel al usuario (principiante, intermedio o avanzado).
- Si hay error: ❌ Error → ✅ Correcto.
- Mantén las respuestas concisas (máximo 200 palabras).
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


def send_voice_file(chat_id, filepath):
    with open(filepath, "rb") as audio:
        requests.post(
            f"{TELEGRAM_BASE}/sendVoice",
            data={"chat_id": chat_id},
            files={"voice": audio}
        )


async def tts_async(text, voice, output_path):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def speak_english(chat_id, text):
    """Genera y envía audio en inglés americano."""
    try:
        print(f"Generando audio para: {text}")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            output_path = f.name
        asyncio.run(tts_async(text, VOICE_EN, output_path))
        send_voice_file(chat_id, output_path)
        os.unlink(output_path)
        print("Audio enviado exitosamente")
    except Exception as e:
        print(f"Error en TTS: {e}")


def get_english_phrase(spanish_reply, user_input):
    """Llama a Groq para obtener SOLO la frase en inglés que el usuario debe practicar."""
    prompt = f"""A student said or wrote: "{user_input}"

The tutor responded in Spanish: "{spanish_reply}"

Based on this, write ONLY the English sentence or phrase the student should practice or that was corrected. 
Write ONLY the English text, nothing else. No explanations, no Spanish, no punctuation before it.
Maximum 2 sentences in English."""

    response = requests.post(
        GROQ_CHAT_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 100
        }
    )
    data = response.json()
    if "choices" in data:
        phrase = data["choices"][0]["message"]["content"].strip()
        print(f"Frase en inglés para audio: {phrase}")
        return phrase
    return None


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


def process_message(chat_id, user_id, user_text, is_voice=False):
    if user_id not in user_histories:
        user_histories[user_id] = []

    history = user_histories[user_id]

    if is_voice:
        content = f"[Mensaje de VOZ del usuario en inglés. Transcripción: '{user_text}']. Corrígelo si hay errores, felicítalo si estuvo bien."
    else:
        content = user_text

    history.append({"role": "user", "content": content})
    send_typing(chat_id)

    # 1. Obtener respuesta en español
    reply = ask_groq(history)
    history.append({"role": "assistant", "content": reply})
    user_histories[user_id] = history[-20:]

    # 2. Enviar texto en español
    send_message(chat_id, reply)

    # 3. Obtener frase en inglés y enviar audio
    english_phrase = get_english_phrase(reply, user_text)
    if english_phrase:
        send_message(chat_id, "🇺🇸 Escucha la pronunciación en inglés americano:")
        speak_english(chat_id, english_phrase)


def handle_update(update):
    if "message" not in update:
        return

    message = update["message"]
    chat_id = message["chat"]["id"]
    user_id = str(chat_id)

    # Mensaje de VOZ
    if "voice" in message:
        send_message(chat_id, "🎤 Analizando tu pronunciación...")
        file_id = message["voice"]["file_id"]
        transcription = transcribe_voice(file_id)

        if transcription:
            send_message(chat_id, f"📝 Escuché: {transcription}")
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
            "Siempre escucharas la pronunciacion correcta en ingles americano! 🔊\n\n"
            "Por donde quieres empezar? 😊"
        )
        return

    if text == "/reset":
        user_histories[user_id] = []
        send_message(chat_id, "Conversacion reiniciada!")
        return

    if text == "/help":
        send_message(chat_id,
            "Comandos:\n"
            "/start - Iniciar\n"
            "/reset - Borrar historial\n"
            "/ejercicio - Recibir ejercicio\n"
            "/help - Ver ayuda\n\n"
            "Tambien puedes enviar mensajes de VOZ 🎤"
        )
        return

    if text == "/ejercicio":
        text = "Dame un ejercicio corto de ingles americano para practicar ahora."

    process_message(chat_id, user_id, text, is_voice=False)


def main():
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    print("Bot iniciado con audio en ingles americano!")

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
