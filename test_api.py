"""Test rapide de l'actor Apify « Entity SEO API », hors Streamlit.
Affiche la réponse brute pour contrôler les champs renvoyés.
Usage : python test_api.py [url]"""
import json
import os
import sys
import time

from apify_client import ApifyClient

token = os.environ.get("APIFY_TOKEN")
actor = os.environ.get("APIFY_ACTOR_ID") or "EJWI6K70hv3VyAHvp"
url = sys.argv[1] if len(sys.argv) > 1 else "https://fr.wikipedia.org/wiki/Marie_Curie"
print("Jeton trouvé :", bool(token))
t0 = time.time()
client = ApifyClient(token)
run = client.actor(actor).call(run_input={"urls": [url]})
print(f"Statut {run.get('status')} en {time.time() - t0:.1f} s")
for item in client.dataset(run["defaultDatasetId"]).iterate_items():
    print(json.dumps(item, ensure_ascii=False, indent=2)[:3000])
