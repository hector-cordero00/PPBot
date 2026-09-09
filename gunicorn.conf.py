"""
gunicorn.conf.py — gunicorn carga este archivo automáticamente si está en el
mismo directorio desde donde se ejecuta (no hace falta referenciarlo en el
Start Command).

Arranca el scheduler interno DESPUÉS de que gunicorn hace fork del worker,
para que viva en el mismo proceso que atiende las peticiones de Flask (y por
lo tanto comparta la misma memoria de `referencias`). Ver la nota larga en
bot.py junto a la definición de `scheduler` para el porqué.
"""


def post_fork(server, worker):
    from bot import iniciar_scheduler
    iniciar_scheduler()
