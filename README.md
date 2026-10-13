# Entités SERP — prototype

Outil pour les rédacteurs : à partir des URLs du top 10 (copiées depuis le guide YTG),
il liste les entités du Knowledge Graph que Google associe aux pages concurrentes.

L'analyse des pages passe par l'actor Apify **Entity SEO API**
(ID `EJWI6K70hv3VyAHvp`) : pour chaque URL, il renvoie les entités du Knowledge Graph
avec leur identifiant, une description et un score de 0 à 100.

## Installation (Windows, PowerShell)

```powershell
pip install -r requirements.txt
$env:APIFY_TOKEN="apify_api_...votre_jeton..."
streamlit run app.py
```

L'outil s'ouvre dans le navigateur (http://localhost:8501).
Le jeton se trouve dans la console Apify : Settings > API & Integrations.

Pour ne pas retaper le jeton : créer `.streamlit/secrets.toml` à côté de `app.py` :

```toml
APIFY_TOKEN = "apify_api_...votre_jeton..."
```

Pour utiliser un autre actor qui prend la même entrée (`{"urls": [...]}`), définir
`APIFY_ACTOR_ID`.

Test rapide hors Streamlit (affiche la réponse brute) : `python test_api.py [url]`.

## Utilisation

Coller les URLs du top 10, cliquer sur « Analyser la SERP ». Régler le seuil (nombre
minimum de pages qui citent l'entité), décocher les entités hors sujet, puis
télécharger la liste (CSV).

## Notes

- Toutes les URLs partent dans une seule exécution de l'actor ; compter de quelques
  secondes à quelques minutes selon le nombre de pages.
- Les résultats par URL sont gardés en cache 24 h pour ne pas refacturer les mêmes pages
  (les échecs ne sont pas mis en cache).
- Noms et descriptions en français : l'identifiant Knowledge Graph de chaque entité
  (`/m/…`, `/g/…`) est retrouvé dans Wikidata (propriétés P646 et P2671), qui fournit
  le libellé et la description en français et le lien Wikipédia FR. Sans fiche
  française, le nom anglais est gardé ; le nom d'origine reste visible dans le tableau.
  Si Wikidata ne répond pas, un avertissement s'affiche et les noms restent en anglais.
- Les entités que ni l'actor ni Wikidata ne nomment sont listées à part
  (« Entités sans nom »), avec un lien vers leur fiche Google.
- La réponse brute d'Apify est consultable dans l'onglet 1 (« Réponse brute d'Apify »)
  pour contrôler les champs renvoyés.
- Coût : facturé par Apify selon la tarification de l'actor (les URLs en échec ne sont
  pas facturées).
- Avant un déploiement chez Solocal : jeton sur un compte Apify Solocal, validation DSI
  (les URLs analysées sont envoyées à Apify), hébergement interne.

## Mise en ligne sur Streamlit Community Cloud

1. **Pousser le code sur un dépôt GitHub privé** (dans ce dossier) :
   ```bash
   git init
   git add .
   git commit -m "Outil Entités SERP"
   gh repo create outil-entites-serp --private --source=. --push
   ```
   Le `.gitignore` exclut `.streamlit/secrets.toml` : la clé ne part jamais sur GitHub.
2. Sur https://share.streamlit.io, se connecter avec GitHub, puis **Create app** :
   dépôt `outil-entites-serp`, branche `main`, fichier `app.py`.
3. Dans **Advanced settings** : choisir Python 3.12, et coller dans **Secrets** :
   ```toml
   APIFY_TOKEN = "apify_api_..."
   APP_PASSWORD = "mot_de_passe_de_l_equipe"
   ```
4. **Deploy**. Partager ensuite l'URL `https://….streamlit.app` et le mot de passe
   avec l'équipe.

Sans `APP_PASSWORD`, l'app est accessible sans mot de passe (pratique en local).
Pour changer le jeton ou le mot de passe : réglages de l'app > Secrets, sans redéployer.
