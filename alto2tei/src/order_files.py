# -----------------------------------------------------------
# Code by: Kelly Christensen
# Python class to organize the file paths in the data directory.
# -----------------------------------------------------------

import re
from collections import namedtuple

File = namedtuple("File", ["num", "filepath"])

class Files:
    def __init__(self, document_name, file_list):
        self.doc = document_name
        self.fl = file_list

    def order_files(self):
        """
        Trie les fichiers ALTO par numéro de page :
        - reconnaît f12.xml, f12-np.xml, page_001.xml, etc.
        - place les fichiers non numérotés à la fin
        """
        numbered = []
        others = []

        for f in self.fl:
            m = re.search(r"(\d+)", f.stem)
            if m:
                try:
                    num = int(m.group(1))
                    numbered.append(File(num, f))
                except ValueError:
                    others.append(File(999999, f))
            else:
                others.append(File(999999, f))
                print(f"[warn] fichier ignoré (pas de numéro de page détecté): {f.name}")

        if not numbered and not others:
            raise ValueError(f"Aucun fichier ALTO valide trouvé pour {self.doc}")

        ordered_files = sorted(numbered + others, key=lambda x: x.num)
        return ordered_files