# Audit de robustesse — aperçu de la première page

**Projet** : `abio-docsearch` (`docsearch/preview.py`) · **Date** : 20 novembre 2026
**Version auditée** : commit `7ff36f3`, `preview.py` sha256 `9273a23026c99e95…` (copie figée dans `/tmp/preview-audit/snapshot`)
**Environnement** : Python 3.14.7, PyMuPDF 2.1 (MuPDF 1.28.6), python-docx 1.2.0, openpyxl 3.1.5, python-pptx 1.0.2 — **Linux**, donc branche de repli texte (`allow_office=False`) ; le chemin COM Windows n'a pas été exercé.

## Méthode

- Corpus de 97 fichiers généré par `/tmp/preview-audit/gen_corpus.py` (tableaux simples/fusionnés/imbriqués, titres multi-niveaux, images PNG/JPEG/CMYK/EXIF, en-têtes/pieds, zones de texte, listes, hyperliens, accents et emoji, documents vides, longs tableaux, colonnes, DOCX altérés au niveau du zip, PDF chiffrés/paysage/A0/tronqués, PPTX/XLSX variés, fichiers 0 octet, extensions trompeuses).
- Pour chaque fichier : `docsearch.preview.render(path, allow_office=False)` appelé deux fois (froid puis cache), chronométré, résultat classé (image / texte / vide / erreur / exception) avec titre extrait, longueur de texte et dimensions du tableau.
- Sondes ciblées (`/tmp/preview-audit/probes.py`) : caractérisation du KeyError, cache, noms non UTF-8, accès concurrent, contenus invisibles à l'extraction.
- **Aucun fichier applicatif n'a été modifié.**

> **Note de reproductibilité.** L'arbre de travail a été modifié par un autre processus *pendant* l'audit (`preview.py` réécrit, sha `a33ef891…`, non commité). Pour des résultats stables, tout ce qui suit porte sur la version **committée** (`7ff36f3`), figée dans `/tmp/preview-audit/snapshot` : `AUDIT_PROJECT=/tmp/preview-audit/snapshot python audit.py`. Les écarts constatés sur l'arbre de travail sont signalés en annexe B.

## 1. Tableau des cas testés

Résultat = type d'aperçu produit. « **vide** » = `ok=True` mais page sans texte ni tableau (avec message) ; « **vide (muet)** » = sans aucun message. Temps = premier appel, cache froid.


**PDF**

| Fichier | Résultat | ms | Titre extrait | Texte | Tableau | Observation |
|---|---|---:|---|---:|---|---|
| `MAJUSCULES.PDF` | image | 36.1 | — | 0 | — | suffixe insensible à la casse : OK |
| `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa…` | image | 30.2 | — | 0 | — | — |
| `faux_docx.pdf` | image | 39.2 | — | 0 | — | contenu DOCX nommé .pdf : rendu par MuPDF (image) |
| `faux_jpg.pdf` | image | 32.0 | — | 0 | — | contenu PDF nommé .jpg : rendu correct |
| `lien_pdf.pdf` | image | 27.2 | — | 0 | — | lien symbolique résolu |
| `nom_invalide_\udcff.pdf` | erreur | 0.1 | — | 0 | — | PDF illisible (FileDataError) |
| `pdf_2000pages.pdf` | image | 36.9 | — | 0 | — | seule la page 1 est ouverte (≈40 ms) |
| `pdf_a0_portrait.pdf` | image | 27.8 | — | 0 | — | A0 → 900×1273 en 27 ms |
| `pdf_a3_paysage.pdf` | image | 14.9 | — | 0 | — | conforme |
| `pdf_chiffre_owner.pdf` | image | 28.0 | — | 0 | — | permissions=0 ignorées : rendu quand même |
| `pdf_chiffre_user.pdf` | erreur | 19.9 | — | 0 | — | erreur explicite en 20 ms (bon comportement) |
| `pdf_entete_seul.pdf` | erreur | 0.4 | — | 0 | — | magic PDF + binaire aléatoire : illisible |
| `pdf_mediaBox_zero.pdf` | image | 23.5 | — | 0 | — | MediaBox 0×0 réparée par MuPDF (900×1165) |
| `pdf_paysage_a4.pdf` | image | 15.4 | — | 0 | — | paysage respecté (900×636) |
| `pdf_rotation90.pdf` | image | 14.4 | — | 0 | — | /Rotate 90 respecté (900×636) |
| `pdf_scan_image.pdf` | image | 40.0 | — | 0 | — | page image rendue |
| `pdf_simple.pdf` | image | 26.4 | — | 0 | — | référence, rendu conforme |
| `pdf_tronque.pdf` | erreur | 0.3 | — | 0 | — | PDF illisible (FileDataError) |
| `pdf_zero_octet.pdf` | erreur | 0.1 | — | 0 | — | PDF illisible (EmptyFileError) |
| `sans_extension` | **vide** | 0.2 | sans_extension | 0 | — | détection par suffixe uniquement, pas de sniffing |
| `dossier.pdf` | erreur | 0.1 | — | 0 | — | répertoire : PDF illisible |

**Images**

