"""
Outil Entités SERP — prototype pour les équipes de rédaction

1. « Entités à couvrir » : coller les URLs du top 10 (copiées depuis YTG)
   → socle commun aux pages concurrentes, en deux familles :
     - entités nommées (lieux, marques, personnes) avec MID Knowledge Graph ;
     - concepts du sujet (noms communs : toiture, tuiles, zinguerie…).
2. « Vérifier mon brouillon » : coller son texte
   → score de couverture des deux familles + ce qui manque.

Lancement :  streamlit run app.py
Clé API   :  variable d'environnement GOOGLE_NL_API_KEY
             (ou fichier .streamlit/secrets.toml : GOOGLE_NL_API_KEY = "AIza...")
"""

import hmac
import math
import os
import re
import time
import unicodedata
from collections import Counter, defaultdict

import pandas as pd
import requests
import streamlit as st
import trafilatura

# --- Forcer l'IPv4 : sur certains réseaux, l'IPv6 vers Google ne répond pas
import socket
import urllib3.util.connection as _urllib3_cn
_urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

st.set_page_config(page_title="Entités SERP", page_icon="🧭", layout="wide")

NL_URL = "https://language.googleapis.com/v1/documents:analyzeEntities"
MAX_CHARS = 20000
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def get_api_key():
    key = os.environ.get("GOOGLE_NL_API_KEY")
    if key:
        return key
    try:
        return st.secrets["GOOGLE_NL_API_KEY"]
    except Exception:
        return None


API_KEY = get_api_key()


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


# Mots de navigation / gabarit web, sans intérêt éditorial
MOTS_VIDES = {
    "accueil", "article", "articles", "avis", "commentaire", "commentaires",
    "contact", "cookie", "cookies", "email", "e mail", "lien", "liens", "menu",
    "merci", "newsletter", "page", "pages", "partage", "politique", "site",
    "sites", "internet", "web", "chose", "choses", "fois", "cas", "exemple",
    "personne", "personnes", "gens", "an", "ans", "annee", "annees", "jour",
    "jours", "mois", "semaine", "heure", "heures", "minute", "minutes",
}


# Mots trop généraux pour guider la rédaction : décochés par défaut (re-cochables)
GENERIQUES = {
    "particulier", "professionnel", "entreprise", "societe", "service", "client",
    "besoin", "demande", "qualite", "conseil", "etape", "changement", "proximite",
    "partenaire", "coeur", "type", "regle", "zone", "vie", "experience",
    "savoir faire", "projet", "delai", "travail", "equipe", "gerant", "contrainte",
    "solution", "prix", "tarif", "cout", "offre", "domaine", "secteur", "region",
    "ville", "commune", "alentour", "environ", "niveau", "moment", "temp", "part",
    "question", "reponse", "information", "detail", "point", "aspect", "element",
    "maniere", "facon", "raison", "objectif", "resultat", "probleme", "possibilite",
    "satisfaction", "confiance", "disponibilite",
}


def singulier(mot):
    if len(mot) > 3 and mot[-1] in "sx" and not mot.endswith("ss"):
        return mot[:-1]
    return mot


def cle_concept(nom):
    """Clé de regroupement d'un concept : minuscules, sans accents, au singulier.
    Renvoie None pour les termes trop courts, chiffrés ou de navigation."""
    n = norm(nom)
    if len(n) < 3 or any(c.isdigit() for c in n) or n in MOTS_VIDES:
        return None
    if len(n.split()) > 4:  # groupe nominal trop long : rarement réutilisable
        return None
    return " ".join(singulier(m) for m in n.split())


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


def analyser(texte):
    """Appel à l'API Natural Language, avec cache en mémoire de session
    (pas de st.cache_data ici : on écarte cette piste du blocage)."""
    cache = st.session_state.setdefault("_cache_nl", {})
    cle = hash(texte)
    if cle in cache:
        return cache[cle]
    t0 = time.time()
    print("    → requête envoyée à Google…", flush=True)
    try:
        r = requests.post(NL_URL, params={"key": API_KEY}, timeout=(10, 45), json={
            "document": {"type": "PLAIN_TEXT", "content": texte[:MAX_CHARS]},
            "encodingType": "UTF8"})
    except requests.RequestException as e:
        print(f"    ← échec après {time.time() - t0:.1f} s : {e!r}", flush=True)
        raise RuntimeError(f"connexion à Google impossible ({e.__class__.__name__})")
    print(f"    ← réponse {r.status_code} en {time.time() - t0:.1f} s", flush=True)
    if r.status_code != 200:
        raise RuntimeError(r.json().get("error", {}).get("message", r.text[:200]))
    out = {"nommees": [], "concepts": []}
    for e in r.json().get("entities", []):
        meta = e.get("metadata", {})
        if meta.get("mid"):  # entité du Knowledge Graph
            out["nommees"].append({"nom": e["name"], "type": e.get("type", ""),
                                   "saillance": e.get("salience", 0.0),
                                   "mid": meta["mid"],
                                   "mentions": len(e.get("mentions", [])),
                                   "wikipedia": meta.get("wikipedia_url", "")})
            continue
        types_mentions = {m.get("type") for m in e.get("mentions", [])}
        cle_c = cle_concept(e["name"])
        if "COMMON" in types_mentions and cle_c:
            out["concepts"].append({"nom": e["name"], "cle": cle_c,
                                    "saillance": e.get("salience", 0.0),
                                    "mentions": len(e.get("mentions", []))})
    cache[cle] = out
    return out


