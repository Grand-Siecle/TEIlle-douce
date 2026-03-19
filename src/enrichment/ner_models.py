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
        """HuggingFace NER pipeline for French classical text."""
        if self._camembert is None:
            model_id = self._config["camembert"]["model_id"]
            logger.info("Loading CamemBERT NER model: %s", model_id)
            from transformers import pipeline as hf_pipeline

            self._camembert = hf_pipeline(
                "ner",
                model=model_id,
                aggregation_strategy="simple",
            )
            logger.info("CamemBERT NER model loaded")
        return self._camembert

    @property
    def gliner(self):
        """GLiNER multi-language zero-shot NER model."""
        if self._gliner is None:
            model_id = self._config["gliner"]["model_id"]
            logger.info("Loading GLiNER model: %s", model_id)
            from gliner import GLiNER

            self._gliner = GLiNER.from_pretrained(model_id)
            logger.info("GLiNER model loaded")
        return self._gliner

    @property
    def has_camembert(self):
        """True if CamemBERT has been loaded."""
        return self._camembert is not None

    @property
    def has_gliner(self):
        """True if GLiNER has been loaded."""
        return self._gliner is not None
