from pathlib import Path
import re
import sys
from time import perf_counter
from zipfile import ZipFile
from collections import defaultdict

import pandas as pd
from lxml import etree
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.console import Console

from src.build import TEI
from src.teiheader_build import teiheader

console = Console()

# Dossiers
OCR_DIR = Path("OCR")
OUTPUT_DIR = Path("tei_output")
OUTPUT_DIR.mkdir(exist_ok=True)

# Métadonnées générales
APP_VERSIONS = {"KRAKEN_VERSION": "4.3.x",
                   "YOLO_VERSION": "8.0.x",
                   "YALTAI_VERSION": "1.0.x"}

MODELS_VERSIONS = {"KRAKEN_MODEL": {"name": "",
                                    "source": ""},
                   "YOLO_MODEL": {"name": "CapricciosaX.pt",
                                  "source": "https://doi.org/10.5281/zenodo.10602196"}
                   }

METADATA_CSV = Path("metadata_livre.csv")

IIIF_URI = {
    "scheme": "https",
    "server": "gallica.bnf.fr",
    "manifest_prefix": "/iiif/ark:/12148/",
    "manifest_suffix": "/manifest.json",
    "image_prefix": "/iiif/ark:/12148",
}

RESPONSIBILITY = {
    "text": "Encodage TEI SegmOnto à partir d’ALTO (pipeline custom).",
    "resp": [
        {
            "forename": "Prénom",
            "surname": "Nom",
            "ptr": {
                "type": "orcid",
                "target": "https://orcid.org/0000-0000-0000-0000",
            },
        }
    ],
    "publisher": "Ton labo / projet",
    "authority": "Ton institution",
    "availability": {"status": "restricted"},
    "licence": {"target": "https://creativecommons.org/licenses/by/4.0/"},
}


# -------- utilitaires --------

def _dd(d: dict | None = None, base: dict | None = None):
    """defaultdict(None) + pré-remplissage -> évite tout KeyError sur ['...']."""
    dd = defaultdict(lambda: None)
    if base:
        dd.update(base)
    if d:
        dd.update(
            {k: (None if str(v).strip().lower() == "nan" else v) for k, v in d.items()}
        )
    return dd


def _val(row, key):
    if row is None:
        return None
    v = row.get(key)
    if v is None:
        return None
    s = str(v).strip()
    return None if s == "" or s.lower() == "nan" else s


def build_config(data_path: Path):
    return {
        "data": {"path": str(data_path)},
        "iiifURI": IIIF_URI,
        "responsibility": RESPONSIBILITY,
        "offline": True,
    }