def agreger(resultats):
    """resultats = {url: [entités]} → groupes fusionnés par libellé normalisé
    (ex. Bastia commune + Bastia arrondissement = une seule ligne)."""
    par_mid = defaultdict(lambda: {"noms": Counter(), "urls": {}, "ment": Counter(),
                                   "type": "", "wiki": ""})
    for url, res in resultats.items():
        for e in res["nommees"]:
            g = par_mid[e["mid"]]
            g["noms"][e["nom"]] += 1
            g["urls"][url] = max(g["urls"].get(url, 0), e["saillance"])
            g["ment"][url] += e.get("mentions", 1)
            g["type"] = g["type"] or e["type"]
            g["wiki"] = g["wiki"] or e["wikipedia"]

    groupes = {}
    for mid, g in par_mid.items():
        libelle = g["noms"].most_common(1)[0][0]
        cle = norm(libelle)
        if cle not in groupes:
            groupes[cle] = {"libelle": libelle, "mids": set(), "noms": set(),
                            "urls": {}, "ment": Counter(), "type": g["type"],
                            "wiki": g["wiki"], "famille": "Lieu / nom propre"}
        G = groupes[cle]
        G["mids"].add(mid)
        G["noms"].update(g["noms"].keys())
        for u, s in g["urls"].items():
            G["urls"][u] = max(G["urls"].get(u, 0), s)
        G["ment"].update(g["ment"])
    return list(groupes.values())


def agreger_concepts(resultats):
    """Regroupe les noms communs entre pages (singulier/pluriel, casse, accents)."""
    groupes = defaultdict(lambda: {"noms": Counter(), "urls": {}, "mentions": 0,
                                   "ment": Counter()})
    for url, res in resultats.items():
        for c in res["concepts"]:
            g = groupes[c["cle"]]
            g["noms"][c["nom"].lower()] += 1
            g["urls"][url] = max(g["urls"].get(url, 0), c["saillance"])
            g["mentions"] += c["mentions"]
            g["ment"][url] += c["mentions"]
    out = []
    for cle, g in groupes.items():
        out.append({"cle": cle, "libelle": g["noms"].most_common(1)[0][0],
                    "noms": set(g["noms"]), "urls": g["urls"],
                    "mentions": g["mentions"], "ment": g["ment"],
                    "famille": "Notion"})
    return out


# ---------------------------------------------------------------- interface
st.title("Entités SERP")
st.caption("Les entités (lieux, marques, personnes…) que les pages du top 10 "
           "traitent, et celles qui manquent à votre brouillon.")

