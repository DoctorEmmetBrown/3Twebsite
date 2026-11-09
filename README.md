# Site de l'équipe 3T (Translational Theranostics Team, IAB)

Site statique construit avec [Hugo](https://gohugo.io) (version étendue 0.140.2), sans thème externe.
Le site est en anglais. Le contenu vit dans `content/` (Markdown) et `data/` (YAML) : on n'a jamais besoin de toucher aux gabarits pour le mettre à jour.

## Prévisualiser chez soi

```bash
brew install hugo      # une seule fois
hugo server            # puis ouvrir http://localhost:1313
```

## Mettre à jour le site

| Je veux… | Je modifie… |
|---|---|
| ajouter / retirer un membre | `data/members.yaml` (copier un bloc ; `group` = metals, xwi ou igs ; `kind` = lead ou staff : « Permanent members », junior : « PhD students and postdocs », alumni) |
| ajouter une photo | déposer le fichier dans `static/img/team/` sous le nom indiqué par `photo:` du membre (ex. `benoit-busser.jpg`, carré de préférence, 400 px suffisent) ; sans fichier, ce sont les initiales qui s'affichent |
| ajouter ORCID, site perso, LinkedIn | champs `orcid:` (identifiant seul), `web:` et `linkedin:` (adresse complète) du membre dans `data/members.yaml` |
| publier une actualité | `hugo new news/mon-titre.md`, rédiger, passer `draft: false` |
| mettre un lien dans une actualité ou une page | Markdown : `[texte](https://…)` ; les liens externes s'ouvrent dans un nouvel onglet |
| ajouter un projet | `data/projects.yaml` (la description est optionnelle) |
| ajouter un article clé | `data/publications.yaml` |
| mettre à jour la liste complète des publications | `python3 scripts/fetch_publications.py` (voir la section ORCID) |
| ajouter un ORCID à un membre | `orcid:` dans `data/members.yaml` |
| changer les chiffres de l'accueil | `data/numbers.yaml` |
| modifier les partenaires | `data/collaborators.yaml` |
| modifier le texte d'un groupe | `content/research/*.md` |
| modifier une plateforme | `content/platforms/*.md` |
| ajouter un email de contact | `hugo.toml`, ajouter `contact_email = "..."` sous `[params]` ; il apparaît sur la page Contact |
| changer l'adresse | `hugo.toml`, `[params] address` |

## Publications : récupération automatique depuis ORCID

La page Publications affiche les articles clés (`data/publications.yaml`) puis la liste complète des
membres, avec recherche et filtre par groupe. Cette liste est générée par un script Python sans
dépendance (Python 3.9 ou plus) à partir des identifiants ORCID de `data/members.yaml` (champ `orcid:`).

```bash
python3 scripts/fetch_publications.py            # écrit data/publications_all.json
python3 scripts/fetch_publications.py -v         # détaille chaque fusion de doublons
python3 scripts/fetch_publications.py --dry-run  # affiche le bilan sans rien écrire
python3 scripts/fetch_publications.py --since 0  # toutes les années (défaut : depuis 2019)
python3 scripts/test_fetch_publications.py       # tests du dédoublonnage
```

**Doublons.** Un même article est fusionné quand les profils ORCID partagent un DOI (écrit différemment
ou non), quand les titres sont identiques après normalisation, ou quand ils sont quasi identiques
(similarité d'au moins 0,94). Un preprint et sa version publiée donnent une seule entrée : la version
de la revue est gardée et le DOI du preprint est conservé dans `also`. « Part I » et « Part II »
ne sont jamais fusionnés, ni un erratum avec son article. Les paires entre 0,85 et 0,94 ne sont pas
fusionnées : elles sont listées dans `scripts/publications_review.txt` pour relecture.

**Réglages** dans `scripts/publications_config.json` : année minimale (`since`), types exclus
(par défaut les jeux de données), `exclude_dois`, `exclude_title_regex` (par exemple pour masquer
les articles d'un homonyme), `extra_orcids` (un ORCID hors équipe) et `mailto` (votre adresse,
facultative, pour l'API Crossref).

Les réponses de l'API sont gardées 24 h dans `scripts/.cache/`. `--refresh` force une nouvelle requête.
Le script a besoin d'accéder à `pub.orcid.org`. Il doit donc tourner sur votre ordinateur ou sur GitHub.

**Mise à jour automatique.** Le workflow `.github/workflows/update-publications.yml` relance le script
chaque lundi, enregistre le résultat s'il a changé et republie le site. Il peut aussi être lancé à la main
depuis l'onglet *Actions* de GitHub.

## Déployer sur GitHub Pages

1. Créer un dépôt GitHub et y pousser ce dossier (branche `main`).
2. Dans *Settings → Pages*, choisir *Source : GitHub Actions*.
3. Chaque `git push` sur `main` reconstruit et publie le site (workflow `.github/workflows/deploy.yml`).

## Structure

```
hugo.toml            configuration, menu, adresse, liens
content/             pages (Markdown)
data/                équipe, projets, publications, partenaires, chiffres (YAML)
layouts/             gabarits Hugo
assets/css, assets/js   styles et script (menu mobile)
static/              polices, favicon, images
```

## Charte graphique

Teal et corail inspirés de l'IAB, un code couleur par groupe (Metals in Medicine : teal ; X-Ray Wave Imaging : bleu pétrole ; Image-guided Surgery and NCT : corail). Polices Outfit et Source Sans 3, hébergées sur le site (aucun appel à Google Fonts). Mode sombre automatique.
Les couleurs sont définies en tête de `assets/css/main.css`.

## Sources du contenu

Présentation HCERES (13 nov.) et dossier SAB 2025 de l'équipe 3T. Les montants de financement, le SWOT et les informations personnelles ne sont volontairement pas publiés.
