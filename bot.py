"""
bot.py — Bot de Telegram para consultar el precio del oro (24/7 en Render, plan de pago)
─────────────────────────────────────────────────────────────────────────────
Usa Flask + webhook de Telegram para responder comandos, y un scheduler
interno (APScheduler) para mandar el mensaje programado a un chat fijo.

Comandos soportados:
    /start              → mensaje de bienvenida
    /precio              → precio actual (usa tu referencia si la tienes puesta)
    /precio 1250         → fija 1250 como tu referencia y muestra la comparación
    /ref                 → muestra tu referencia actual

Horarios automáticos (hora de Ciudad de México):
    Lunes a viernes: 8:00, 13:00 y 18:00
    Domingo: 18:00

El mensaje programado se manda siempre al mismo chat fijo (CHAT_DESTINO más
abajo) — ya no hay /suscribir ni lista de suscriptores, porque con el estado
en memoria y varios workers eso resultaba frágil. Si en el futuro se necesita
mandar a más de un chat, se puede volver a agregar una lista, pero fija (por
variable de entorno, por ejemplo) en vez de dinámica.

Variables de entorno requeridas:
    TELEGRAM_BOT_TOKEN   → token que te da @BotFather
    CHAT_DESTINO         → id del chat al que se manda el mensaje programado
                            (por defecto -5154895477 si no se define)
"""
import os
import logging
from flask import Flask, request, jsonify
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from precio_oro import obtener_precio_oro, formatear_mensaje

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

ZONA = "America/Mexico_City"

# Chat al que se manda el mensaje programado. Se puede sobreescribir con la
# variable de entorno CHAT_DESTINO en Render sin tocar código.
CHAT_DESTINO = int(os.environ.get("CHAT_DESTINO", "-5154895477"))

app = Flask(__name__)

# Referencia de precio por chat — sigue en memoria (se pierde en cada redeploy)
referencias = {}


# ── Telegram ───────────────────────────────────────────────────────────────
def enviar_mensaje(chat_id, texto):
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"No se pudo enviar mensaje a {chat_id}: {e}")


def enviar_programado():
    """Llamada por el scheduler interno a las horas programadas."""
    log.info(f"[scheduler] Estado actual de referencias en memoria: {referencias} (pid={os.getpid()})")
    try:
        precio = obtener_precio_oro()
    except Exception as e:
        log.exception("[scheduler] Error obteniendo precio")
        return
    ref = referencias.get(CHAT_DESTINO)
    log.info(f"[scheduler] Referencia encontrada para {CHAT_DESTINO}: {ref}")
    enviar_mensaje(CHAT_DESTINO, formatear_mensaje(precio, precio_ref=ref))
    log.info(f"[scheduler] Mensaje programado enviado a {CHAT_DESTINO}.")


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
                "*/ref* — muestra tu referencia actual\n\n"
                "Los mensajes automáticos (8am, 1pm, 6pm lun-vie; 6pm domingo) "
                "se mandan a un chat fijo configurado por el administrador.",
            )
        elif texto.startswith("/precio"):
            partes = texto.split(maxsplit=1)
            if len(partes) > 1:
                try:
                    referencias[chat_id] = float(partes[1].replace(",", ""))
                    log.info(f"[/precio] Referencia guardada para {chat_id}: {referencias} (pid={os.getpid()})")
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


# ── Scheduler interno ─────────────────────────────────────────────────────
scheduler = BackgroundScheduler(timezone=ZONA)
scheduler.add_job(enviar_programado, CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=ZONA))
scheduler.add_job(enviar_programado, CronTrigger(day_of_week="mon-fri", hour=13, minute=0, timezone=ZONA))
scheduler.add_job(enviar_programado, CronTrigger(day_of_week="mon-fri", hour=18, minute=0, timezone=ZONA))
scheduler.add_job(enviar_programado, CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=ZONA))
scheduler.start()
log.info(f"Scheduler interno iniciado. Mensajes programados van al chat {CHAT_DESTINO}.")


if __name__ == "__main__":
    # Solo para pruebas locales (Render usa gunicorn, ver Procfile)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
