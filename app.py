"""
Outil Entités SERP — prototype pour les équipes de rédaction

1. « Entités à couvrir » : coller les URLs du top 10 (copiées depuis YTG)
   → entités du Knowledge Graph que Google associe à chaque page (actor Apify
     « Entity SEO API »), et socle commun aux pages concurrentes.
2. « Vérifier mon brouillon » : coller son texte
   → score de couverture + ce qui manque.

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
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
import streamlit as st
import trafilatura
from apify_client import ApifyClient

# --- Forcer l'IPv4 : sur certains réseaux, l'IPv6 vers Google ne répond pas
import socket
import urllib3.util.connection as _urllib3_cn
_urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

st.set_page_config(page_title="Entités SERP", page_icon="🧭", layout="wide")

ACTOR_PAR_DEFAUT = "EJWI6K70hv3VyAHvp"  # Entity SEO API (Apify)
CACHE_TTL = 24 * 3600
MAX_CHARS = 20000
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


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


def singulier(mot):
    if len(mot) > 3 and mot[-1] in "sx" and not mot.endswith("ss"):
        return mot[:-1]
    return mot


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def recuperer_texte(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=(5, 20))
        r.raise_for_status()
    except requests.RequestException as e:
        return None, f"page inaccessible ({e.__class__.__name__})"
    try:
        texte = trafilatura.extract(r.text, include_comments=True, favor_recall=True)
    except Exception as e:
        return None, f"extraction impossible ({e.__class__.__name__})"
    if not texte or len(texte) < 200:
        return None, "pas de texte exploitable (page en JavaScript ou bloquée)"
    return texte[:MAX_CHARS], None


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
                            "lien": g["lien"], "famille": "Entité"}
        G = groupes[cle]
        G["ids"].add(id_)
        G["noms"].update(g["noms"].keys())
        for u, s in g["urls"].items():
            G["urls"][u] = max(G["urls"].get(u, 0), s)
    return list(groupes.values())


# ---------------------------------------------------------------- interface
st.title("Entités SERP")
st.caption("Les entités du Knowledge Graph que Google associe aux pages du top 10, "
           "et celles qui manquent à votre brouillon.")

if not API_TOKEN:
    st.error("Jeton Apify absent : définir APIFY_TOKEN (voir README).")
    st.stop()

onglet1, onglet2 = st.tabs(["1 · Entités à couvrir", "2 · Vérifier mon brouillon"])

# ---------- onglet 1
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


with onglet1:
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
            # Texte des pages : sert seulement aux exemples et au nombre d'emplois
            with st.spinner("Récupération du texte des pages pour les exemples…"):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    textes = {u: t for u, (t, err) in
                              zip(urls, pool.map(recuperer_texte, urls)) if not err}
            st.session_state["serp"] = resultats
            st.session_state["textes"] = textes
            if bruts:
                st.session_state["bruts"] = bruts
            st.session_state["etat"] = [{
                "URL": u,
                "Statut": "✅" if u in resultats else f"⚠ {erreurs.get(u)}",
                "Entités": len(resultats.get(u, [])),
                "Texte récupéré": "oui" if u in textes else "non",
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
                   "termes trop génériques) avant de vérifier un brouillon.")

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

# ---------- onglet 2
def formes_de(noms):
    """Toutes les formes à chercher pour un élément : normalisée, singulier, pluriel."""
    formes = set()
    for nm in noms:
        base = norm(nm)
        if not base:
            continue
        sing = " ".join(singulier(m) for m in base.split())
        formes |= {base, sing, sing + "s"}
    return formes


def compter(formes, texte_norm):
    return sum(texte_norm.count(f" {f} ") for f in formes)


def extrait(formes, textes, urls_triees):
    """Première phrase d'une page concurrente qui emploie le terme (exemple d'usage)."""
    for url in urls_triees:
        for phrase in re.split(r"(?<=[.!?])\s+|\n+", textes.get(url, "")):
            pn = f" {norm(phrase)} "
            if 20 <= len(phrase) <= 260 and any(f" {f} " in pn for f in formes):
                return phrase.strip(), url
    return None, None


def surligner(phrase, noms):
    """Met en gras la première occurrence du terme dans l'extrait."""
    for nm in sorted(noms, key=len, reverse=True):
        motif = re.compile(rf"(?i)(?<!\w)({re.escape(nm)}s?|{re.escape(nm[:-1])})(?!\w)")
        if motif.search(phrase):
            return motif.sub(r"**\1**", phrase, count=1)
    return phrase


def domaine(url):
    return re.sub(r"^https?://(www\.)?", "", url).split("/")[0]


PRIORITES = [("Indispensable", 0.75, "red"), ("Recommandé", 0.5, "orange"),
             ("Facultatif", 0.0, "gray")]


def priorite(part):
    for nom, seuil, couleur in PRIORITES:
        if part >= seuil:
            return nom, couleur
    return PRIORITES[-1][0], PRIORITES[-1][2]


def carte_manquant(it):
    with st.container(border=True):
        st.markdown(
            f"**{it['libelle']}** &nbsp; :{it['couleur']}-badge[{it['priorite']}] "
            f":gray-badge[{it['famille']}]  \n"
            f":gray[Employé par {it['pages']}/{it['n']} concurrents"
            + (f", environ {it['med']} fois par page" if it["med"] >= 2 else "") + "]")
        if it["extrait"]:
            st.markdown(f"> {surligner(it['extrait'], it['noms'])}  \n"
                        f"> :gray[— {domaine(it['source'])}]")


