"""
bot.py — Bot de Telegram para consultar el precio del oro (24/7 en la nube)
─────────────────────────────────────────────────────────────────────────────
Usa Flask + webhook de Telegram (no "polling"), para que funcione bien en
hosting gratuito tipo Render, donde el proceso puede "dormirse" y despertar
solo cuando llega una petición HTTP (el propio webhook de Telegram lo hace).

Comandos soportados:
    /start            → mensaje de bienvenida
    /precio           → precio actual (usa tu referencia si la tienes puesta)
    /precio 1250      → fija 1250 como tu referencia y muestra la comparación
    /ref              → muestra tu referencia actual

Variables de entorno requeridas:
    TELEGRAM_BOT_TOKEN   → token que te da @BotFather

Nota sobre persistencia: la referencia de precio se guarda en memoria por
chat_id. En el plan gratuito de Render el proceso puede reiniciarse (por
ejemplo tras dormirse por inactividad o al hacer un nuevo deploy), y en ese
caso se perderían las referencias guardadas. Si quieres que sobrevivan
reinicios, el siguiente paso sería moverlas a una base de datos — dilo y lo
agregamos.
"""
import os
import logging
from flask import Flask, request, jsonify
import requests

from precio_oro import obtener_precio_oro, formatear_mensaje

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

app = Flask(__name__)

# Referencia de precio por chat_id (en memoria — ver nota de persistencia arriba)
referencias = {}


def enviar_mensaje(chat_id, texto):
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"No se pudo enviar mensaje a {chat_id}: {e}")


@app.route("/", methods=["GET"])
def home():
    return "Bot de precio del oro activo ✅"


@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(silent=True) or {}
    mensaje = update.get("message") or update.get("edited_message")
    if not mensaje:
        return jsonify(ok=True)

    chat_id = mensaje["chat"]["id"]
    texto = (mensaje.get("text") or "").strip()

    try:
        if texto.startswith("/start"):
            enviar_mensaje(
                chat_id,
                "👋 ¡Hola! Soy tu bot de precio del oro.\n\n"
                "Comandos:\n"
                "*/precio* — precio actual\n"
                "*/precio 1250* — fija tu referencia en 1250 MXN/g y compara\n"
                "*/ref* — muestra tu referencia actual",
            )
        elif texto.startswith("/precio"):
            partes = texto.split(maxsplit=1)
            if len(partes) > 1:
                try:
                    referencias[chat_id] = float(partes[1].replace(",", ""))
                except ValueError:
                    enviar_mensaje(chat_id, "⚠️ Usa: /precio 1250")
                    return jsonify(ok=True)
            precio = obtener_precio_oro()
            ref = referencias.get(chat_id)
            enviar_mensaje(chat_id, formatear_mensaje(precio, precio_ref=ref))
        elif texto.startswith("/ref"):
            ref = referencias.get(chat_id)
            if ref:
                enviar_mensaje(chat_id, f"📌 Tu referencia actual: ${ref:,.2f} MXN/g")
            else:
                enviar_mensaje(chat_id, "No tienes referencia fijada. Usa /precio 1250")
        else:
            enviar_mensaje(chat_id, "No entendí el comando. Usa /precio")
    except Exception as e:
        log.exception("Error procesando update")
        enviar_mensaje(chat_id, f"❌ Error consultando el precio: {e}")

    return jsonify(ok=True)


if __name__ == "__main__":
    # Solo para pruebas locales (Render usa gunicorn, ver Procfile)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
