"""Test rapide de l'API Natural Language, hors Streamlit.
Usage : python test_api.py"""
import os
import time

import requests

# --- Forcer l'IPv4 : sur certains réseaux, l'IPv6 vers Google ne répond pas
import socket
import urllib3.util.connection as _urllib3_cn
_urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

key = os.environ.get("GOOGLE_NL_API_KEY")
print("Clé trouvée :", bool(key))
t0 = time.time()
r = requests.post(
    "https://language.googleapis.com/v1/documents:analyzeEntities",
    params={"key": key}, timeout=(10, 45),
    json={"document": {"type": "PLAIN_TEXT",
                       "content": "Bonifacio et Porto-Vecchio sont en Corse."},
          "encodingType": "UTF8"})
print(f"Réponse {r.status_code} en {time.time() - t0:.1f} s")
print([e["name"] for e in r.json().get("entities", [])] or r.text[:300])