| Fichier | Résultat | ms | Titre extrait | Texte | Tableau | Observation |
|---|---|---:|---|---:|---|---|
| `faux_png.png` | erreur | 0.6 | — | 0 | — | texte nommé .png : Image illisible |
| `img_1x1.png` | image | 15.2 | — | 0 | — | 1 px → 675×675, sur-agrandissement inutile |
| `img_8000x8000.png` | image | 854.1 | — | 0 | — | 64 Mpx : ≈0,8 s et ~192 Mo de pixels décodés |
| `img_anime.gif` | image | 22.7 | — | 0 | — | 1re image seulement (attendu) |
| `img_corrompu.jpg` | erreur | 0.5 | — | 0 | — | Image illisible (FileDataError) |
| `img_exif_rot90.jpg` | image | 47.7 | — | 0 | — | orientation EXIF appliquée (844×1500) |
| `img_image.bmp` | image | 21.6 | — | 0 | — | — |
| `img_jpeg.jpg` | image | 30.4 | — | 0 | — | — |
| `img_jpeg_cmyk.jpg` | image | 66.6 | — | 0 | — | CMJN converti, rouge correct (242,94,68) |
| `img_multipage.tiff` | image | 35.5 | — | 0 | — | 1re page seulement (attendu) |
| `img_palette.gif` | image | 30.5 | — | 0 | — | — |
| `img_png.png` | image | 29.0 | — | 0 | — | — |
| `img_png_transparent.png` | image | 26.1 | — | 0 | — | alpha composé sur blanc (JPEG) |
| `img_webp.webp` | erreur | 0.5 | — | 0 | — | extension déclarée supportée, PyMuPDF refuse le fichier |
| `img_zero_octet.png` | erreur | 0.1 | — | 0 | — | Image illisible (EmptyFileError) |

**DOCX**

| Fichier | Résultat | ms | Titre extrait | Texte | Tableau | Observation |
|---|---|---:|---|---:|---|---|
| `doc_ancien.doc` | **vide** | 0.1 | doc_ancien | 0 | — | extension déclarée, aucun extracteur |
| `docx_accents_emoji.docx` | texte | 39.8 | Compte rendu — réunion qualité | 207 | — | accents, emoji, ZWJ et sélecteurs conservés |
| `docx_colonnes.docx` | texte | 7.2 | Bulletin en deux colonnes | 1123 | — | texte présent, colonnes aplaties |
| `docx_corrompu.docx` | **vide** | 0.2 | docx_corrompu | 0 | — | PackageNotFoundError |
| `docx_document_xml_casse.docx` | **vide** | 2.1 | docx_document_xml_casse | 0 | — | Aperçu indisponible (XMLSyntaxError). |
| `docx_entete_pied.docx` | texte | 10.5 | Le corps ne contient que ce pa | 39 | — | en-tête et pied ignorés, titre = 1er paragraphe du corps |
| `docx_images.docx` | texte | 4.5 | Rapport photographique | 67 | — | légendes seules, images absentes |
| `docx_liens.docx` | texte | 4.3 | Références externes | 70 | — | texte des hyperliens conservé |
| `docx_listes.docx` | texte | 7.4 | Points de contrôle | 162 | — | puces/numéros perdus (texte à plat) |
| `docx_nom_emoji_🗂️_rapport.docx` | texte | 7.2 | Procédure de vérification | 440 | — | nom de fichier emoji : extraction normale (440 car.) |
| `docx_rel_cassee.docx` | **vide** | 0.6 | docx_rel_cassee | 0 | — | KeyError — voir §2.1 (cause C) |
| `docx_sans_content_types.docx` | **vide** | 0.3 | docx_sans_content_types | 0 | — | KeyError — voir §2.1 (cause A) |
| `docx_sans_rels_racine.docx` | **vide** | 0.4 | docx_sans_rels_racine | 0 | — | KeyError — voir §2.1 (cause B) |
| `docx_sans_styles.docx` | **vide** | 0.5 | docx_sans_styles | 0 | — | KeyError — voir §2.1 (cause C) |
| `docx_sans_styles_ni_rel.docx` | texte | 2.8 | Procédure de vérification | 440 | — | OK : relation retirée, plus de KeyError |
| `docx_simple.docx` | texte | 7.0 | Procédure de vérification | 440 | — | référence (440 car.) |
| `docx_style_absent.docx` | texte | 7.1 | Procédure de vérification | 440 | — | pStyle inconnu : sans effet sur l'extraction |
| `docx_tableau_fusion.docx` | texte | 7.1 | Tableau avec fusions | 20 | — | tableau invisible (20 car.) |
| `docx_tableau_imbrique.docx` | texte | 7.0 | Tableau imbriqué | 16 | — | tableau imbriqué invisible (16 car.) |
| `docx_tableau_long.docx` | texte | 10.2 | docx_tableau_long | 559 | — | 14 lignes sur 200, sans mention de troncature |
| `docx_tableau_simple.docx` | texte | 7.9 | Matrice de traçabilité | 22 | — | TABLEAU INVISIBLE : 22 car. (titre seul) |
| `docx_texte_300k.docx` | texte | 9.0 | Début mot mot mot mot mot mot  | 3000 | — | tronqué à 3000 car. (attendu) |
| `docx_titres_multi.docx` | texte | 7.1 | Manuel qualité | 578 | — | — |
| `docx_tronque.docx` | **vide** | 0.2 | docx_tronque | 0 | — | PackageNotFoundError |
| `docx_vide.docx` | **vide (muet)** | 7.1 | docx_vide | 0 | — | page vide, aucun message |
| `docx_zero_octet.docx` | **vide** | 0.1 | docx_zero_octet | 0 | — | PackageNotFoundError |
| `docx_zip_vide.docx` | **vide** | 0.2 | docx_zip_vide | 0 | — | KeyError — voir §2.1 (cause A) |
| `docx_zone_texte.docx` | **vide (muet)** | 7.1 | docx_zone_texte | 0 | — | TEXTE EN ZONE DE TEXTE PERDU, page vide sans message |
| `faux_pdf.docx` | **vide** | 0.3 | faux_pdf | 0 | — | contenu PDF nommé .docx : PackageNotFoundError |

