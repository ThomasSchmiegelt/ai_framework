"""Feature-Router-Paket fuer das AI_Framework_Thomas-Backend.

Jedes Modul definiert einen ``router = APIRouter()``; ``main.py`` importiert sie
und haengt sie per ``app.include_router(...)`` VOR dem StaticFiles-Mount ein.
Geteilte Namen kommen ueber ``from core import *``.
"""