if not API_KEY:
    st.error("Clé API absente : définir GOOGLE_NL_API_KEY (voir README).")
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
            resultats, etat, textes = {}, [], {}
            barre = st.progress(0.0, text="Analyse en cours…")
            for i, url in enumerate(urls, 1):
                barre.progress((i - 1) / len(urls), text=f"{i}/{len(urls)} · téléchargement de {url}")
                print(f"[{i}/{len(urls)}] téléchargement {url}", flush=True)
                texte, err = recuperer_texte(url)
                if not err:
                    textes[url] = texte
                    barre.progress((i - 1) / len(urls), text=f"{i}/{len(urls)} · analyse Google ({len(texte)} car.)")
                    print(f"    texte extrait : {len(texte)} car. → appel API", flush=True)
                    try:
                        resultats[url] = analyser(texte)
                        print(f"    {len(resultats[url]['nommees'])} entités nommées · "
                              f"{len(resultats[url]['concepts'])} concepts", flush=True)
                    except Exception as e:
                        err = f"erreur API : {e}"
                if err:
                    print(f"    ⚠ {err}", flush=True)
                res = resultats.get(url, {"nommees": [], "concepts": []})
                etat.append({"URL": url,
                             "Statut": "✅" if not err else f"⚠ {err}",
                             "Entités nommées": len(res["nommees"]),
                             "Concepts": len(res["concepts"])})
                barre.progress(i / len(urls), text=f"{i}/{len(urls)} pages analysées")
            barre.empty()
            st.session_state["serp"] = resultats
            st.session_state["etat"] = etat
            st.session_state["textes"] = textes
            for k in ("socle", "socle_concepts"):
                st.session_state.pop(k, None)

    if st.session_state.get("serp"):
        resultats = st.session_state["serp"]
        n = len(resultats)
        with st.expander(f"Pages analysées : {n} sur {len(st.session_state['etat'])}"):
            st.dataframe(pd.DataFrame(st.session_state["etat"]),
                         hide_index=True, width="stretch")

        seuil = st.slider("Présent dans au moins … pages", 1, max(n, 1),
                          value=max(1, math.ceil(n / 3)))
        st.caption("Décochez ce qui est hors sujet (faux sens, pays étrangers, "
                   "mots trop génériques) avant de vérifier un brouillon.")

        # --- entités nommées
        socle = sorted([g for g in agreger(resultats) if len(g["urls"]) >= seuil],
                       key=lambda g: (-len(g["urls"]), -sum(g["urls"].values())))
        st.subheader(f"Entités nommées : {len(socle)}")
        st.caption("Lieux, marques, organismes, personnes, rattachés au Knowledge Graph.")
        if not socle:
            st.info("Aucune entité nommée à ce seuil.")
            st.session_state["socle"] = []
        else:
            tableau_editable([{"groupe": g, "affichage": {
                "Garder": True,
                "Entité": g["libelle"],
                "Type": g["type"],
                "Pages": f"{len(g['urls'])}/{n}",
                "Saillance moy.": round(sum(g["urls"].values()) / len(g["urls"]), 4),
                "Wikipédia": g["wiki"],
                "MID": ", ".join(sorted(g["mids"])),
            }} for g in socle], "socle", "entites_nommees.csv", ("Wikipédia",))

        # --- concepts
        concepts = sorted(
            [c for c in agreger_concepts(resultats) if len(c["urls"]) >= seuil],
            key=lambda c: (-len(c["urls"]), -sum(c["urls"].values())))[:60]
        st.subheader(f"Concepts du sujet : {len(concepts)}")
        st.caption("Noms communs que les pages traitent (ex. toiture, tuiles, "
                   "démoussage). Limité aux 60 premiers. Les mots trop généraux "
                   "(entreprise, besoin, qualité…) sont décochés d'office : "
                   "recochez-les s'ils comptent pour votre sujet.")
        if not concepts:
            st.info("Aucun concept à ce seuil.")
            st.session_state["socle_concepts"] = []
        else:
            tableau_editable([{"groupe": c, "affichage": {
                "Garder": c["cle"] not in GENERIQUES,
                "Concept": c["libelle"],
                "Pages": f"{len(c['urls'])}/{n}",
                "Mentions": c["mentions"],
                "Saillance moy.": round(sum(c["urls"].values()) / len(c["urls"]), 4),
                "Variantes": ", ".join(sorted(c["noms"]))[:80],
            }} for c in concepts], "socle_concepts", "concepts.csv")

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
    socle_c = st.session_state.get("socle_concepts") or []
    if not socle and not socle_c:
        st.info("Commencez par l'onglet 1 pour définir les éléments de référence.")
    else:
        n_pages = len(st.session_state.get("serp", {})) or 1
        textes = st.session_state.get("textes", {})
        st.caption(f"Référence : {len(socle) + len(socle_c)} éléments tirés de "
                   f"{n_pages} pages concurrentes.")
        brouillon = st.text_area("Collez votre brouillon", height=250)

        if st.button("Analyser mon brouillon", type="primary") and brouillon.strip():
            try:
                res_b = analyser(brouillon)
            except RuntimeError as e:
                st.error(f"Erreur API : {e}")
                st.stop()
            mids_b = {e["mid"] for e in res_b["nommees"]}
            cles_b = {c["cle"] for c in res_b["concepts"]}
            texte_norm = f" {norm(brouillon)} "

            items = []
            for g in socle + socle_c:
                formes = formes_de(g["noms"])
                nb = compter(formes, texte_norm)
                detecte = bool(g.get("mids", set()) & mids_b) or g.get("cle") in cles_b
                part = len(g["urls"]) / n_pages
                prio, couleur = priorite(part)
                ments = sorted(g["ment"].values()) or [0]
                med = ments[len(ments) // 2]
                urls_triees = sorted(g["urls"], key=lambda u: -g["urls"][u])
                items.append({
                    "libelle": g["libelle"], "famille": g["famille"],
                    "pages": len(g["urls"]), "n": n_pages, "part": part,
                    "priorite": prio, "couleur": couleur, "med": med,
                    "nb": max(nb, 1 if detecte else 0), "formes": formes,
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
                st.success("Votre brouillon couvre tous les éléments importants de la SERP.")
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