**PPTX**

| Fichier | Résultat | ms | Titre extrait | Texte | Tableau | Observation |
|---|---|---:|---|---:|---|---|
| `odp_diaporama.odp` | **vide** | 0.1 | odp_diaporama | 0 | — | extension déclarée, aucun extracteur |
| `ppt_ancien.ppt` | **vide** | 0.2 | ppt_ancien | 0 | — | extension déclarée, aucun extracteur |
| `pptx_corrompu.pptx` | **vide** | 51.4 | pptx_corrompu | 0 | — | PackageNotFoundError (51 ms) |
| `pptx_image_seule.pptx` | **vide (muet)** | 3.6 | pptx_image_seule | 0 | — | slide image seule → page vide sans message |
| `pptx_simple.pptx` | texte | 2.8 | Revue de direction | 36 | — | titre + corps conformes |
| `pptx_tableau.pptx` | **vide (muet)** | 2.4 | pptx_tableau | 0 | — | TABLEAU PPTX NON EXTRAIT (GraphicFrame ignoré) |
| `pptx_titre_seul.pptx` | texte | 2.4 | Titre sans corps | 16 | — | — |
| `pptx_vide.pptx` | **vide (muet)** | 2.2 | pptx_vide | 0 | — | 0 diapositive → page vide |
| `pptx_zero_octet.pptx` | **vide** | 0.2 | pptx_zero_octet | 0 | — | PackageNotFoundError |

**XLSX/CSV/ODS**

| Fichier | Résultat | ms | Titre extrait | Texte | Tableau | Observation |
|---|---|---:|---|---:|---|---|
| `csv_simple.csv` | **vide** | 0.2 | csv_simple | 0 | — | AUCUNE EXTRACTION : .csv déclaré mais non traité |
| `ods_simple.ods` | **vide** | 74.7 | ods_simple | 0 | — | InvalidFileException : openpyxl ne lit pas ODS |
| `xls_ancien.xls` | **vide** | 0.1 | xls_ancien | 0 | — | extension déclarée, aucun extracteur |
| `xlsx_20000lignes.xlsx` | texte | 8.4 | xlsx_20000lignes | 0 | 16×6 | 15 ms, seules 16 lignes lues |
| `xlsx_corrompu.xlsx` | **vide** | 0.2 | xlsx_corrompu | 0 | — | BadZipFile |
| `xlsx_feuille_cachee.xlsx` | texte | 2.3 | xlsx_feuille_cachee | 0 | 1×6 | FEUILLE MASQUÉE PRÉVISUALISÉE (« CONFIDENTIEL ») |
| `xlsx_formules.xlsx` | texte | 2.0 | xlsx_formules | 0 | 1×6 | FORMULES → CELLULES VIDES (data_only, pas de cache) |
| `xlsx_fusion_6plus.xlsx` | texte | 2.2 | xlsx_fusion_6plus | 0 | 6×6 | 6 colonnes max, fusions aplaties |
| `xlsx_multi_feuilles.xlsx` | texte | 2.1 | xlsx_multi_feuilles | 0 | 2×6 | 1re feuille seulement (Sommaire) |
| `xlsx_simple.xlsx` | texte | 2.4 | xlsx_simple | 0 | 15×6 | plafond 16 lignes × 6 colonnes, conforme |
| `xlsx_une_cellule.xlsx` | texte | 1.7 | xlsx_une_cellule | 0 | 1×6 | ligne conservée (C7) |
| `xlsx_zero_octet.xlsx` | **vide** | 0.2 | xlsx_zero_octet | 0 | — | BadZipFile |

**Autres / pièges**

