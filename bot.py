"""
bot.py — Bot de Telegram para consultar el precio del oro (24/7 en Render, plan de pago)
─────────────────────────────────────────────────────────────────────────────
Usa Flask + webhook de Telegram para responder comandos, y un scheduler
interno (APScheduler) para mandar mensajes automáticos a horas fijas.

Esto es válido porque el servicio corre en un plan de PAGO de Render, que no
se duerme por inactividad — si estuviera en el plan gratis, el proceso se
detendría y el scheduler interno dejaría de dispararse (por eso antes
usábamos un cron externo). Ver nota de simplificación abajo.

Comandos soportados:
    /start              → mensaje de bienvenida
    /precio              → precio actual (usa tu referencia si la tienes puesta)
    /precio 1250         → fija 1250 como tu referencia y muestra la comparación
    /ref                 → muestra tu referencia actual
    /suscribir           → te agrega a la lista de mensajes automáticos programados
    /desuscribir         → te quita de esa lista

Horarios automáticos (hora de Ciudad de México):
    Lunes a viernes: 8:00, 13:00 y 18:00
    Domingo: 18:00

Variables de entorno requeridas:
    TELEGRAM_BOT_TOKEN   → token que te da @BotFather

NOTA — simplificación a propósito (decisión tomada con el usuario):
    Los suscriptores y las referencias de precio se guardan en memoria
    (diccionarios normales), NO en una base externa. Esto es más simple,
    pero significa que un redeploy o un reinicio manual del servicio en
    Render borra esas listas — cada quien tendría que volver a escribir
    /suscribir y /precio <valor>. Se aceptó ese trade-off para no depender
    de Upstash. Si más adelante se quiere que sobreviva a un redeploy, se
    puede agregar de nuevo una base como Upstash Redis.
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

app = Flask(__name__)

# Estado en memoria — ver nota de simplificación arriba
suscriptores = set()
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


def enviar_a_suscriptores():
    """Llamada por el scheduler interno a las horas programadas."""
    if not suscriptores:
        log.info("[scheduler] Sin suscriptores, no se manda nada.")
        return
    try:
        precio = obtener_precio_oro()
    except Exception as e:
        log.exception("[scheduler] Error obteniendo precio")
        return
    for chat_id in list(suscriptores):
        ref = referencias.get(chat_id)
        enviar_mensaje(chat_id, formatear_mensaje(precio, precio_ref=ref))
    log.info(f"[scheduler] Mensaje programado enviado a {len(suscriptores)} suscriptor(es).")


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
                "*/ref* — muestra tu referencia actual\n"
                "*/suscribir* — recibe mensajes automáticos (8am, 1pm, 6pm lun-vie; 6pm domingo)\n"
                "*/desuscribir* — deja de recibirlos",
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
        elif texto.startswith("/suscribir"):
            suscriptores.add(chat_id)
            enviar_mensaje(
                chat_id,
                "✅ Quedaste suscrito. Recibirás el precio automáticamente: "
                "8am, 1pm y 6pm de lunes a viernes, y 6pm el domingo. "
                "Usa /desuscribir para salir.",
            )
        elif texto.startswith("/desuscribir"):
            suscriptores.discard(chat_id)
            enviar_mensaje(chat_id, "❌ Ya no recibirás mensajes automáticos.")
        else:
            enviar_mensaje(chat_id, "No entendí el comando. Usa /precio")
    except Exception as e:
        log.exception("Error procesando update")
        enviar_mensaje(chat_id, f"❌ Error consultando el precio: {e}")

    return jsonify(ok=True)


# ── Scheduler interno ─────────────────────────────────────────────────────
scheduler = BackgroundScheduler(timezone=ZONA)
scheduler.add_job(enviar_a_suscriptores, CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=ZONA))
scheduler.add_job(enviar_a_suscriptores, CronTrigger(day_of_week="mon-fri", hour=13, minute=0, timezone=ZONA))
scheduler.add_job(enviar_a_suscriptores, CronTrigger(day_of_week="mon-fri", hour=18, minute=0, timezone=ZONA))
scheduler.add_job(enviar_a_suscriptores, CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=ZONA))
scheduler.start()
log.info("Scheduler interno iniciado (8am/1pm/6pm lun-vie, 6pm domingo, hora CDMX).")


if __name__ == "__main__":
    # Solo para pruebas locales (Render usa gunicorn, ver Procfile)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
