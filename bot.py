import os

from gtts import gTTS
import time

import threading
import tempfile
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

VOICE_EN = "en-US-JennyNeural"

SYSTEM_PROMPT = """Eres "Tutor AI", un tutor de inglés americano interactivo, amigable y motivador.
Tu misión es guiar al estudiante en una conversación fluida y progresiva para aprender inglés.

══════════════════════════════════════
FLUJO DE CONVERSACIÓN
══════════════════════════════════════

1. PRIMERA VEZ O SALUDO:
   - Saluda con entusiasmo
   - Detecta su nivel (principiante/intermedio/avanzado) según cómo escribe o habla
   - Pregúntale qué tema quiere practicar hoy
   - Ejemplos de temas: viajes, trabajo, familia, comida, películas, rutina diaria, negocios, etc.

2. CUANDO EL USUARIO ENVÍA UNA FRASE O PALABRA:
   - PRIMERO corrige si hay errores: ❌ Error → ✅ Correcto
   - LUEGO continúa la conversación de forma natural sobre el tema que están practicando
   - Haz UNA pregunta o propón UN reto relacionado para que siga hablando en inglés
   - Introduce vocabulario o expresiones nuevas de forma natural en la conversación
   - Nunca termines una respuesta sin invitarlo a continuar practicando

3. ADAPTACIÓN AL NIVEL:
   - Principiante: frases cortas, vocabulario básico, mucho apoyo y celebración
   - Intermedio: frases más complejas, phrasal verbs, expresiones coloquiales americanas
   - Avanzado: modismos, matices culturales, fluidez y naturalidad

4. MEMORIA DE SESIÓN:
   - Recuerda el tema que están practicando y continúa desde donde quedaron
   - Recuerda el nivel detectado y ajusta la dificultad progresivamente
   - Si el usuario mejora, felicítalo y sube un poco el nivel

══════════════════════════════════════
REGLAS DE FORMATO
══════════════════════════════════════
- Responde SIEMPRE en ESPAÑOL, pero usa inglés americano para enseñar
- Usa emojis con moderación para hacer la conversación amena
- Mantén respuestas concisas (máximo 150 palabras)
- Si hay corrección, ponla al inicio claramente
- Siempre termina con una pregunta o invitación a practicar más
- Sé cálido, paciente y muy motivador — celebra cada avance del estudiante

══════════════════════════════════════
EJEMPLO DE RESPUESTA IDEAL
══════════════════════════════════════
Usuario dice: "I go to the store yesterday"
Respuesta:
"✅ ¡Casi perfecto! Solo un ajuste:
❌ I go → ✅ I went (pasado de 'go')

¡Muy bien que estás usando el pasado! 🎉 Ya que estamos practicando compras, cuéntame: 
👉 What did you buy at the store? (¿Qué compraste en la tienda?)

Intenta responder con al menos 2 cosas que compraste. ¡Tú puedes! 💪"
"""

user_histories = {}   # historial de mensajes
user_state = {}       # nivel y tema por usuario


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
    requests.post(f"{TELEGRAM_BASE}/sendMessage", json={"chat_id": chat_id, "text": text})


def send_typing(chat_id):
    requests.post(f"{TELEGRAM_BASE}/sendChatAction", json={"chat_id": chat_id, "action": "typing"})


def send_voice_file(chat_id, filepath):
    with open(filepath, "rb") as audio:
        resp = requests.post(
            f"{TELEGRAM_BASE}/sendVoice",
            data={"chat_id": chat_id},
            files={"voice": audio}
        )
        print(f"sendVoice: {resp.status_code} {resp.text[:200]}")


def speak_english(chat_id, text):
    """Genera audio en inglés usando gTTS."""
    try:
        print(f"TTS para: {text}")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            output_path = f.name

        tts = gTTS(text=text, lang="en", tld="us", slow=False)
        tts.save(output_path)

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            send_voice_file(chat_id, output_path)
            print("Audio enviado correctamente")
        else:
            print("Error: archivo de audio vacío")

        if os.path.exists(output_path):
            os.unlink(output_path)
    except Exception as e:
        print(f"Error TTS: {e}")


def get_english_phrase(spanish_reply, user_input):
    prompt = f"""Student input: "{user_input}"
Tutor Spanish response: "{spanish_reply}"
Write ONLY the correct English sentence to practice. Nothing else. Just English."""

    response = requests.post(
        GROQ_CHAT_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 80}
    )
    data = response.json()
    if "choices" in data:
        phrase = data["choices"][0]["message"]["content"].strip()
        print(f"Frase inglés: {phrase}")
        return phrase
    return None


