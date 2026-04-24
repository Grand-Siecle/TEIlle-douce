# -----------------------------------------------------------
# Lazy-loading NER model manager.
# -----------------------------------------------------------
"""
NER model loading (Phase 7 support).

Provides lazy-loaded access to CamemBERT and GLiNER models.
Models are only loaded when first accessed, avoiding unnecessary
memory usage when a corpus contains only one language family.
"""

import logging

logger = logging.getLogger(__name__)


class _FlairNERWrapper:
    """
    Wraps a Flair SequenceTagger to behave like a HuggingFace NER pipeline.

    Callable with a list of strings, returns list of list of dicts with
    keys: entity_group, score, start, end.
    """

    def __init__(self, tagger):
        self._tagger = tagger

    def __call__(self, texts):
        from flair.data import Sentence

        sentences = [Sentence(t) for t in texts]
        self._tagger.predict(sentences)

        all_results = []
        for sentence in sentences:
            preds = []
            for entity in sentence.get_spans("ner"):
                preds.append(
                    {
                        "entity_group": entity.get_label("ner").value,
                        "score": entity.get_label("ner").score,
                        "start": entity.start_position,
                        "end": entity.end_position,
                    }
                )
            all_results.append(preds)
        return all_results


class NERModels:
    """
    Lazy-loading wrapper for NER models.

    CamemBERT is loaded only when French blocks exist.
    GLiNER is loaded only when needed (any language, extended types).
    """

    def __init__(self, models_config):
        """
        Args:
            models_config: The NER_MODELS dict from config.py.
        """
        self._config = models_config
        self._camembert = None
        self._gliner = None

    @property
    def camembert(self):
        """Flair SequenceTagger for French classical text (wrapped as HF-like pipeline)."""
        if self._camembert is None:
            model_id = self._config["camembert"]["model_id"]
            logger.info("Loading CamemBERT NER model (Flair): %s", model_id)
            from flair.models import SequenceTagger

            # Flair expects pytorch_model.bin in the repo, but some repos
            # use a custom filename. Download explicitly when model_id
            # contains a filename (namespace/repo/file).
            if model_id.count("/") > 1:
                from huggingface_hub import hf_hub_download
                parts = model_id.split("/", 2)
                local_path = hf_hub_download(
                    repo_id=f"{parts[0]}/{parts[1]}",
                    filename=parts[2],
                )
                tagger = SequenceTagger.load(local_path)
            else:
                tagger = SequenceTagger.load(model_id)
            self._camembert = _FlairNERWrapper(tagger)
            logger.info("CamemBERT NER model loaded")
        return self._camembert

    @property
    def gliner(self):
        """GLiNER multi-language zero-shot NER model."""
        if self._gliner is None:
            model_id = self._config["gliner"]["model_id"]
            logger.info("Loading GLiNER model: %s", model_id)
            import torch
            from gliner import GLiNER

            device = "cuda" if torch.cuda.is_available() else "cpu"
            self._gliner = GLiNER.from_pretrained(model_id).to(device)
            logger.info("GLiNER model loaded on %s", device)
        return self._gliner

    @property
    def has_camembert(self):
        """True if CamemBERT has been loaded."""
        return self._camembert is not None

    @property
    def has_gliner(self):
        """True if GLiNER has been loaded."""
        return self._gliner is not None
