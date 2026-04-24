import os
import re
import time
import threading
import tempfile
import requests
from gtts import gTTS
from http.server import HTTPServer, BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

SYSTEM_PROMPT = """Eres "Tutor AI", un tutor de ingles americano interactivo, amigable y motivador.
Tu mision es guiar al estudiante en una conversacion fluida y progresiva para aprender ingles.

FLUJO DE CONVERSACION:

1. PRIMERA VEZ O SALUDO:
   - Saluda con entusiasmo
   - Detecta su nivel segun como escribe o habla
   - Preguntale que tema quiere practicar hoy
   - Temas posibles: viajes, trabajo, familia, comida, peliculas, rutina diaria, negocios, etc.

2. CUANDO EL USUARIO ENVIA UNA FRASE:
   - PRIMERO corrige si hay errores: Error -> Correcto
   - LUEGO continua la conversacion de forma natural sobre el tema
   - Haz UNA pregunta para que siga hablando en ingles
   - Introduce vocabulario nuevo de forma natural
   - Nunca termines sin invitarlo a continuar practicando

3. NIVEL:
   - Principiante: frases cortas, vocabulario basico, mucho apoyo
   - Intermedio: frases complejas, phrasal verbs, expresiones coloquiales
   - Avanzado: modismos, matices culturales, fluidez

REGLAS:
- Responde SIEMPRE en ESPANOL, usa ingles para ensenar
- Usa emojis con moderacion
- Maximo 150 palabras por respuesta
- Siempre termina con una pregunta o reto en ingles
- Se calido, paciente y motivador
"""

user_histories = {}
user_state = {}


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot activo!")

    def log_message(self, format, *args):
        pass


def run_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


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
        resp = requests.post(
            f"{TELEGRAM_BASE}/sendVoice",
            data={"chat_id": chat_id},
            files={"voice": audio}
        )
        print(f"sendVoice: {resp.status_code}")


def clean_for_tts(text):
    """Elimina emojis y caracteres especiales para gTTS."""
    text = re.sub(r"[^\x00-\x7F]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def speak_english(chat_id, text):
    """Genera audio en ingles americano con gTTS y lo envia."""
    try:
        clean = clean_for_tts(text)
        print(f"TTS texto: {clean}")

        if not clean:
            print("Texto vacio, omitiendo audio")
            return

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            output_path = f.name

        tts = gTTS(text=clean, lang="en", tld="us", slow=False)
        tts.save(output_path)

        size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        print(f"Audio generado: {size} bytes")

        if size > 0:
            send_voice_file(chat_id, output_path)
            print("Audio enviado")
        else:
            print("Archivo vacio, no se envia")

        if os.path.exists(output_path):
            os.unlink(output_path)

    except Exception as e:
        print(f"Error speak_english: {e}")


def groq_chat(messages, max_tokens=500):
    """Llama a la API de Groq."""
    try:
        response = requests.post(
            GROQ_CHAT_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": messages, "max_tokens": max_tokens},
            timeout=20
        )
        data = response.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"].strip()
        print(f"Groq error: {data}")
        return None
    except Exception as e:
        print(f"Error groq_chat: {e}")
        return None


def get_english_audio(spanish_reply, user_input):
    """Obtiene version en ingles de la respuesta del tutor para el audio."""
    prompt = f"""Student said: "{user_input}"
Tutor replied in Spanish: "{spanish_reply}"

Translate the tutor reply into natural American English. Max 3 sentences.
Include the correction if any, and the follow-up question.
Write ONLY plain English. No emojis. No symbols. No Spanish."""

    result = groq_chat([{"role": "user", "content": prompt}], max_tokens=120)
    print(f"English audio text: {result}")
    return result


def transcribe_voice(file_id):
    """Transcribe un audio con Groq Whisper."""
    try:
        file_info = requests.get(f"{TELEGRAM_BASE}/getFile?file_id={file_id}").json()
        file_path = file_info["result"]["file_path"]
        audio_data = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}").content

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
        result = response.json().get("text", "")
        print(f"Whisper: {result}")
        return result
    except Exception as e:
        print(f"Error transcribe: {e}")
        return ""