def ask_groq(history):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    response = requests.post(
        GROQ_CHAT_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "messages": messages, "max_tokens": 500}
    )
    data = response.json()
    if "choices" in data:
        return data["choices"][0]["message"]["content"]
    return "Error al obtener respuesta."


def transcribe_voice(file_id):
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
        return response.json().get("text", "")
    except Exception as e:
        print(f"Error transcribiendo: {e}")
        return ""


def process_message(chat_id, user_id, user_text, is_voice=False):
    if user_id not in user_histories:
        user_histories[user_id] = []
    if user_id not in user_state:
        user_state[user_id] = {"level": "desconocido", "topic": "ninguno", "turns": 0}

    state = user_state[user_id]
    state["turns"] += 1

    history = user_histories[user_id]

    # Incluir contexto del estado en el mensaje del sistema dinámico
    state_context = f"[Estado del estudiante — Nivel detectado: {state['level']} | Tema actual: {state['topic']} | Turnos completados: {state['turns']}]"

    if is_voice:
        content = f"{state_context} [MENSAJE DE VOZ transcrito: '{user_text}']. Corrígelo si hay errores y continúa la conversación."
    else:
        content = f"{state_context} {user_text}"

    history.append({"role": "user", "content": content})

    send_typing(chat_id)
    reply = ask_groq(history)
    history.append({"role": "assistant", "content": reply})
    user_histories[user_id] = history[-20:]

    # Actualizar nivel y tema detectados por el modelo (heurística simple)
    if "principiante" in reply.lower():
        state["level"] = "principiante"
    elif "intermedio" in reply.lower():
        state["level"] = "intermedio"
    elif "avanzado" in reply.lower():
        state["level"] = "avanzado"

    send_message(chat_id, reply)

    english_phrase = get_english_phrase(reply, user_text)
    if english_phrase:
        send_message(chat_id, "🇺🇸 Pronunciación en inglés americano:")
        speak_english(chat_id, english_phrase)


def handle_update(update):
    if "message" not in update:
        return
    message = update["message"]
    chat_id = message["chat"]["id"]
    user_id = str(chat_id)

    if "voice" in message:
        send_message(chat_id, "🎤 Analizando tu pronunciación...")
        transcription = transcribe_voice(message["voice"]["file_id"])
        if transcription:
            send_message(chat_id, f"📝 Escuché: {transcription}")
            process_message(chat_id, user_id, transcription, is_voice=True)
        else:
            send_message(chat_id, "No pude entender el audio. Intenta de nuevo.")
        return

    text = message.get("text", "")
    if not text:
        return

    if text == "/start":
        user_histories[user_id] = []
        send_message(chat_id,
            "Hola! Soy tu Tutor de Ingles Americano AI 🎓🇺🇸\n\n"
            "🎤 Voz en ingles → corrección + audio en ingles americano\n"
            "📝 Espanol → te enseno como decirlo\n"
            "✍️ Ingles escrito → corrijo errores\n"
            "📚 /ejercicio → practica guiada\n\n"
            "Por donde quieres empezar? 😊"
        )
        return
    if text == "/reset":
        user_histories[user_id] = []
        user_state[user_id] = {"level": "desconocido", "topic": "ninguno", "turns": 0}
        send_message(chat_id, "Conversacion reiniciada! Empecemos de nuevo. De que tema quieres practicar hoy? 😊")
        return
    if text == "/help":
        send_message(chat_id, "/start /reset /ejercicio /help")
        return
    if text == "/ejercicio":
        text = "Dame un ejercicio corto de ingles americano."

    process_message(chat_id, user_id, text, is_voice=False)


def get_start_offset():
    """Al iniciar, saltarse mensajes viejos para evitar respuestas duplicadas."""
    try:
        resp = requests.get(f"{TELEGRAM_BASE}/getUpdates", params={"offset": -1, "timeout": 5}, timeout=10)
        results = resp.json().get("result", [])
        if results:
            latest_id = results[-1]["update_id"]
            print(f"Saltando mensajes viejos, iniciando desde offset {latest_id + 1}")
            return latest_id + 1
    except Exception as e:
        print(f"Error obteniendo offset inicial: {e}")
    return 0


def main():
    threading.Thread(target=run_server, daemon=True).start()
    print("Bot iniciado!")

    # Saltar mensajes viejos al reiniciar
    offset = get_start_offset()

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
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
