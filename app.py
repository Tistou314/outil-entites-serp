"""
Outil Entités SERP — prototype pour les équipes de rédaction

Coller les URLs du top 10 (copiées depuis YTG)
→ entités du Knowledge Graph que Google associe à chaque page (actor Apify
  « Entity SEO API »), et socle commun aux pages concurrentes.

Lancement :  streamlit run app.py
Jeton API :  variable d'environnement APIFY_TOKEN
             (ou fichier .streamlit/secrets.toml : APIFY_TOKEN = "apify_api_...")
"""

import hmac
import math
import os
import re
import time
import unicodedata
from collections import Counter, defaultdict

import pandas as pd
import streamlit as st
from apify_client import ApifyClient

st.set_page_config(page_title="Entités SERP", page_icon="🧭", layout="wide")

ACTOR_PAR_DEFAUT = "EJWI6K70hv3VyAHvp"  # Entity SEO API (Apify)
CACHE_TTL = 24 * 3600


def reglage(nom):
    val = os.environ.get(nom)
    if val:
        return val
    try:
        return st.secrets[nom]
    except Exception:
        return None


API_TOKEN = reglage("APIFY_TOKEN")
ACTOR_ID = reglage("APIFY_ACTOR_ID") or ACTOR_PAR_DEFAUT


def verifier_acces():
    """Écran de mot de passe si APP_PASSWORD est défini (secrets Streamlit ou
    variable d'environnement). Sans mot de passe défini : accès libre (usage local)."""
    try:
        mdp = st.secrets.get("APP_PASSWORD")
    except Exception:
        mdp = None
    mdp = mdp or os.environ.get("APP_PASSWORD")
    if not mdp or st.session_state.get("acces_ok"):
        return
    st.title("Entités SERP")
    with st.form("connexion"):
        saisi = st.text_input("Mot de passe de l'équipe", type="password")
        valider = st.form_submit_button("Entrer", type="primary")
    if valider:
        if hmac.compare_digest(saisi.encode(), str(mdp).encode()):
            st.session_state["acces_ok"] = True
            st.rerun()
        st.error("Mot de passe incorrect.")
    st.stop()


verifier_acces()


# ---------------------------------------------------------------- utilitaires
def norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _champ(d, *cles):
    """Première valeur non vide parmi plusieurs noms de champ possibles."""
    for k in cles:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def cle_url(url):
    """Clé de comparaison d'URL : sans protocole, sans www, sans / final."""
    u = re.sub(r"^https?://(www\.)?", "", str(url).strip().lower())
    return u.split("#")[0].rstrip("/")


def lire_entite(e):
    nom = _champ(e, "name", "entityName", "entity", "label", "title")
    if not nom:
        return None
    score = _champ(e, "score", "confidence", "relevance", "relevanceScore", "salience")
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    types = _champ(e, "type", "types", "entityType", "category")
    if isinstance(types, list):
        types = ", ".join(str(t) for t in types)
    return {"nom": str(nom),
            "id": str(_champ(e, "id", "mid", "kgId", "kgmid", "kgMid", "entityId",
                             "knowledgeGraphId", "wikidataId") or norm(str(nom))),
            "score": score,
            "type": str(types or ""),
            "description": str(_champ(e, "description", "desc", "detailedDescription") or ""),
            "lien": str(_champ(e, "wikipediaUrl", "wikipedia", "wikiUrl", "wikidataUrl",
                               "kgUrl", "link") or "")}


def lire_items(items):
    """Éléments du dataset Apify → {clé d'URL: {"entites": [...], "erreur": str|None}}.
    Accepte un élément par page (avec une liste d'entités) ou un élément par entité."""
    out = defaultdict(lambda: {"entites": [], "erreur": None})
    for it in items:
        url = _champ(it, "url", "inputUrl", "input_url", "pageUrl", "sourceUrl", "page")
        if not url:
            continue
        page = out[cle_url(url)]
        if _champ(it, "error", "errorMessage"):
            page["erreur"] = str(_champ(it, "error", "errorMessage"))[:150]
        ents = _champ(it, "entities", "knowledgeGraphEntities", "kgEntities", "results")
        if isinstance(ents, list):
            page["entites"] += [x for x in map(lire_entite, ents) if x]
        else:
            e = lire_entite(it)
            if e:
                page["entites"].append(e)
    return out


