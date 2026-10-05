# Entités SERP — prototype

Outil pour les rédacteurs : à partir des URLs du top 10 (copiées depuis le guide YTG),
il liste ce que les pages concurrentes traitent, en deux familles, puis vérifie
qu'un brouillon les couvre :

- **Entités nommées** : lieux, marques, organismes, personnes (avec MID Knowledge Graph) ;
- **Concepts du sujet** : noms communs du métier (toiture, tuiles, démoussage…).

## Installation (Windows, PowerShell)

```powershell
pip install -r requirements.txt
$env:GOOGLE_NL_API_KEY="AIza...votre_clé..."
streamlit run app.py
```

L'outil s'ouvre dans le navigateur (http://localhost:8501).

Pour ne pas retaper la clé : créer `.streamlit/secrets.toml` à côté de `app.py` :

```toml
GOOGLE_NL_API_KEY = "AIza...votre_clé..."
```

## Utilisation

1. **Entités à couvrir** : coller les URLs du top 10, cliquer sur « Analyser la SERP ».
   Régler le seuil (nombre minimum de pages qui citent l'entité), décocher les entités
   hors sujet.
2. **Vérifier mon brouillon** : coller le texte, cliquer sur « Vérifier la couverture ».
   L'outil affiche le taux de couverture et les entités à ajouter.

## Notes

- Les entités nommées sont celles que l'API rattache au Knowledge Graph (avec un MID).
- Les concepts sont les noms communs détectés par l'API, regroupés entre les pages
  (singulier/pluriel, casse, accents). Les mots de navigation (contact, page, cookie…)
  sont écartés ; décochez à la main ceux qui restent trop génériques.
- Un élément est compté comme couvert si l'API le détecte dans le brouillon, ou si son
  nom y figure (au singulier ou au pluriel).
- Coût : environ 1 $ pour 100 pages analysées au-delà du quota gratuit mensuel
  (API Cloud Natural Language, analyse des entités).
- Les résultats par URL sont gardés en cache 24 h pour ne pas refacturer les mêmes pages.
- Avant un déploiement chez Solocal : clé sur un projet Google Cloud Solocal, validation
  DSI (les brouillons sont envoyés à l'API Google), hébergement interne.

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
   GOOGLE_NL_API_KEY = "AIza...votre_clé..."
   APP_PASSWORD = "mot_de_passe_de_l_equipe"
   ```
4. **Deploy**. Partager ensuite l'URL `https://….streamlit.app` et le mot de passe
   avec l'équipe.

Sans `APP_PASSWORD`, l'app est accessible sans mot de passe (pratique en local).
Pour changer la clé ou le mot de passe : réglages de l'app > Secrets, sans redéployer.