| Fichier | Résultat | ms | Titre extrait | Texte | Tableau | Observation |
|---|---|---:|---|---:|---|---|
| `json_simple.json` | texte | 0.2 | {"document": "CR-2026-014", "s | 68 | — | contenu brut (pas de mise en forme) |
| `md_simple.md` | texte | 0.2 | # Procédure | 54 | — | markdown brut |
| `odt_document.odt` | **vide** | 0.2 | odt_document | 0 | — | extension déclarée, aucun extracteur |
| `rtf_document.rtf` | **vide** | 0.1 | rtf_document | 0 | — | extension déclarée, aucun extracteur |
| `txt_2mo.txt` | texte | 0.2 | ligne de journal — xxxxxxxxxxx | 3000 | — | tronqué à 3000 car. (attendu) |
| `txt_binaire.txt` | texte | 0.2 |           | 3000 | — | octets de contrôle (NUL…) affichés bruts |
| `txt_latin1.txt` | texte | 0.1 | Rapport qualit� � mesures � 25 | 37 | — | CP1252 → U+FFFD (accents remplacés) |
| `txt_simple.txt` | texte | 0.1 | Note interne | 53 | — | — |
| `txt_zero_octet.txt` | **vide (muet)** | 0.1 | txt_zero_octet | 0 | — | page vide, aucun message |
| `xml_config.xml` | texte | 0.1 | <?xml version="1.0"?> | 58 | — | XML brut |

Synthèse sur 97 passages (96 fichiers distincts, le cas au nom non UTF-8 étant exécuté deux fois) : 25 images, 32 pages texte, 23 pages vides annotées, **6 pages vides sans message**, 11 erreurs explicites, **0 exception échappée** — mais 2 exceptions échappent à `render()` dans les sondes ciblées (§2.2). 37 fichiers ont un titre réduit au nom de fichier.

## 2. Erreurs et dégradations

### 2.1 KeyError à la lecture DOCX — 5 fichiers, 3 causes distinctes

Symptôme utilisateur : page vide avec la note `Aperçu indisponible (KeyError).` — le message d'origine est perdu. Trois altérations différentes produisent ce même `KeyError`, toutes dans le chargeur OPC de python-docx :

**Cause A — archive sans `[Content_Types].xml`** (`docx_zip_vide.docx`, `docx_sans_content_types.docx`)

```
File "docx/opc/pkgreader.py", in _load_serialized_parts
File ".../zipfile/__init__.py", line 1655, in getinfo
    raise KeyError('There is no item named %r in the archive' % name)
KeyError: "There is no item named '[Content_Types].xml' in the archive"
```

**Cause B — `_rels/.rels` absent ou sans relation `officeDocument`** (`docx_sans_rels_racine.docx`)

```
File "docx/opc/package.py", line 137, in part_related_by
    return self.rels.part_with_reltype(reltype)
File "docx/opc/rel.py", line 98, in _get_rel_of_type
    raise KeyError(tmpl % reltype)
KeyError: "no relationship of type '…/relationships/officeDocument' in collection"
```

**Cause C — relation pointant vers une partie absente du zip** (`docx_sans_styles.docx`, `docx_rel_cassee.docx`)

```
File "docx/opc/pkgreader.py", in _walk_parts        # parcourt TOUTES les relations
File ".../zipfile/__init__.py", line 1655, in getinfo
KeyError: "There is no item named 'word/styles.xml' in the archive"
```

Reproduction minimale de la cause C :

```bash
python - <<'PY'
import zipfile
src = "docx_simple.docx"
z = zipfile.ZipFile(src)
parts = {n: z.read(n) for n in z.namelist()}
parts.pop("word/styles.xml")          # la relation subsiste dans document.xml.rels
with zipfile.ZipFile("casse.docx", "w", zipfile.ZIP_DEFLATED) as out:
    for n, d in parts.items():
        out.writestr(n, d)
PY
```

Points importants :

- `docx_sans_styles_ni_rel.docx` (styles.xml **et** sa relation supprimés) est lu **sans erreur** : c'est bien la relation orpheline qui tue, pas l'absence du style. Le style référencé mais inexistant (`docx_style_absent.docx`) est également sans effet — `paragraph.text` ne consulte jamais `styles`.
- L'échec est **mémorisé en cache** : la note `KeyError` est réaffichée à chaque clic, indéfiniment, tant que le fichier ne change pas (§2.8).
- Le diagnostic est impossible côté utilisateur : `preview.py:337-338` ne conserve que `type(exc).__name__`, jamais `str(exc)` ni la partie fautive.
- **Conséquence produit** : un document de conteneur incohérent — mais dont le corps `word/document.xml` est intact et lisible — est présenté comme illisible, alors que le texte est extractible sans styles ni relations (cf. §4, recommandation 1).

### 2.2 Deux exceptions qui s'échappent de `render()`

`render()` ne protège que l'écriture finale du cache par `except OSError` (`preview.py:413-421`) et la création du répertoire de cache n'est pas protégée du tout.

**a) Nom de fichier non UTF-8 (octet `\xff` ou `\xfe`, cas des partages SMB/rsync) — reproduit sur `.txt`, `.md` et `.docx` :**

```
File "preview.py", line 419, in render
    meta_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
UnicodeEncodeError: 'utf-8' codec can't encode character '\udcff' in position 187: surrogates not allowed
```

L'extraction a réussi, c'est l'écriture du JSON qui échoue ; `UnicodeEncodeError` est une `ValueError`, pas une `OSError`, donc elle n'est pas rattrapée. Trois formats mesurés (`.txt`, `.md`, `.docx`) échouent identiquement ; par construction, tous les formats routés vers `_text_preview` sont touchés (`.json`, `.xlsx`…). Les images et PDF ne cassent pas : MuPDF refuse d'abord ces chemins, et le JSON des images est sérialisé avec `ensure_ascii=True` (`preview.py:417`).