@st.cache_resource
def _cache_apify():
    """Cache partagé {clé d'URL: (horodatage, résultat)} : une page déjà analysée
    dans les dernières 24 h n'est pas refacturée."""
    return {}


def infos_run(run):
    """(identifiant du dataset, statut) d'une exécution : dictionnaire avec
    apify-client 1.x/2.x, objet avec apify-client 3.x."""
    if run is None:
        return None, None
    if isinstance(run, dict):
        dataset_id, statut = run.get("defaultDatasetId"), run.get("status")
    else:
        dataset_id = getattr(run, "default_dataset_id", None)
        statut = getattr(run, "status", None)
    statut = getattr(statut, "value", statut)  # énumération en 3.x
    return dataset_id, (str(statut) if statut is not None else None)


def analyser_urls(urls):
    """Lance l'actor Apify sur les URLs absentes du cache ; renvoie
    ({url: [entités]}, {url: erreur}, éléments bruts du dernier appel)."""
    cache, maintenant = _cache_apify(), time.time()
    a_faire = [u for u in urls if not (cle_url(u) in cache
               and maintenant - cache[cle_url(u)][0] < CACHE_TTL)]
    bruts = []
    if a_faire:
        client = ApifyClient(API_TOKEN)
        print(f"→ Apify : {len(a_faire)} URL(s) envoyée(s)", flush=True)
        t0 = time.time()
        run = client.actor(ACTOR_ID).call(run_input={"urls": a_faire})
        dataset_id, statut = infos_run(run)
        if not dataset_id:
            raise RuntimeError("l'actor Apify n'a renvoyé aucun résultat")
        if statut not in (None, "SUCCEEDED"):
            raise RuntimeError(f"exécution Apify terminée avec le statut {statut}")
        bruts = [dict(x) for x in client.dataset(dataset_id).iterate_items()]
        print(f"← {len(bruts)} élément(s) en {time.time() - t0:.1f} s", flush=True)
        lus = lire_items(bruts)
        for u in a_faire:
            res = lus.get(cle_url(u))
            if res and res["entites"]:  # on ne garde pas les échecs en cache
                cache[cle_url(u)] = (maintenant, res)
            else:
                cache.pop(cle_url(u), None)
                lus.setdefault(cle_url(u), {"entites": [], "erreur": None})
        resultats_neufs = lus
    else:
        resultats_neufs = {}
    resultats, erreurs = {}, {}
    for u in urls:
        res = resultats_neufs.get(cle_url(u)) or cache.get(cle_url(u), (0, None))[1]
        if res and res["entites"]:
            resultats[u] = res["entites"]
        else:
            erreurs[u] = (res or {}).get("erreur") or "aucune entité renvoyée par Apify"
    return resultats, erreurs, bruts


def agreger(resultats):
    """resultats = {url: [entités]} → groupes fusionnés par libellé normalisé."""
    par_id = defaultdict(lambda: {"noms": Counter(), "urls": {}, "type": "",
                                  "description": "", "lien": ""})
    for url, ents in resultats.items():
        for e in ents:
            g = par_id[e["id"]]
            g["noms"][e["nom"]] += 1
            g["urls"][url] = max(g["urls"].get(url, 0), e["score"])
            g["type"] = g["type"] or e["type"]
            g["description"] = g["description"] or e["description"]
            g["lien"] = g["lien"] or e["lien"]

    groupes = {}
    for id_, g in par_id.items():
        libelle = g["noms"].most_common(1)[0][0]
        cle = norm(libelle)
        if cle not in groupes:
            groupes[cle] = {"libelle": libelle, "ids": set(), "noms": set(),
                            "urls": {}, "type": g["type"], "description": g["description"],
                            "lien": g["lien"]}
        G = groupes[cle]
        G["ids"].add(id_)
        G["noms"].update(g["noms"].keys())
        for u, s in g["urls"].items():
            G["urls"][u] = max(G["urls"].get(u, 0), s)
    return list(groupes.values())


