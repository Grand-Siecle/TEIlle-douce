from src.teiheader_metadata.iiif_data import IIIF
from src.teiheader_metadata.sru_data import SRU

class Metadata:
    metadata = {"sru":None, "iiif":None}
    def __init__(self, document, config):
        self.d = document
        self.metadata
        self.iiifURI = config

    def prepare(self):
        # Mode hors ligne : on évite les requêtes HTTP
        if hasattr(self, "config") and self.config.get("offline"):
            print("[info] Mode hors ligne activé — pas de requêtes IIIF/SRU.")
            return {
                "sru": {"found": False},  # 👈 clé obligatoire
                "iiif": {}
            }

        try:
            iiif_data = IIIF.clean(IIIF.request())
        except Exception as e:
            print(f"[warn] Impossible de lire le manifeste IIIF ({e}); le header sera partiel.")
            iiif_data = {}

        try:
            sru_data = SRU.clean(SRU.request())
        except Exception as e:
            print(f"[warn] Impossible de lire les données SRU ({e}); le header sera partiel.")
            sru_data = {}

        return {"sru": sru_data, "iiif": iiif_data}