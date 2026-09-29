"""
precio_oro.py — Módulo refinado para obtener el precio del oro
─────────────────────────────────────────────────────────────────────────────
Precio del oro — cascada de 2 fuentes:
  1. Kitco /price/precious-metals  (JSON embebido __NEXT_DATA__ — muy preciso)
  2. gold-api.com                  (JSON público, sin API key)
Tipo de cambio USD→MXN — cascada de 3 fuentes, de más a menos frecuente:
  1. Google Finance (scraping — el mercado de divisas cotiza continuamente)
  2. Frankfurter    (API del BCE — sin API key, publica una vez al día)
  3. exchangerate-api (último fallback — actualización diaria)
Uso:
    from precio_oro import obtener_precio_oro, formatear_mensaje
    precio = obtener_precio_oro()
    print(formatear_mensaje(precio))
"""
import re
import json
import logging
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ZONA_CDMX = ZoneInfo("America/Mexico_City")

GRAMOS_POR_OZ = 31.1035  # 1 oz troy exacta

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ── FUENTE 1: Kitco /price/precious-metals ────────────────────────────────────
def _kitco_precio_pagina() -> float:
    """
    Scrapea https://www.kitco.com/price/precious-metals

    Kitco ahora es una app Next.js: los precios NO están sueltos en el HTML,
    sino embebidos como JSON dentro de un <script id="__NEXT_DATA__">.
    Ahí viene props.pageProps.dehydratedState.queries[*].state.data.gold.results[0]
    con campos "bid", "ask", "mid", etc. Por eso extraemos ese bloque y lo
    parseamos como JSON en vez de buscar números sueltos con regex (que es
    lo que fallaba: los patrones "href=...precious-metals/gold" y
    "world spot price" ya no existen en el HTML actual).
    """
    url = "https://www.kitco.com/price/precious-metals"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    html = resp.text

    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    if not match:
        raise ValueError("No se encontró el bloque __NEXT_DATA__ en Kitco.")

    data = json.loads(match.group(1))
    try:
        queries = data["props"]["pageProps"]["dehydratedState"]["queries"]
        gold_result = None
        for q in queries:
            gold_data = q.get("state", {}).get("data", {}).get("gold")
            if gold_data and gold_data.get("results"):
                gold_result = gold_data["results"][0]
                break
        if gold_result is None:
            raise ValueError("No se encontró información de oro en el JSON de Kitco.")
        precio = float(gold_result.get("bid") or gold_result.get("mid"))
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"Estructura inesperada en el JSON de Kitco: {e}")

    if 1_500 < precio < 8_000:
        log.info(f"[Kitco] Precio encontrado: ${precio} USD/oz")
        return precio
    raise ValueError(f"Precio de Kitco fuera de rango: {precio}")