def write_pretty_xml(root: etree._Element, out_path: Path):
    """
    Écrit le TEI sur disque.
    pretty_print=False -> beaucoup plus rapide pour les gros fichiers.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    etree.ElementTree(root).write(
        str(out_path),
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=True,
    )


def bdd_prefix(doc_folder_name: str) -> str:
    m = re.match(r"([A-Za-z]+?\d+)", doc_folder_name)
    return m.group(1) if m else doc_folder_name


def find_metadata_row(df: pd.DataFrame, key: str):
    if df is None or "BDD" not in df.columns:
        return None
    m = df[df["BDD"].astype(str).str.startswith(key)]
    return None if m.empty else m.iloc[0]


def override_teiheader_from_csv(root: etree._Element, row):
    """
    Injecte les métadonnées issues du CSV dans le <teiHeader>.
    (Version simple, sans prise en compte explicite des namespaces TEI.)
    """
    if row is None:
        return

    def set_text(xpath, value):
        if pd.isna(value) or value in (None, "", "nan"):
            return
        el = root.find(xpath)
        if el is not None:
            el.text = str(value)

    # Champs bibliographiques principaux
    titre = row.get("Titre_long") or row.get("Titre_abrege")
    set_text(".//teiHeader/fileDesc/titleStmt/title", titre)
    set_text(".//teiHeader/fileDesc/sourceDesc/bibl/title", titre)

    # Auteur / imprimeur / libraire / éditeur
    auteur = row.get("ID_auteur")
    imprimeur = row.get("ID_imprimeurs")
    editeur = row.get("ID_Editeur")
    traducteur = row.get("ID_Traducteurs")

    contrib = ", ".join(
        str(x) for x in [auteur, traducteur, imprimeur, editeur] if x and not pd.isna(x)
    )
    if contrib:
        el = root.find(".//teiHeader/fileDesc/titleStmt/author")
        if el is None:
            parent = root.find(".//teiHeader/fileDesc/titleStmt")
            if parent is not None:
                el = etree.SubElement(parent, "author")
        if el is not None:
            el.text = contrib

    # Publication
    set_text(
        ".//teiHeader/fileDesc/sourceDesc/bibl/pubPlace",
        row.get("Lieu_publication"),
    )
    set_text(
        ".//teiHeader/fileDesc/sourceDesc/bibl/publisher",
        editeur or imprimeur,
    )
    set_text(
        ".//teiHeader/fileDesc/sourceDesc/bibl/date",
        row.get("Date_01") or row.get("Date_02"),
    )

    # Cote, localisation
    set_text(
        ".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier/repository",
        row.get("Localisation"),
    )
    set_text(
        ".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier/idno",
        row.get("Cote"),
    )

    # Langue
    set_text(".//teiHeader/profileDesc/langUsage/language", row.get("langues"))

    # Sujet / matière
    sujets = [row.get("Sujet"), row.get("Matiere")]
    sujets = [s for s in sujets if s and not pd.isna(s)]
    if sujets:
        prof = root.find(".//teiHeader/profileDesc")
        if prof is not None:
            text = "; ".join(sujets)
            keywords = etree.SubElement(prof, "keywords")
            term = etree.SubElement(keywords, "term")
            term.text = text

    # ARK et manifest IIIF dans <idno>
    ark = row.get("ARK")
    manifest = row.get("manifest_iiif")
    if ark or manifest:
        idno_parent = root.find(
            ".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier"
        )
        if idno_parent is not None:
            if ark:
                id_ark = etree.SubElement(idno_parent, "idno", type="ark")
                id_ark.text = ark
            if manifest:
                id_manifest = etree.SubElement(idno_parent, "idno", type="iiif")
                id_manifest.text = manifest


# ------- ZIP support -------

def expand_archives(ocr_dir: Path) -> list[Path]:
    """
    Dézippe chaque *.zip sous OCR/ dans un sous-dossier <stem> si nécessaire.
    Retourne la liste des dossiers prêts à être traités (y compris ceux déjà extraits).
    """
    ready_dirs = set()

    # 1) Dézipper ce qui ne l'est pas
    for z in ocr_dir.glob("*.zip"):
        target = ocr_dir / z.stem
        if not target.exists():
            console.print(f"[dim]Extraction : {z.name} → {target.name}/[/dim]")
            target.mkdir(parents=True, exist_ok=True)
            with ZipFile(z) as zf:
                zf.extractall(target)
        ready_dirs.add(target)

    # 2) Inclure aussi les dossiers non-zippés déjà présents
    for d in ocr_dir.iterdir():
        if d.is_dir():
            if any(d.rglob("*.xml")):
                ready_dirs.add(d)

    return sorted(ready_dirs)


# ------- IIIF mapping (dans le dossier extrait) -------

def pick_mapping_csv(doc_dir: Path, alto_files: list[Path]) -> Path | None:
    """
    Choisit un CSV de mapping IIIF plausible :
      - nom contenant 'iiif' ou 'mapping'
      - taille raisonnable
      - au moins 3 colonnes
      - la 3e colonne correspond à des noms de fichiers ALTO
    """
    csvs = []
    for p in doc_dir.glob("*.csv"):
        name = p.name.lower()
        if not ("iiif" in name or "mapping" in name):
            continue
        # éviter d'avaler un monstre de 200 Mo juste pour rien
        if p.stat().st_size > 5_000_000:
            continue
        csvs.append(p)

    if not csvs:
        return None

    csvs.sort(key=lambda p: (0 if "iiif" in p.name.lower() else 1, p.name.lower()))
    alto_names = {p.name for p in alto_files}

    for csv_path in csvs:
        try:
            df = pd.read_csv(csv_path, header=None, nrows=5000)
            if df.shape[1] < 3 or df.empty:
                continue
            if any(str(x).strip() in alto_names for x in df.iloc[:, 2].astype(str)):
                return csv_path
        except Exception:
            continue
    return None


def read_local_iiif_mapping(csv_path: Path) -> dict[int, str]:
    """
    Lit un CSV (3 colonnes : URL IIIF, source, nom ALTO) -> {fN:int : url}.
    """
    if not csv_path or not csv_path.exists():
        return {}
    try:
        df = pd.read_csv(csv_path, header=None)
    except Exception:
        return {}

    mapping = {}
    for _, row in df.iterrows():
        try:
            url = str(row[0]).strip()
            alto_name = str(row[2]).strip()
            m = re.search(r"f(\d+)", alto_name)
            if m:
                mapping[int(m.group(1))] = url
        except Exception:
            continue
    return mapping


def add_surface_facs_from_mapping(root: etree._Element, mapping_page_to_url: dict[int, str]):
    """
    Ajoute @facs sur <surface> à partir d'un mapping {folio:int -> url}.
    NOTE : avec les xml:id en UUID, cette fonction ne trouvera un match
    que si les surfaces portent encore un identifiant de type 'fN'.
    À adapter si besoin.
    """
    if not mapping_page_to_url:
        return

    ns_xml = "{http://www.w3.org/XML/1998/namespace}id"
    for surf in root.findall(".//sourceDoc/surface"):
        sid = surf.get(ns_xml)
        if not sid:
            continue
        m = re.match(r"f(\d+)$", sid)
        if not m:
            continue
        folio = int(m.group(1))
        url = mapping_page_to_url.get(folio)
        if url:
            surf.set("facs", url)


# --------------- main ---------------

def main():
    if not OCR_DIR.exists():
        console.print(f"[red]Dossier introuvable : {OCR_DIR}[/red]")
        sys.exit(1)

    # Extraction auto des zips
    ready_dirs = expand_archives(OCR_DIR)

    # Collecte des volumes : chaque dossier avec des .xml
    docs: list[tuple[str, list[Path], Path]] = []  # (nom, fichiers xml, dossier)
    for d in ready_dirs:
        xmls = sorted(d.rglob("*.xml"))
        if xmls:
            docs.append((d.name, xmls, d))

    if not docs:
        console.print("[red]Aucun volume ALTO trouvé sous OCR/.[/red]")
        sys.exit(1)

    # Métadonnées globales (optionnel)
    df_meta = None
    if METADATA_CSV.exists():
        try:
            df_meta = pd.read_csv(METADATA_CSV, sep=";")
        except Exception as e:
            console.print(
                f"[yellow]Avertissement : échec de lecture {METADATA_CSV}: {e}[/yellow]"
            )

    config = build_config(OCR_DIR)
    config["perf"] = {
        "skip_glyphs": True,
        "skip_strings": True
    }

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[green]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:

        task_docs = progress.add_task("Traitement des documents", total=len(docs))

        for doc_name, filepaths, doc_dir in docs:
            t0 = perf_counter()
            console.print(f"\n[bold cyan]→ {doc_name}[/bold cyan]")

            # TEI tree de base
            tree = TEI(doc_name, filepaths)
            tree.build_tree()

            # Progress bar par page
            task_pages = progress.add_task(
                f"{doc_name} : pages", total=len(filepaths), visible=True
            )

            # Métadonnées CSV pour ce doc
            row = find_metadata_row(df_meta, bdd_prefix(doc_name)) if df_meta is not None else None
            authors_field = _val(row, "ID_auteur")

            sru_local = {
                "found": row is not None,
                "ark": _val(row, "ARK"),
                "title": _val(row, "Titre_long") or _val(row, "Titre_abrege"),
                "date": _val(row, "Date_01") or _val(row, "Date_02"),
                "publisher": _val(row, "ID_Editeur") or _val(row, "ID_imprimeurs"),
                "place": _val(row, "Lieu_publication"),
                "language": _val(row, "langues"),
                "idno": _val(row, "Cote"),
                "repository": _val(row, "Localisation"),
                "subject": _val(row, "Sujet") or _val(row, "Matiere"),
                "authors": [],
            }

            if authors_field:
                authors_list = [
                    {
                        "xmlid": aid.strip(),
                        "name": aid.strip(),
                        "secondary_name": None,
                        "namelink": None,
                        "primary_name": None,
                        "isni": None,
                    }
                    for aid in str(authors_field).split("|")
                    if aid.strip()
                ]
                sru_local["authors"] = authors_list

            iiif_local = {
                "manifest": _val(row, "manifest_iiif"),
                "ark": _val(row, "ARK"),
                "Creator": _val(row, "ID_auteur"),
                "Title": _val(row, "Titre_long") or _val(row, "Titre_abrege"),
                "Date": _val(row, "Date_01") or _val(row, "Date_02"),
                "Publisher": _val(row, "ID_Editeur") or _val(row, "ID_imprimeurs"),
                "Place": _val(row, "Lieu_publication"),
                "Extent": _val(row, "Format"),
            }

            metadata = {
                "sru": _dd(sru_local, base={"found": False}),
                "iiif": _dd(iiif_local),
            }

            tree.metadata = metadata

            # Construction du teiHeader (sans requêtes SRU/IIIF réseau)
            tree.root, tree.segmonto_zones, tree.segmonto_lines = teiheader(
                tree.metadata,
                tree.d,
                tree.root,
                len(tree.fp),
                config,
                KRAKEN_VERSION,
                tree.fp,
                tree.segmonto_zones,
                tree.segmonto_lines,
            )

            # sourceDoc (ALTO → SegmOnto) — parallélisé dans sourcedoc_build
            tree.build_sourcedoc(
                config,
                progress=progress,
                parent_task_pages=task_pages,
            )

            # body
            tree.build_body()

            # Mapping IIIF local éventuel
            mapping_csv = pick_mapping_csv(doc_dir, filepaths)
            if mapping_csv:
                mapping = read_local_iiif_mapping(mapping_csv)
                add_surface_facs_from_mapping(tree.root, mapping)
                console.print(
                    f"[dim]Mapping IIIF appliqué depuis {mapping_csv.name} ({len(mapping)} entrées)[/dim]"
                )

            # Surcharge du teiHeader avec le CSV local
            row = find_metadata_row(df_meta, bdd_prefix(doc_name)) if df_meta is not None else None
            override_teiheader_from_csv(tree.root, row)

            # Écriture finale
            out_path = OUTPUT_DIR / f"{doc_name}.tei.xml"
            write_pretty_xml(tree.root, out_path)

            dt = perf_counter() - t0
            console.print(
                f"[green]✔[/green] Écrit : {out_path} [dim](en {dt:.2f}s)[/dim]"
            )

            progress.update(task_pages, visible=False)
            progress.advance(task_docs)

    console.print("\n[bold green]Terminé.[/bold green]")


if __name__ == "__main__":
    main()