**b) Cache disque non inscriptible (disque plein, profil verrouillé, antivirus) :**

```
File "preview.py", line 61, in _cache_paths
    shard.mkdir(parents=True, exist_ok=True)
PermissionError: [Errno 13] Permission denied: '…/previews/a5'
```

Toute la prévisualisation devient impossible alors que le cache est un pur confort. Variante testée : `DOCSEARCH_DATA` pointant sur un fichier → `FileExistsError` dès `paths.data_dir()`.

Dans l'application, `Api.preview()` (`app.py:93-97`) rattrape tout et affiche l'erreur brute ; l'auto-test embarqué (`selftest.py:74`) appelle `render()` sans filet.

### 2.3 Extensions annoncées mais sans extracteur

`WORD_EXTS`, `EXCEL_EXTS` et `PPT_EXTS` contiennent `.doc`, `.rtf`, `.odt`, `.csv`, `.xls`, `.ppt`, `.odp`, mais `_text_preview()` ne traite que `.docx/.docm`, `.pptx/.pptm` et `.xlsx/.xlsm/.ods`. Résultat : `Aperçu indisponible pour ce format.` pour **`.csv`, `.doc`, `.xls`, `.ppt`, `.rtf`, `.odt`, `.odp`** — alors que `.csv`/`.rtf` seraient triviaux à lire et que `.doc/.xls/.ppt` sont les formats d'archive les plus fréquents dans un fonds documentaire ancien.