# ── FUENTE 2: gold-api.com (JSON público, sin API key) ────────────────────────
def _gold_api_precio() -> float:
    """
    metals.live (la fuente 2 original) dio de baja su endpoint público
    (api.metals.live ya ni siquiera resuelve TLS correctamente). Se
    reemplaza por gold-api.com, que sí funciona sin API key.
    """
    resp = requests.get("https://api.gold-api.com/price/XAU", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    precio = float(data["price"])
    if 1_500 < precio < 8_000:
        log.info(f"[gold-api.com] Precio: ${precio} USD/oz")
        return precio
    raise ValueError(f"Precio fuera de rango: {precio}")


# ── TIPO DE CAMBIO USD → MXN ──────────────────────────────────────────────────
def _tc_google_finance() -> float:
    """
    Scrapea https://www.google.com/finance/quote/USD-MXN

    A diferencia de Frankfurter/ExchangeRate-API (que publican un tipo de
    cambio de referencia una vez al día), el mercado de divisas cotiza
    continuamente entre semana (24/5), y Google Finance refleja eso — es la
    fuente más "oportuna" de las disponibles sin necesitar una API key.

    La página no pinta el precio en HTML estático (no hay un <div> simple
    con la clase de siempre) — Google la inyecta vía varios bloques
    <script>AF_initDataCallback({key: 'ds:2', ..., data: [...ver abajo...]})</script>
    con los datos crudos de la cotización. Dentro de esos bloques aparece
    un array con la forma:
        ["/g/xxxx", null, "USD / MXN", 3, null,
         [dayHigh, cambio, cambioPct, 4, 4, 2], null, PRECIO, null, ...]
    Extraemos ese PRECIO (el número justo después del segundo "null" que
    sigue al arreglo de 6 números). Si Google cambia esta estructura, cae al
    plan B: buscar un número en rango razonable cerca del texto "USD / MXN".
    """
    url = "https://www.google.com/finance/quote/USD-MXN"
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    html = resp.text

    match = re.search(
        r'"USD / MXN"\s*,\s*\d+\s*,\s*null\s*,\s*\[[^\]]*\]\s*,\s*null\s*,\s*([\d.]+)',
        html,
    )
    if match:
        tc = float(match.group(1))
        if 10 < tc < 40:
            log.info(f"[Google Finance] Tipo de cambio: ${tc:.4f} MXN/USD")
            return tc

    pos = html.find("USD / MXN")
    if pos != -1:
        fragmento = html[pos: pos + 500]
        numeros = re.findall(r'([\d]{1,3}\.\d{3,7})', fragmento)
        for n in numeros:
            val = float(n)
            if 10 < val < 40:
                log.info(f"[Google Finance fallback] Tipo de cambio: ${val:.4f} MXN/USD")
                return val

    raise ValueError("No se encontró el tipo de cambio en Google Finance.")


def _tc_frankfurter() -> float:
    """
    Frankfurter API — tipo de referencia del BCE, publicado una vez al día
    en días hábiles (a pesar del nombre "rate", no es continuo).
    """
    resp = requests.get(
        "https://api.frankfurter.dev/v2/rate/USD/MXN", timeout=10
    )
    resp.raise_for_status()
    tc = float(resp.json()["rate"])
    if 10 < tc < 40:
        log.info(f"[Frankfurter] Tipo de cambio: ${tc:.4f} MXN/USD")
        return tc
    raise ValueError(f"Tipo de cambio fuera de rango: {tc}")


def _tc_exchangerate_api() -> float:
    """Último fallback — actualización diaria, siempre disponible."""
    resp = requests.get(
        "https://api.exchangerate-api.com/v4/latest/USD", timeout=10
    )
    resp.raise_for_status()
    tc = float(resp.json()["rates"]["MXN"])
    log.info(f"[ExchangeRate-API] Tipo de cambio: ${tc:.4f} MXN/USD (actualización diaria)")
    return tc


def _tipo_cambio_mxn() -> tuple[float, str]:
    """
    Intenta las fuentes en orden de qué tan seguido se actualizan.
    Devuelve (tipo_cambio, nombre_fuente).
    """
    fuentes = [
        ("Google Finance",              _tc_google_finance),
        ("Frankfurter/BCE (diario)",    _tc_frankfurter),
        ("ExchangeRate-API (diario)",   _tc_exchangerate_api),
    ]
    for nombre, fn in fuentes:
        try:
            tc = fn()
            return tc, nombre
        except Exception as e:
            log.warning(f"[TC] {nombre} falló: {e}")
    log.warning("Todas las fuentes de tipo de cambio fallaron, usando 17.50")
    return 17.50, "aproximación"


# ── FUNCIÓN PRINCIPAL ─────────────────────────────────────────────────────────
def obtener_precio_oro() -> dict:
    """
    Devuelve:
        usd_oz       — precio en USD por onza troy
        mxn_gramo    — precio en MXN por gramo
        mxn_oz       — precio en MXN por onza
        tipo_cambio  — USD/MXN usado
        fuente       — de dónde vino el precio
        timestamp    — datetime del momento de consulta
    """
    precio_usd = None
    fuente = None
    for nombre, fn in [("Kitco", _kitco_precio_pagina), ("gold-api.com", _gold_api_precio)]:
        try:
            precio_usd = fn()
            fuente = nombre
            break
        except Exception as e:
            log.warning(f"[{nombre}] falló: {e}")

    if precio_usd is None:
        raise RuntimeError(
            "No se pudo obtener el precio del oro. "
            "Verifica tu conexión a internet."
        )

    tc, tc_fuente = _tipo_cambio_mxn()
    mxn_oz    = precio_usd * tc
    mxn_gramo = mxn_oz / GRAMOS_POR_OZ

    return {
        "usd_oz":      round(precio_usd, 2),
        "mxn_oz":      round(mxn_oz, 2),
        "mxn_gramo":   round(mxn_gramo, 2),
        "tipo_cambio": round(tc, 4),
        "fuente":      fuente,
        "tc_fuente":   tc_fuente,
        "timestamp":   datetime.now(ZONA_CDMX),
    }


# ── FORMATEAR MENSAJE (WhatsApp / Telegram) ───────────────────────────────────
DIAS  = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
MESES = ["enero","febrero","marzo","abril","mayo","junio",
         "julio","agosto","septiembre","octubre","noviembre","diciembre"]


def formatear_mensaje(precio: dict, precio_ref: float | None = None) -> str:
    ts    = precio["timestamp"]
    dia   = DIAS[ts.weekday()].capitalize()
    mes   = MESES[ts.month - 1]
    fecha = f"{dia}, {ts.day} de {mes} de {ts.year}"
    hora  = ts.strftime("%H:%M")

    if precio_ref:
        diff  = precio["mxn_gramo"] - precio_ref
        pct   = (diff / precio_ref) * 100
        signo = "+" if diff >= 0 else ""
        emoji = "📈" if diff > 0 else ("📉" if diff < 0 else "➡️")
        estado = "POR ENCIMA" if diff > 0 else ("POR DEBAJO" if diff < 0 else "igual a")
        comparacion = (
            f"\n\n📌 *Comparación con tu referencia:*\n"
            f"Referencia fija: ${precio_ref:,.2f} MXN/g\n"
            f"Diferencia: {signo}${diff:,.2f} MXN/g ({signo}{pct:.2f}%)\n"
            f"{emoji} El mercado está *{estado}* de tu referencia"
        )
    else:
        comparacion = (
            "\n\n_ℹ️ No tienes precio de referencia fijado._\n"
            "_Usa_ *!precio 1250* _(o /precio 1250 en Telegram)._"
        )

    return (
        f"🥇 *PRECIO DEL ORO — Kitco*\n"
        f"{fecha}, {hora} (CDMX)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💵 *USD/oz:* ${precio['usd_oz']:,.2f} USD\n"
        f"💱 *Tipo de cambio:* ${precio['tipo_cambio']:.4f} MXN/USD"
        f"  _({precio.get('tc_fuente', '')} )_\n"
        f"🇲🇽 *MXN/gramo:* ${precio['mxn_gramo']:,.2f} MXN\n"
        f"🇲🇽 *MXN/onza:*  ${precio['mxn_oz']:,.2f} MXN"
        f"{comparacion}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"_Fuente: {precio['fuente']}_"
    )


# ── TEST RÁPIDO ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print("Consultando precio del oro...\n")
    p = obtener_precio_oro()
    print(f"USD/oz:      ${p['usd_oz']:,.2f}")
    print(f"Tipo cambio: ${p['tipo_cambio']:.4f} MXN/USD")
    print(f"MXN/gramo:   ${p['mxn_gramo']:,.2f}")
    print(f"MXN/onza:    ${p['mxn_oz']:,.2f}")
    print(f"Fuente:      {p['fuente']}")
    print(f"\nMensaje formateado:\n")
    print(formatear_mensaje(p, precio_ref=2500.00))