with onglet2:
    socle = st.session_state.get("socle") or []
    if not socle:
        st.info("Commencez par l'onglet 1 pour définir les entités de référence.")
    else:
        n_pages = len(st.session_state.get("serp", {})) or 1
        textes = st.session_state.get("textes", {})
        textes_norm = {u: f" {norm(t)} " for u, t in textes.items()}
        st.caption(f"Référence : {len(socle)} entités tirées de "
                   f"{n_pages} pages concurrentes.")
        brouillon = st.text_area("Collez votre brouillon", height=250)

        if st.button("Analyser mon brouillon", type="primary") and brouillon.strip():
            texte_norm = f" {norm(brouillon)} "

            items = []
            for g in socle:
                formes = formes_de(g["noms"])
                nb = compter(formes, texte_norm)
                part = len(g["urls"]) / n_pages
                prio, couleur = priorite(part)
                # emplois par page chez les concurrents dont on a le texte
                ments = sorted(compter(formes, textes_norm[u])
                               for u in g["urls"] if u in textes_norm) or [0]
                med = ments[len(ments) // 2]
                urls_triees = sorted(g["urls"], key=lambda u: -g["urls"][u])
                items.append({
                    "libelle": g["libelle"], "famille": g["famille"],
                    "pages": len(g["urls"]), "n": n_pages, "part": part,
                    "priorite": prio, "couleur": couleur, "med": med,
                    "nb": nb, "formes": formes,
                    "noms": g["noms"],
                    "urls_triees": urls_triees, "extrait": None, "source": None,
                })

            manquants = sorted([i for i in items if i["nb"] == 0],
                               key=lambda i: (-i["part"], -i["med"]))
            a_developper = sorted(
                [i for i in items if i["nb"] > 0 and i["med"] >= 3
                 and i["nb"] * 3 <= i["med"] and i["part"] >= 0.5],
                key=lambda i: (-i["part"], -(i["med"] - i["nb"])))
            couverts = [i for i in items if i["nb"] > 0]
            for it in manquants:
                it["extrait"], it["source"] = extrait(it["formes"], textes, it["urls_triees"])

            par_prio = {nom: [i for i in items if i["priorite"] == nom] for nom, _, _ in PRIORITES}
            manq_prio = {nom: [i for i in manquants if i["priorite"] == nom] for nom, _, _ in PRIORITES}

            # --- verdict
            n_ind = len(manq_prio["Indispensable"])
            n_rec = len(manq_prio["Recommandé"])
            if n_ind == 0 and n_rec == 0 and not a_developper:
                st.success("Votre brouillon couvre toutes les entités importantes de la SERP.")
            else:
                morceaux = []
                if n_ind:
                    morceaux.append(f"**{n_ind} élément{'s' if n_ind > 1 else ''} "
                                    f"indispensable{'s' if n_ind > 1 else ''}** à intégrer")
                if n_rec:
                    morceaux.append(f"**{n_rec} recommandé{'s' if n_rec > 1 else ''}**")
                if a_developper:
                    morceaux.append(f"**{len(a_developper)} à développer**")
                st.markdown("### " + "À faire : " + ", ".join(morceaux))

            c1, c2, c3 = st.columns(3)
            for col, nom in zip((c1, c2), ("Indispensable", "Recommandé")):
                total = len(par_prio[nom])
                ok = total - len(manq_prio[nom])
                col.metric(f"{nom}s couverts", f"{ok}/{total}" if total else "—")
            c3.metric("À développer", len(a_developper))

            # --- 1. à ajouter
            if n_ind or n_rec:
                st.subheader("1 · À ajouter")
                st.caption("Ce que les concurrents traitent et que votre texte ne mentionne pas. "
                           "L'extrait montre comment un concurrent l'emploie.")
                for nom in ("Indispensable", "Recommandé"):
                    liste = manq_prio[nom]
                    if not liste:
                        continue
                    aide = ("employés par au moins 75 % des concurrents" if nom == "Indispensable"
                            else "employés par au moins la moitié des concurrents")
                    st.markdown(f"##### {nom}s ({len(liste)}) &nbsp; :gray[{aide}]")
                    cols = st.columns(2)
                    for k, it in enumerate(liste):
                        with cols[k % 2]:
                            carte_manquant(it)

            # --- 2. à développer
            if a_developper:
                st.subheader("2 · À développer")
                st.caption("Présent dans votre texte, mais nettement moins traité que "
                           "chez les concurrents.")
                st.dataframe(pd.DataFrame([{
                    "Élément": i["libelle"],
                    "Dans votre texte": f"{i['nb']} fois",
                    "Chez les concurrents": f"~{i['med']} fois par page",
                    "Priorité": i["priorite"],
                } for i in a_developper]), hide_index=True, width="stretch")

            # --- 3. facultatif + déjà couvert (repliés)
            if manq_prio["Facultatif"]:
                with st.expander(f"Pistes facultatives ({len(manq_prio['Facultatif'])}) : "
                                 "traitées par une minorité de concurrents"):
                    st.markdown(" · ".join(i["libelle"] for i in manq_prio["Facultatif"]))
            with st.expander(f"Déjà couverts ({len(couverts)})"):
                st.markdown(" · ".join(
                    f"{i['libelle']} ({i['nb']})" for i in
                    sorted(couverts, key=lambda i: (-i["part"], i["libelle"]))))

            # --- checklist à copier
            lignes = []
            for nom in ("Indispensable", "Recommandé"):
                for it in manq_prio[nom]:
                    lignes.append(f"[ ] Ajouter « {it['libelle']} » ({nom.lower()}, "
                                  f"{it['pages']}/{it['n']} concurrents)")
            for it in a_developper:
                lignes.append(f"[ ] Développer « {it['libelle']} » "
                              f"({it['nb']} fois → viser ~{it['med']})")
            if lignes:
                with st.expander("Liste de tâches à copier"):
                    st.code("\n".join(lignes), language=None)