# ---------------------------------------------------------------- interface
st.title("Entités SERP")
st.caption("Les entités du Knowledge Graph que Google associe aux pages du top 10.")

if not API_TOKEN:
    st.error("Jeton Apify absent : définir APIFY_TOKEN (voir README).")
    st.stop()


def tableau_editable(lignes, cle_session, nom_csv, colonnes_lien=()):
    """Affiche un tableau avec une colonne « Garder » ; renvoie les lignes gardées."""
    df = pd.DataFrame([l["affichage"] for l in lignes])
    edite = st.data_editor(
        df, hide_index=True, width="stretch", key=f"editeur_{cle_session}",
        disabled=[c for c in df.columns if c != "Garder"],
        column_config={c: st.column_config.LinkColumn() for c in colonnes_lien})
    gardes = [l["groupe"] for l, keep in zip(lignes, edite["Garder"]) if keep]
    st.session_state[cle_session] = gardes
    st.download_button(
        "Télécharger (CSV)",
        edite[edite["Garder"]].drop(columns="Garder")
            .to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name=nom_csv, mime="text/csv", key=f"dl_{cle_session}")
    return gardes


urls_brutes = st.text_area(
    "URLs du top 10, une par ligne (copiées depuis le guide YTG)",
    height=200, placeholder="https://www.exemple.fr/page-1\nhttps://www.exemple.fr/page-2")

if st.button("Analyser la SERP", type="primary"):
    urls = list(dict.fromkeys(
        u.strip() for u in urls_brutes.splitlines() if u.strip().startswith("http")))
    if not urls:
        st.warning("Aucune URL valide.")
    else:
        with st.spinner(f"Analyse de {len(urls)} page(s) par Apify… "
                        "(compter de quelques secondes à quelques minutes)"):
            try:
                resultats, erreurs, bruts = analyser_urls(urls)
            except Exception as e:
                st.error(f"Erreur Apify : {e}")
                st.stop()
        st.session_state["serp"] = resultats
        if bruts:
            st.session_state["bruts"] = bruts
        st.session_state["etat"] = [{
            "URL": u,
            "Statut": "✅" if u in resultats else f"⚠ {erreurs.get(u)}",
            "Entités": len(resultats.get(u, [])),
        } for u in urls]
        st.session_state.pop("socle", None)

if st.session_state.get("serp"):
    resultats = st.session_state["serp"]
    n = len(resultats)
    with st.expander(f"Pages analysées : {n} sur {len(st.session_state['etat'])}"):
        st.dataframe(pd.DataFrame(st.session_state["etat"]),
                     hide_index=True, width="stretch")
    if st.session_state.get("bruts"):
        with st.expander("Réponse brute d'Apify (contrôle)"):
            st.json(st.session_state["bruts"][:3])

    if n > 1:
        seuil = st.slider("Présent dans au moins … pages", 1, n,
                          value=max(1, math.ceil(n / 3)))
    else:
        seuil = 1
        st.caption("Une seule page analysée : toutes ses entités sont reprises. "
                   "Ajoutez d'autres URLs pour dégager un socle commun.")
    st.caption("Décochez ce qui est hors sujet (faux sens, pays étrangers, "
               "termes trop génériques) avant de télécharger la liste.")

    socle = sorted([g for g in agreger(resultats) if len(g["urls"]) >= seuil],
                   key=lambda g: (-len(g["urls"]), -sum(g["urls"].values())))
    st.subheader(f"Entités à couvrir : {len(socle)}")
    st.caption("Entités du Knowledge Graph associées aux pages, avec leur score "
               "(0 à 100 : à quel point l'entité est centrale dans la page).")
    if not socle:
        st.info("Aucune entité à ce seuil.")
        st.session_state["socle"] = []
    else:
        tableau_editable([{"groupe": g, "affichage": {
            "Garder": True,
            "Entité": g["libelle"],
            "Type": g["type"],
            "Pages": f"{len(g['urls'])}/{n}",
            "Score moy.": round(sum(g["urls"].values()) / len(g["urls"]), 1),
            "Description": g["description"],
            "Lien": g["lien"],
            "ID": ", ".join(sorted(g["ids"])),
        }} for g in socle], "socle", "entites.csv", ("Lien",))