`ods_simple.ods` est bien routé vers openpyxl (`.ods` dans l'ensemble) mais échoue : `InvalidFileException` — openpyxl ne lit pas l'ODF.

Le cas `sans_extension` (PDF sans suffixe) montre que la détection repose **uniquement sur le suffixe** : aucun « sniffing » de signature.

### 2.4 Tableaux DOCX invisibles

`_docx_text()` ne bascule sur les tableaux que si **aucun** paragraphe n'a été trouvé (`preview.py:271`). Dès qu'un titre ou une ligne de texte existe, les tableaux disparaissent de l'aperçu :

| Fichier | Contenu réel | Texte extrait |
|---|---|---|
| `docx_tableau_simple.docx` | titre + tableau 5×3 | `Matrice de traçabilité` (22 car.) |
| `docx_tableau_fusion.docx` | paragraphe + tableau fusionné 6×4 | `Tableau avec fusions` (20 car.) |
| `docx_tableau_imbrique.docx` | paragraphe + tableau imbriqué | `Tableau imbriqué` (16 car.) |
| `docx_tableau_long.docx` | tableau 200×4 seul | 14 lignes / 200, sans mention de troncature |

Pour un compte rendu qualité (matrice de traçabilité, résultats de mesures), c'est le contenu le plus utile qui manque. Le plafond 14 lignes × 2 tableaux n'est jamais signalé à l'utilisateur.

### 2.5 XLSX : quatre pièges silencieux

| Cas | Comportement | Risque |
|---|---|---|
| `xlsx_formules.xlsx` | `data_only=True` + aucune valeur calculée en cache → cellules **vides** (`[['Total','','','','','']]`) | Un tableau généré par openpyxl/pandas paraît vide |
| `xlsx_feuille_cachee.xlsx` | `wb.sheetnames[0]` ignore l'état `hidden` → aperçu de « CONFIDENTIEL — ne pas diffuser » | Fuite d'une feuille volontairement masquée |
| première feuille = graphique (`chartsheet`) | `AttributeError` → `Aperçu indisponible (AttributeError).` | Fichier « tableau de bord » courant |
| `xlsx_fusion_6plus.xlsx` | plafond 6 colonnes / 16 lignes, non signalé | Colonnes G, H invisibles sans avertissement |

### 2.6 `.webp` déclaré, jamais lisible

`IMAGE_EXTS` contient `.webp` ; le `.webp` du corpus (PIL, 700×500) donne `ok=False`, `Image illisible (FileDataError)` en 0,5 ms. La build PyMuPDF 1.28.6 ne décode pas ce format : l'extension est annoncée par le code mais absente du moteur.

### 2.7 Fichiers vides : comportement incohérent et pages muettes

- 0 octet en `.docx/.xlsx/.pptx` → `ok=True`, `kind=text`, note `PackageNotFoundError`/`BadZipFile` : l'interface affiche une **feuille blanche** « reconstruite » au lieu d'une erreur.
- 0 octet en `.pdf/.png` → `ok=False`, erreur explicite. Deux traitements pour la même situation.
- 0 octet en `.txt` → `ok=True`, texte vide, **note vide** : page blanche totalement muette.
- Même page muette, sans fichier vide, pour `docx_vide.docx`, `docx_zone_texte.docx`, `pptx_image_seule.pptx`, `pptx_tableau.pptx`, `pptx_vide.pptx` — soit 6 cas où l'utilisateur ne peut pas distinguer « document vide » de « aperçu qui a échoué ».
- **Office chiffré par mot de passe** (conteneur OLE) : `.xlsx` → `BadZipFile`, `.docx`/`.pptx` → `PackageNotFoundError`. Le message fait croire à un fichier corrompu, alors qu'il est simplement protégé — un cas très fréquent en entreprise.

### 2.8 Cache disque : cinq défauts

1. **Image corrompue servie sans contrôle** : en remplaçant le `.jpg` du cache par des octets arbitraires, `render()` renvoie `ok=True`, `cached=True` et ces octets en base64 (`CECI-NEST-PAS-UN-JPEG`). Aucune validation, aucune régénération.
2. **Échec mémorisé** : `Aperçu indisponible (KeyError).` est stocké (`ok=True` suffit à écrire le JSON) et resservi à chaque ouverture.
3. **Note dépendante de la plateforme mémorisée** : `Rendu Office non disponible sur cette plateforme.` est mise en cache ; un dossier `data/` copié sous Windows continuera d'afficher des aperçus texte au lieu du rendu COM.
4. **Péremption par `mtime` seule** : `_cache_key` = `path|taille|mtime_ns` (`preview.py:47-49`). Un contenu réécrit à taille identique avec `mtime` restauré (`rsync -t`, écrasement réseau) donne un **aperçu périmé** servi avec `cached=True` — déduit du code, non reproduit ici (il faut deux fichiers de même taille).
5. **Aucune éviction** : `preview_cache_mb` (800 Mo par défaut, `paths.py:42`) n'est lu nulle part ; `cache_size()` ne sert qu'à l'affichage. Le cache grossit jusqu'au nettoyage manuel. Ordre de grandeur mesuré : 676 Ko pour 95 aperçus (≈7 Ko/fichier), soit ≈700 Mo pour un fonds de 100 000 documents.

### 2.9 Performance et mémoire

| Cas | Temps | Commentaire |
|---|---|---|
| `img_8000x8000.png` (64 Mpx) | **815–855 ms** | décodage complet en mémoire (~192 Mo de pixels) avant réduction à 900×900 |
| `pdf_2000pages.pdf` | 37–46 ms | seule la page 1 est ouverte : bon comportement |
| `pdf_a0_portrait.pdf` | 27–31 ms | 900×1273, correct |
| `xlsx_20000lignes.xlsx` | 15 ms | `max_row=20` : bon comportement |
| `ods_simple.ods` | ≈75 ms (puis échec) | temps perdu avant l'erreur |
| `pdf_chiffre_user.pdf` | 20 ms | erreur explicite « Document protégé par mot de passe » |

Aucune limite de taille n'est appliquée avant décodage : une image de 200 Mpx ou un PDF à page unique de 10 000×10 000 pt peut saturer la mémoire du processus (PyMuPDF alloue large avant de réduire).

### 2.10 Contenus perdus par l'extraction (dégradations silencieuses)

- **Zone de texte** (`docx_zone_texte.docx`) : seul contenu du document, **totalement perdu** → page vide, titre = nom de fichier.
- **Content control** (`w:sdt`, modèles Word) : texte perdu (`doc.paragraphs` ne descend pas dedans).
- **En-tête / pied** : jamais lus (`docx_entete_pied.docx` : le titre officiel de l'en-tête est ignoré, le titre affiché devient la 1re phrase du corps).
- **Tableau PPTX** : `GraphicFrame` sans `text_frame` → `pptx_tableau.pptx` donne une page vide alors que la diapositive est pleine de texte.
- **Forme groupée PPTX** : le texte du groupe est perdu (seules les formes de premier niveau sont lues).
- **Image seule en 1re diapositive** (`pptx_image_seule.pptx`) : page vide, aucune note explicative.
- **Listes à puces** : puces et numérotation perdues, texte à plat (`docx_listes.docx`).
- **CP1252** (`txt_latin1.txt`) : décodé en UTF-8 avec `errors="replace"` → `Rapport qualit�` (U+FFFD). Un essai `cp1252` en repli corrigerait les fonds documentaires français.
- **Octets binaires** (`txt_binaire.txt`) : NUL et caractères de contrôle affichés bruts dans la page d'aperçu.

À l'inverse, plusieurs points sont **corrects** et ne demandent rien : accents et emoji (y compris ZWJ et sélecteurs de variante) conservés, texte des hyperliens extrait, orientation EXIF appliquée, CMJN converti sans inversion, `/Rotate 90` respecté, PDF protégé détecté proprement, formats macro `.docm/.xlsm/.pptm` lus.

## 3. Causes racines probables

1. **Confiance implicite dans python-docx** : `Document(path)` est supposé réussir sur tout fichier `.docx`. Or son chargeur OPC lève `KeyError` (et non une exception dédiée) sur trois incohérences de conteneur que Word, lui, répare à l'ouverture (comportement connu, non vérifié ici faute d'Office). C'est la cause du KeyError signalé.
2. **Extraction qui ne parcourt qu'un seul axe du document** : `doc.paragraphs` (corps), `slide.shapes` (premier niveau), `ws.iter_rows` (une feuille) — tout ce qui vit dans une relation indirecte (en-tête, zone de texte, content control, groupe, tableau, graphique) est hors champ, sans que le code le sache.
3. **Tableaux traités comme un cas de secours et non comme du contenu** : le `if not lines` transforme « document sans paragraphe » en « document sans contenu ».
4. **Couverture d'erreurs partielle** : `except Exception` autour de l'extraction (bien) mais `except OSError` seulement autour du cache (insuffisant : `ValueError`, `FileExistsError` de `mkdir`, `UnicodeEncodeError`).
5. **Erreurs réduites au nom de classe** : `f"Aperçu indisponible ({type(exc).__name__})."` jette message, partie fautive et traceback — d'où un KeyError non diagnosticable en production.
6. **Cache sans contrat de validité** : clé = `mtime`+taille, contenu jamais vérifié, échecs mis en cache, aucune politique d'éviction, note dépendante de l'OS figée dans le cache.
7. **Ensembles d'extensions et branches de traitement désynchronisés** : trois constantes promettent neuf formats que `_text_preview` ne sait pas lire.
8. **Décision prise sur le seul suffixe** : un PDF sans extension ou un `.xls` qui est un `.xlsx` ne sont jamais reconnus, alors que MuPDF sait ouvrir par signature.

## 4. Recommandations

**Bloquantes (à corriger avant diffusion)**

1. **Lecture DOCX tolérante.** Envelopper `Document(path)` et, en cas d'échec, extraire le texte directement du zip (`word/document.xml` + `word/header*.xml`) avec `xml.etree.ElementTree`, en itérant sur tous les `w:t` — sans dépendre des relations ni des styles. Un repli d'une vingtaine de lignes couvre les trois causes A/B/C (c'est ce que fait déjà la réécriture en cours, annexe B) et produit le même texte que python-docx sur les fichiers sains.
2. **Ne jamais perdre le message d'erreur.** Remplacer `type(exc).__name__` par `f"{type(exc).__name__}: {exc}"` (tronqué à ~120 car.) et journaliser la traceback complète dans un fichier de log. Sans cela, aucun incident de terrain n'est diagnosticable.
3. **Encadrer tout le cache.** Mettre `_cache_paths`/écriture/lecture sous un unique `try/except (OSError, ValueError)` et traiter l'échec de cache comme non bloquant (retourner l'aperçu calculé, éventuellement avec `cached=False`). Corriger le `UnicodeEncodeError` en sérialisant avec `ensure_ascii=True` (ou `errors="backslashreplace"`).
4. **Ne pas mettre en cache les échecs.** N'écrire le JSON que si `ok` **et** extraction non vide ; sinon retenter au prochain clic (un fichier peut être en cours de copie).

**Haute priorité**

5. **Traiter les tableaux comme du contenu** : après les paragraphes, ajouter systématiquement les tableaux du corps (pas seulement en l'absence de paragraphes), avec un marqueur lisible `[tableau N×M]` et la mention « … N lignes supplémentaires » quand le plafond est atteint.
6. **Compléter les extracteurs déjà promis** : `.csv` via `csv.reader` (sniffer `;`/`,`, encodage `utf-8-sig` puis `cp1252`), `.ods` via `zipfile` + `content.xml` (même approche que le repli DOCX), `.rtf` par décodage des groupes `\uN`/`\'xx`. Pour `.doc/.xls/.ppt`, soit implémenter un minimum (OLE2 + `WordDocument` est coûteux), soit **retirer ces extensions des ensembles** et afficher un message honnête du type « format hérité non prévisualisable ».
7. **XLSX** : `data_only=False` puis, si la valeur est une formule (`=…`), afficher la formule elle-même plutôt qu'une cellule vide ; itérer sur toutes les feuilles visibles (`sheet_state == "visible"`) et **exclure les feuilles masquées** ; ignorer proprement les `chartsheet` (`ws.__class__.__name__`) ; signaler la troncature au-delà de 6 colonnes.
8. **Zone de texte et en-tête** : extraire aussi `w:txbxContent` et `w:headerReference`/`w:footerReference` (un titre d'en-tête est souvent le vrai titre du document), et `w:sdtContent`.
9. **PPTX** : parcourir récursivement `GroupShape` et extraire le texte des `GraphicFrame` contenant un tableau (`shape.has_table`).
10. **`.webp`** : vérifier la capacité réelle du moteur au démarrage (`pymupdf.Pixmap(...)` ou liste des formats) ou retirer l'extension de `IMAGE_EXTS` ; à défaut, repli Pillow si présent.
11. **Page vide** : quand le texte et le tableau sont vides, renvoyer une note explicite (« Aucun texte détecté — document peut-être scanné ou composé d'images ») et, pour les PDF, **chercher la première page non vide** plutôt que d'afficher une page de garde blanche.

**Robustesse / exploitation**

12. **Détection par signature** en complément du suffixe : `%PDF`, `PK\x03\x04` + `[Content_Types].xml`, `\x89PNG`, `\xff\xd8\xff`, `II*\x00`/`MM\x00*`, `RIFF….WEBP`. Corrige `sans_extension`, les extensions mensongères et les fichiers renommés.
13. **Garde-fous mémoire** : refuser (ou réduire) le rendu au-delà de ~50 Mpx et de ~30 000 pt de côté, avec `page.get_pixmap(clip=…)` pour ne rasteriser que la zone utile ; mesurer avec `pymupdf.TOOLS.set_memory_limit()` si nécessaire.
14. **Éviction du cache** : appliquer `preview_cache_mb` (LRU par `mtime` des fichiers de cache, purge au démarrage ou tous les N aperçus) ; écrire les fichiers via `tempfile` + `os.replace` pour éviter les JSON tronqués en cas d'arrêt brutal.
15. **Tests de non-régression** : ajouter au corpus de `tests/test_preview.py` au moins `docx sans styles.xml + relation`, `DOCX vide`, `XLSX avec formules`, `XLSX feuille masquée+1re`, `PDF chiffré`, `image 1×1` et un cas de nom non UTF-8, avec assertion sur `ok`, `kind`, et absence d'exception.

## 5. Résumé

1. Le KeyError des DOCX est reproduit : il vient de python-docx, qui lève `KeyError` sur trois incohérences de conteneur zip (styles.xml manquant avec relation orpheline, `_rels/.rels` absent, `[Content_Types].xml` absent) — alors que le corps `word/document.xml` reste parfaitement lisible dans les trois cas.
2. Cinq fichiers du corpus déclenchent ce KeyError, et l'application n'en conserve que le nom de classe : « Aperçu indisponible (KeyError). », sans partie fautive ni message.
3. Deux exceptions échappent réellement à `render()` : `UnicodeEncodeError` sur un nom de fichier non UTF-8 et `PermissionError` quand le cache est en lecture seule ; les échecs sont en outre mémorisés et une image de cache corrompue est servie sans contrôle.
4. Les dégradations silencieuses sont plus nombreuses que les erreurs : tableaux DOCX/PPTX ignorés, zones de texte et en-têtes perdus, formules XLSX vides, feuille masquée affichée, `.csv/.doc/.rtf/.odt` promis mais non lus, pages blanches muettes.
5. Priorités : repli XML direct pour DOCX, message d'erreur complet, cache non bloquant et non persistant sur échec, extraction des tableaux systématique ; le reste (extracteurs promis, signature, garde-fous mémoire, éviction) consolide sans urgence.

## Limites du test (non testé)

| Point | Raison |
|---|---|
| Chemin COM / rendu Office réel (`allow_office=True`) | Windows + Office requis, indisponibles sur cette machine |
| `.doc`, `.xls`, `.ppt` **binaires réels** | aucun outil de production OLE2 dans le venv (les cas testés sont des conteneurs factices) |
| DOCX avec suivi de modifications et commentaires | python-docx ne sait pas les écrire |
| XLSX/PPTX chiffrés « proprement » (AES OOXML) | `msoffcrypto` absent ; testé seulement via un conteneur OLE |
| `.heic`, `.pages`, `.msg`, `.eml` | générateurs absents — hors périmètre des extensions déclarées, sauf `.msg`/`.eml` qui ne sont pas gérés du tout |
| Chemins réseau SMB, fichiers « cloud-only » OneDrive, `MAX_PATH` Windows | comportements dépendants de Windows |
| PDF signés, PDF/A, pièces jointes PDF | non générables sans outillage dédié (sans impact attendu sur le rendu page 1) |

---

## Annexe A — fichiers produits

| Chemin | Rôle |
|---|---|
| `/tmp/preview-audit/gen_corpus.py` | générateur du corpus (97 entrées ; 2 cas non générables documentés dans `gen_report.txt`) |
| `/tmp/preview-audit/corpus/` | corpus généré |
| `/tmp/preview-audit/audit.py` | balayage complet, écrit `results.json` |
| `/tmp/preview-audit/probes.py` | sondes ciblées (KeyError, cache, concurrence), écrit `probes.json` |
| `/tmp/preview-audit/results.json`, `probes.json` | données brutes (résultats, traces d'exception) |
| `/tmp/preview-audit/snapshot/` | copie figée du commit audité |
| `/tmp/preview-audit/shots/` | captures de contrôle (rendu MuPDF des DOCX/PPTX/XLSX, CMJN, EXIF) |

Rien n'a été écrit hors de `/tmp` et de `docs/rapport-apercus.md`.

## Annexe B — état de l'arbre de travail pendant l'audit

Pendant l'audit, `docsearch/preview.py` a été réécrit par un autre processus (non commité au moment de la rédaction, sha `a33ef891…`). Sur cette version, quatre des cinq cas du §2.1 **ne produisent plus de KeyError** (`docx_sans_styles`, `docx_rel_cassee`, `docx_sans_content_types`, `docx_sans_rels_racine` : repli XML direct), les tableaux DOCX sont extraits (`docx_tableau_simple` passe de 22 à 230 caractères) et une note « Aucun texte détecté » apparaît. Restent non traités : archive totalement vide (`docx_zip_vide` → toujours `KeyError`), `.csv` et formats hérités, formules XLSX, feuille masquée, `.webp`, cache corrompu/périmé. Les constats du §2 portent sur le commit `7ff36f3`, pas sur cette réécriture.