def process_message(chat_id, user_id, user_text, is_voice=False):
    if user_id not in user_histories:
        user_histories[user_id] = []
    if user_id not in user_state:
        user_state[user_id] = {"level": "desconocido", "topic": "ninguno", "turns": 0}

    state = user_state[user_id]
    state["turns"] += 1

    history = user_histories[user_id]
    state_ctx = f"[Nivel: {state['level']} | Tema: {state['topic']} | Turno: {state['turns']}]"

    if is_voice:
        content = f"{state_ctx} [VOZ: '{user_text}']. Corrige si hay errores y continua la conversacion."
    else:
        content = f"{state_ctx} {user_text}"

    history.append({"role": "user", "content": content})

    send_typing(chat_id)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    reply = groq_chat(messages, max_tokens=500)

    if not reply:
        send_message(chat_id, "Hubo un error. Intenta de nuevo.")
        return

    history.append({"role": "assistant", "content": reply})
    user_histories[user_id] = history[-20:]

    # Actualizar nivel detectado
    for level in ["principiante", "intermedio", "avanzado"]:
        if level in reply.lower():
            state["level"] = level

    # Enviar respuesta en texto (espanol)
    send_message(chat_id, reply)

    # Generar y enviar audio en ingles
    english_text = get_english_audio(reply, user_text)
    if english_text:
        send_message(chat_id, "Tutor en ingles americano:")
        speak_english(chat_id, english_text)
    else:
        print("No se obtuvo texto en ingles para el audio")


def handle_update(update):
    if "message" not in update:
        return

    message = update["message"]
    chat_id = message["chat"]["id"]
    user_id = str(chat_id)

    # Voz
    if "voice" in message:
        send_message(chat_id, "Analizando tu pronunciacion...")
        transcription = transcribe_voice(message["voice"]["file_id"])
        if transcription:
            send_message(chat_id, f"Escuche: {transcription}")
            process_message(chat_id, user_id, transcription, is_voice=True)
        else:
            send_message(chat_id, "No pude entender el audio. Intenta de nuevo.")
        return

    # Texto
    text = message.get("text", "")
    if not text:
        return

    if text == "/start":
        user_histories[user_id] = []
        user_state[user_id] = {"level": "desconocido", "topic": "ninguno", "turns": 0}
        send_message(chat_id,
            "Hola! Soy tu Tutor de Ingles Americano AI\n\n"
            "Puedes:\n"
            "- Enviarme mensajes de VOZ en ingles y te corrijo\n"
            "- Escribirme en espanol y te enseno como decirlo\n"
            "- Escribirme en ingles y te corrijo los errores\n"
            "- /ejercicio para practica guiada\n\n"
            "De que tema quieres practicar hoy?"
        )
        return

    if text == "/reset":
        user_histories[user_id] = []
        user_state[user_id] = {"level": "desconocido", "topic": "ninguno", "turns": 0}
        send_message(chat_id, "Conversacion reiniciada! De que tema quieres practicar hoy?")
        return

    if text == "/help":
        send_message(chat_id, "/start - Iniciar\n/reset - Reiniciar\n/ejercicio - Ejercicio\n/help - Ayuda")
        return

    if text == "/ejercicio":
        text = "Dame un ejercicio corto de ingles americano para practicar ahora."

    process_message(chat_id, user_id, text, is_voice=False)


def get_start_offset():
    """Saltar mensajes viejos al reiniciar."""
    try:
        resp = requests.get(f"{TELEGRAM_BASE}/getUpdates", params={"offset": -1, "timeout": 5}, timeout=10)
        results = resp.json().get("result", [])
        if results:
            return results[-1]["update_id"] + 1
    except Exception as e:
        print(f"Error offset: {e}")
    return 0


def keepalive():
    """Hace ping a la URL publica de Render cada 5 minutos para evitar congelamiento."""
    # Render provee esta variable automaticamente con la URL publica del servicio
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if not render_url:
        print("RENDER_EXTERNAL_URL no definida, keepalive desactivado")
        return
    while True:
        try:
            time.sleep(270)  # cada 4.5 minutos (Render congela a los 5 min)
            resp = requests.get(render_url, timeout=10)
            print(f"Keepalive ping OK: {resp.status_code}")
        except Exception as e:
            print(f"Keepalive error: {e}")


def main():
    threading.Thread(target=run_server, daemon=True).start()
    threading.Thread(target=keepalive, daemon=True).start()
    print("Bot iniciado con keepalive!")

    offset = get_start_offset()
    print(f"Offset inicial: {offset}")

    while True:
        try:
            response = requests.get(
                f"{TELEGRAM_BASE}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35
            )
            for update in response.json().get("result", []):
                offset = update["update_id"] + 1
                handle_update(update)
        except requests.exceptions.Timeout:
            print("Timeout en getUpdates, reintentando...")
        except requests.exceptions.ConnectionError:
            print("Error de conexion, esperando 10s...")
            time.sleep(10)
        except Exception as e:
            print(f"Error loop: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
