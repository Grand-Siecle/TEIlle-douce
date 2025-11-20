# -----------------------------------------------------------
# À AJOUTER dans src/teiheader_metadata/iiif_data.py
# Classe pour gérer les mappings IIIF locaux depuis CSV
# -----------------------------------------------------------

from pathlib import Path
from typing import Dict, Optional
import pandas as pd


class IIIFMapping:
    """
    Gère le mapping entre fichiers ALTO et URLs IIIF depuis un CSV local.
    Format CSV attendu (sans header): URL_IIIF,source_identifier,nom_fichier_alto
    """

    def __init__(self):
        self.mapping: Dict[str, str] = {}

    def load_from_csv(self, csv_path: Path) -> bool:
        """
        Charge un mapping depuis un CSV.

        Returns:
            True si succès, False sinon
        """
        if not csv_path.exists():
            print(f"[warn] CSV introuvable: {csv_path}")
            return False

        try:
            df = pd.read_csv(csv_path, header=None)

            if df.shape[1] < 3:
                print(f"[warn] CSV invalide (< 3 colonnes): {csv_path.name}")
                return False

            count = 0
            for _, row in df.iterrows():
                url = str(row[0]).strip()
                alto_name = str(row[2]).strip()

                if not url or url == 'nan':
                    continue

                # Stocker avec et sans .xml
                base_name = alto_name.replace('.xml', '')
                self.mapping[base_name] = url
                self.mapping[f"{base_name}.xml"] = url
                count += 1
            return True

        except Exception as e:
            print(f"[error] Lecture CSV {csv_path.name}: {e}")
            return False

    def get_url(self, filename: str) -> Optional[str]:
        """
        Récupère l'URL IIIF pour un nom de fichier (ou xml:id).

        Args:
            filename: nom du fichier ALTO ou xml:id (ex: "f0-plat-sup", "f1", "f123")

        Returns:
            URL IIIF ou None
        """
        # Essayer tel quel
        url = self.mapping.get(filename)
        if url:
            return url

        # Essayer sans .xml
        base_name = filename.replace('.xml', '')
        return self.mapping.get(base_name)

    def has_mapping(self) -> bool:
        """Vérifie si un mapping a été chargé."""
        return len(self.mapping) > 0

    def count(self) -> int:
        """Nombre de fichiers mappés."""
        return len(self.mapping) // 2  # /2 car on stocke avec/sans .xml

    @staticmethod
    def detect_csv(doc_dir: Path, alto_files: list[Path]) -> Optional[Path]:
        """
        Détecte automatiquement un CSV de mapping dans un dossier.

        Args:
            doc_dir: dossier contenant les ALTO
            alto_files: liste des fichiers ALTO pour validation

        Returns:
            Path du CSV ou None
        """
        patterns = ["*iiif*.csv", "*mapping*.csv", "*manifest*.csv"]

        candidates = []
        for pattern in patterns:
            candidates.extend(doc_dir.glob(pattern))

        if not candidates:
            return None

        # Noms des fichiers ALTO
        alto_names = {p.name for p in alto_files}
        alto_names.update(p.stem for p in alto_files)

        # Trier par pertinence
        candidates.sort(key=lambda p: (
            0 if "iiif" in p.name.lower() else 1,
            p.name.lower()
        ))

        # Valider
        for csv_path in candidates:
            if csv_path.stat().st_size > 10_000_000:
                continue

            try:
                df = pd.read_csv(csv_path, header=None, nrows=100)

                if df.shape[1] < 3 or df.empty:
                    continue

                col2_values = df.iloc[:, 2].astype(str).str.strip()
                matches = sum(
                    1 for val in col2_values
                    if val in alto_names or val.replace('.xml', '') in alto_names
                )

                if matches > 0 and matches / len(col2_values) > 0.3:
                    return csv_path

            except Exception:
                continue

        return None