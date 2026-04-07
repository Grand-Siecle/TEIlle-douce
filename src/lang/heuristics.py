# -----------------------------------------------------------
# Heuristic language detection rules
# -----------------------------------------------------------
"""
Heuristic rules for language detection.

This module provides rule-based language detection as fallback
when statistical model confidence is low, particularly useful for:
- Short texts where statistical models struggle
- OCR-degraded historical texts
- Texts with distinctive character sets (Greek, etc.)
- Common citation patterns (Latin scholarly references)
"""

import re
from collections import Counter


class LanguageHeuristics:
    """
    Rule-based language detection using character patterns and keywords.

    Each language has:
    - `chars`: Distinctive characters/patterns (high confidence indicators)
    - `words`: Common words (weighted by frequency in detection)
    - `patterns`: Regex patterns for specific constructs

    Attributes:
        rules (dict): Language detection rules keyed by TEI ident.
        min_word_matches (int): Minimum word matches to trigger detection.
        char_weight (float): Weight multiplier for character matches.
    """

    def __init__(self, min_word_matches=2, char_weight=3.0):
        """
        Initialize heuristics with configurable thresholds.

        Args:
            min_word_matches: Minimum keyword matches for detection.
            char_weight: Weight multiplier for distinctive characters.
        """
        self.min_word_matches = min_word_matches
        self.char_weight = char_weight
        self.rules = self._build_rules()

    def _build_rules(self):
        """
        Build language detection rules.

        Returns:
            dict: Rules keyed by TEI language ident.
        """
        return {
            # Ancient Greek - very distinctive characters
            "grc": {
                "chars": set("αβγδεζηθικλμνξοπρστυφχψωάέήίόύώϊϋΐΰἀἁἂἃἄἅἆἇᾀᾁᾂᾃᾄᾅᾆᾇᾈᾉᾊᾋᾌᾍᾎᾏ"
                            "ἐἑἒἓἔἕἘἙἚἛἜἝἠἡἢἣἤἥἦἧᾐᾑᾒᾓᾔᾕᾖᾗᾘᾙᾚᾛᾜᾝᾞᾟἰἱἲἳἴἵἶἷὀὁὂὃὄὅ"
                            "ὐὑὒὓὔὕὖὗὠὡὢὣὤὥὦὧᾠᾡᾢᾣᾤᾥᾦᾧᾨᾩᾪᾫᾬᾭᾮᾯὰάὲέὴήὶίὸόὺύὼώᾲᾳᾴᾶᾷ"
                            "ῂῃῄῆῇῲῳῴῶῷΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"),
                "words": [],  # Characters are sufficient for Greek
                "patterns": [],
            },

            # Latin - scholarly/ecclesiastical vocabulary
            # Only words that are unambiguously Latin (not shared with French)
            "lat": {
                "chars": set(),  # Latin alphabet shared with other languages
                "words": [
                    # Unambiguous Latin function words
                    "sed", "cum", "quod", "quae", "quia", "sic", "ita",
                    "ergo", "idem", "enim", "autem", "vel", "aut", "nec", "neque",
                    "atque", "tamen", "etiam", "quidem", "ideo", "unde",
                    # Philosophical/theological terms
                    "esse", "ens", "veritas", "virtus",
                    "anima", "intellectus", "voluntas", "fides",
                    # Scholarly citations
                    "ibid", "ibidem", "articulus",
                    "quaestio", "obiectio", "respondeo", "dico", "dicendum",
                    # Unambiguous Latin verbs
                    "dicit", "dicitur", "potest", "debet", "habet", "facit",
                    # Distinctive Latin endings
                    "orum", "arum", "ibus", "antur", "untur",
                ],
                "patterns": [
                    r"\bS\.\s*Thom",  # S. Thomas (Aquinas)
                    r"\bD\.\s*Thom",  # D. Thomas
                    r"\bcap\.\s*\d+",  # cap. 1, cap. 12
                    r"\blib\.\s*\d+",  # lib. 1
                    r"\bdist\.\s*\d+",  # dist. 1
                    r"\bq\.\s*\d+",  # q. 1 (quaestio)
                    r"\bart\.\s*\d+",  # art. 1 (articulus)
                ],
            },

            # French - distinctive patterns and common words
            # Reinforced for early modern French (16th-18th c.)
            "fra": {
                "chars": set("çœŒéèêëàâùûîïôæÆ"),  # French diacritics
                "words": [
                    # Articles and determiners (unambiguous for French)
                    "le", "la", "les", "une", "des", "du", "au", "aux",
                    # Pronouns
                    "je", "tu", "il", "elle", "nous", "vous", "ils", "elles",
                    "ce", "cette", "ces", "dont", "où",
                    # Prepositions
                    "dans", "sur", "sous", "avec", "pour", "par", "sans", "chez",
                    "entre", "vers", "depuis", "devant", "après",
                    # Conjunctions
                    "ou", "mais", "donc", "car", "ni", "comme",
                    "parce", "lorsque", "puisque", "quoique",
                    # Common verbs
                    "sont", "avoir", "faire", "dire", "voir", "pouvoir",
                    "fut", "peut", "doit", "fait", "dit", "soit",
                    # Adverbs
                    "plus", "moins", "bien", "mal", "très", "aussi", "encore",
                    "toujours", "jamais", "rien", "point", "mesme",
                    # Demonstratives / possessives
                    "son", "ses", "leur", "leurs", "mon", "nos", "vos",
                    # Early modern French / old orthography
                    "estre", "auoit", "estoit", "mesme", "tousiours", "iamais",
                    "auec", "faisoit", "pouuoit", "deuoit", "auant",
                    "lequel", "laquelle", "lesquels", "lesquelles",
                    "ceste", "icelle", "iceluy", "ledit", "ladite",
                    "chapitre", "philosophe", "quelque", "plusieurs",
                ],
                "patterns": [
                    r"\bl['']",  # l'homme, l'art
                    r"\bd['']",  # d'abord, d'un
                    r"\bqu['']",  # qu'il, qu'on
                    r"\bn['']",  # n'est, n'a
                    r"\bs['']",  # s'il, s'est
                    r"\bc['']",  # c'est, c'était
                    r"«.*?»",  # French quotation marks
                    # French suffix patterns (not shared with Latin)
                    r"\b\w+ment\b",  # -ment (adverbs)
                    r"\b\w+eux\b",  # -eux (adjectives)
                    r"\b\w+euse\b",  # -euse
                    r"\b\w+ois\b",  # -ois (old French adjectives)
                    r"\b\w+oit\b",  # -oit (old French verb endings)
                    r"\b\w+oient\b",  # -oient (old French 3rd pl.)
                ],
            },

            # Italian - distinctive patterns
            "ita": {
                "chars": set(),
                "words": [
                    # Articles
                    "il", "lo", "la", "gli", "le", "un", "uno", "una",
                    # Prepositions with articles
                    "del", "dello", "della", "dei", "degli", "delle",
                    "al", "allo", "alla", "ai", "agli", "alle",
                    "nel", "nello", "nella", "nei", "negli", "nelle",
                    # Pronouns
                    "io", "tu", "lui", "lei", "noi", "voi", "loro",
                    "che", "chi", "cui", "quale", "quali",
                    # Common words
                    "di", "da", "con", "per", "tra", "fra", "su",
                    "ma", "anche", "come", "quando", "dove", "perché",
                    "questo", "questa", "quello", "quella",
                    # Verbs
                    "essere", "avere", "fare", "dire", "vedere", "potere",
                    "sono", "hai", "hanno", "fatto", "detto",
                ],
                "patterns": [
                    r"\bdell['']",  # dell'uomo
                    r"\ball['']",  # all'interno
                    r"\bnell['']",  # nell'arte
                    r"\bsull['']",  # sull'argomento
                    r"\bqu['']",  # Italian elision
                ],
            },

            # German - distinctive characters and patterns
            "deu": {
                "chars": set("äöüßÄÖÜ"),  # German umlauts and eszett
                "words": [
                    # Articles
                    "der", "die", "das", "den", "dem", "des",
                    "ein", "eine", "einer", "einem", "einen", "eines",
                    # Pronouns
                    "ich", "du", "er", "sie", "es", "wir", "ihr",
                    "mich", "dich", "sich", "uns", "euch",
                    # Prepositions
                    "in", "an", "auf", "mit", "bei", "nach", "von", "zu",
                    "aus", "durch", "für", "gegen", "ohne", "um",
                    # Conjunctions
                    "und", "oder", "aber", "denn", "weil", "dass", "wenn",
                    # Common words
                    "ist", "sind", "hat", "haben", "wird", "werden",
                    "nicht", "auch", "nur", "noch", "schon", "sehr",
                    "kann", "muss", "soll", "will", "darf",
                ],
                "patterns": [
                    r"\bzu\s+\w+en\b",  # zu machen, zu sehen (infinitive)
                ],
            },

            # English - common words (careful: many shared with other languages)
            "eng": {
                "chars": set(),
                "words": [
                    # Articles (distinctive)
                    "the", "a", "an",
                    # Pronouns
                    "i", "you", "he", "she", "it", "we", "they",
                    "my", "your", "his", "her", "its", "our", "their",
                    "this", "that", "these", "those",
                    # Prepositions
                    "of", "to", "in", "for", "on", "with", "at", "by", "from",
                    # Conjunctions
                    "and", "or", "but", "if", "because", "although", "while",
                    # Common verbs
                    "is", "are", "was", "were", "be", "been", "being",
                    "have", "has", "had", "do", "does", "did",
                    "will", "would", "could", "should", "may", "might",
                    # Adverbs
                    "not", "very", "also", "just", "only", "still",
                    # Distinctive words
                    "which", "what", "who", "whom", "whose", "where", "when",
                ],
                "patterns": [
                    r"\b\w+ing\b",  # -ing endings
                    r"\b\w+tion\b",  # -tion endings
                    r"\bthe\s+\w+",  # the + word
                ],
            },

            # Dutch - distinctive patterns
            "nld": {
                "chars": set(),
                "words": [
                    # Articles
                    "de", "het", "een",
                    # Pronouns
                    "ik", "jij", "je", "hij", "zij", "ze", "wij", "we", "jullie",
                    "mij", "me", "hem", "haar", "ons", "hen", "hun",
                    "dit", "dat", "deze", "die", "wat", "wie", "welke",
                    # Prepositions
                    "van", "in", "op", "met", "voor", "aan", "door", "naar",
                    "uit", "bij", "over", "onder", "tussen", "zonder",
                    # Conjunctions
                    "en", "of", "maar", "want", "dus", "omdat", "dat", "als",
                    # Common verbs
                    "is", "zijn", "ben", "bent", "was", "waren",
                    "hebben", "heeft", "had", "hadden",
                    "worden", "wordt", "werd", "werden",
                    "kunnen", "kan", "kon", "konden",
                    "moeten", "moet", "moest", "moesten",
                    # Common words
                    "niet", "ook", "nog", "wel", "al", "dan", "er", "hier",
                    "waar", "hoe", "waarom", "wanneer",
                ],
                "patterns": [
                    r"\b\w+lijk\b",  # -lijk endings (eigenlijk, natuurlijk)
                    r"\b\w+heid\b",  # -heid endings (vrijheid)
                    r"\bhet\s+\w+",  # het + word
                ],
            },
        }

    def _count_char_matches(self, text, chars):
        """Count distinctive character matches in text."""
        if not chars:
            return 0
        return sum(1 for c in text if c in chars)

    def _count_word_matches(self, text, words):
        """Count keyword matches in text (case-insensitive)."""
        if not words:
            return 0
        text_lower = text.lower()
        # Split into words and count matches
        text_words = set(re.findall(r'\b\w+\b', text_lower))
        return sum(1 for w in words if w in text_words)

    def _count_pattern_matches(self, text, patterns):
        """Count regex pattern matches in text."""
        if not patterns:
            return 0
        count = 0
        for pattern in patterns:
            count += len(re.findall(pattern, text, re.IGNORECASE))
        return count

    def detect(self, text):
        """
        Detect language using heuristic rules.

        Analyzes the text for distinctive characters, keywords, and patterns
        to determine the most likely language.

        Args:
            text (str): Text to analyze.

        Returns:
            tuple: (language_ident, confidence_score) or (None, 0) if no match.
                   Confidence is a relative score, not a probability.
        """
        if not text or len(text.strip()) < 3:
            return (None, 0)

        scores = {}

        for lang, rules in self.rules.items():
            score = 0

            # Character matches (weighted heavily for distinctive scripts)
            char_matches = self._count_char_matches(text, rules["chars"])
            if char_matches > 0:
                score += char_matches * self.char_weight

            # Word matches
            word_matches = self._count_word_matches(text, rules["words"])
            score += word_matches

            # Pattern matches
            pattern_matches = self._count_pattern_matches(text, rules["patterns"])
            score += pattern_matches * 1.5

            if score > 0:
                scores[lang] = score

        if not scores:
            return (None, 0)

        # Return language with highest score
        best_lang = max(scores, key=scores.get)
        best_score = scores[best_lang]

        # Require minimum evidence
        if best_score < self.min_word_matches:
            return (None, best_score)

        return (best_lang, best_score)

# Global singleton
_heuristics = None


def get_heuristics():
    """Get or create the global heuristics instance."""
    global _heuristics
    if _heuristics is None:
        _heuristics = LanguageHeuristics()
    return _heuristics
