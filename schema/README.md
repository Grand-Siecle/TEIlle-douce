# Le schéma du projet

`alto2tei.odd` est la description normative de ce que la chaîne ALTO2TEI
émet. Les trois autres fichiers en sont dérivés et régénérés par
`scripts/build_odd.py` — ne les modifiez jamais à la main.

| Fichier | Rôle |
|---|---|
| `alto2tei.odd` | **la source** : un document TEI décrivant la personnalisation |
| `alto2tei.rng` | modèle de contenu, appliqué par lxml |
| `alto2tei.sch` | contraintes Schematron, forme lisible |
| `alto2tei.svrl.xsl` | les mêmes, précompilées, forme exécutable |

## Pourquoi

Le schéma de référence du projet était `tei_all` : la totalité des
Guidelines, six cents éléments, dont la chaîne en émet 98. Y valider ne dit
donc presque rien — un `<zone type="Coquille">` inventé par erreur, une
`<figure>` sans ancrage ou une entité automatique sans indice de certitude y
passent tous. `tei_all` répond à « est-ce du TEI ? » ; cet ODD répond à
« est-ce cette édition-ci ? ». Les deux validations restent disponibles.

L'ODD contraint sur trois couches :

1. **Inventaire fermé** — les `<moduleRef include="…">` n'importent que les
   éléments effectivement émis, et la contrainte `inventaire-ferme` referme
   la porte que l'import entier de `core` laisse ouverte (voir plus bas).
2. **Listes de valeurs fermées** — `zone/@type` sur la taxonomie SegmOnto de
   `src/constants.py`, `rs/@type` sur `entity_schema.py`, `@resp` sur les
   agents déclarés.
3. **Contraintes Schematron** — les invariants qu'un modèle de contenu ne
   sait pas dire : `<choice>` complet, `@cert` sur toute annotation
   automatique, `@n` de `<language>` avec son unité, pas de césure résiduelle
   dans un `<reg>`, `<langUsage>` sans prose.

Elles sont toutes **locales**. Les invariants qui demandent de parcourir le
document entier — que chaque `@corresp` résolve, que chaque `GraphicZone` ait
sa `<figure>` — restent en Python dans `scripts/validate_tei.py`, qui les
fait en un passage avec des ensembles ; en Schematron ils seraient
quadratiques sur des documents de cent mille éléments.

## Valider

```bash
# schéma du projet : RelaxNG + Schematron
venv/bin/python scripts/validate_tei.py --odd tei_output/*.xml

# tei_all, si vous en avez une copie (non versionnée, ~1 Mo)
venv/bin/python scripts/validate_tei.py --schema tei_all.rng tei_output/*.xml
```

L'E2E valide la sortie contre `alto2tei.rng` à chaque exécution ; la partie
Schematron se saute avec un motif explicite si `saxonche` manque.

## Recompiler

```bash
venv/bin/python scripts/build_odd.py            # après toute modification de l'ODD
venv/bin/python scripts/build_odd.py --check    # vérifie que le dérivé suit la source
venv/bin/python scripts/build_odd.py --refresh  # re-télécharge la boîte à outils
```

Le script matérialise dans `.odd-toolchain/` (non versionné) la TEI P5, les
TEI Stylesheets et SchXslt, à des versions épinglées en tête du script — une
chaîne de compilation qui bouge sous les pieds produirait des diffs de schéma
sans changement d'ODD. Le moteur est SaxonC-HE (`saxonche`, une roue pip :
XSLT 2.0 sans JVM ni ant).

## Deux pièges, payés cher

**Les motifs impossibles.** Un ODD qui n'importe qu'une partie de la TEI vide
des classes de modèle — plus aucun membre de `model.lLike` si l'on n'encode
pas de vers. `odd2relax` les rend par `<notAllowed/>`, ce qui est exact, mais
libxml2 ne les réduit pas : la compilation du schéma tournait encore après
**dix minutes**, et une variante qui compilait rejetait des documents
conformes. `scripts/rng_simplify.py` applique donc les règles de
simplification de la spec RELAX NG (§4.19 et §4.20) avant que lxml ne voie le
schéma — 247 motifs éliminés, compilation ramenée à 0,1 s.

Cela ne suffit pas pour le module `core`, qui reste **importé entier** : lui
seul rend le schéma incompilable même simplifié. D'où la contrainte
`inventaire-ferme`, qui rétablit l'inventaire là où le modèle de contenu ne
peut plus le porter.

**La langue des contraintes.** `extract-isosch` attribue à chaque contrainte
la langue de son premier ancêtre qui en déclare une, et jette celles qui ne
sont pas dans la langue extraite. Un `xml:lang="fr"` sur la racine de l'ODD
suffit à vider le Schematron de toutes nos règles — sans erreur, sans
avertissement. La prose française est donc marquée sur ses `<div>`, jamais
au-dessus du `<schemaSpec>`, et `tests/test_odd.py` vérifie que chaque
contrainte écrite dans l'ODD se retrouve dans le Schematron produit.

## Modifier l'ODD

1. Éditer `alto2tei.odd`.
2. `venv/bin/python scripts/build_odd.py`.
3. `venv/bin/python -m pytest tests/test_odd.py tests/test_e2e_pipeline.py`.
4. Committer la source **et** les trois dérivés.
