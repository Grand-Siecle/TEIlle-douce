# -----------------------------------------------------------
# Lazy-loading NER model manager.
# -----------------------------------------------------------
"""
NER model loading (Phase 7 support).

Provides lazy-loaded access to CamemBERT and GLiNER models.
Models are only loaded when first accessed, avoiding unnecessary
memory usage when a corpus contains only one language family.
"""

import importlib.util
import logging

# What this module imports lazily, named so the CLI can tell before a run
# whether entity recognition can happen at all. Every import below is
# deferred into a method body, so importing this module proves nothing.
NER_DEPENDENCIES = ("flair", "gliner", "huggingface_hub", "torch")


def device_asked_for(override=None):
    """What the operator asked for, "auto" included, unresolved."""
    from teille_douce.settings import get_settings

    return get_settings().ner_device if override is None else override


def resolve_device(asked=None):
    """The device the models should load on, as torch spells it.

    "auto" is what this pipeline did unconditionally, and nothing more:
    CUDA when there is any, CPU otherwise. Not MPS — a Mac was getting the
    CPU before and an automatic move to Metal is a change of machine for
    every existing user, decided by an upgrade rather than by them. Ask for
    it with `--device mps`.

    The two cases automatic cannot guess are the ones the setting exists
    for: a shared GPU somebody else has filled, and a machine with more
    than one.
    """
    asked = device_asked_for(asked)
    if asked != "auto":
        return asked
    try:
        import torch
    except Exception:
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def missing_ner_dependencies():
    """The NER packages that are not installed, in declaration order.

    `find_spec` raises rather than answering for a name already in
    sys.modules with no spec, and propagates a half-installed package's own
    ImportError. Either turned the documented "dependencies not installed"
    warning into a traceback, so anything but a clean answer counts as
    missing — which is what the caller needs to know.
    """
    missing = []
    for name in NER_DEPENDENCIES:
        try:
            found = importlib.util.find_spec(name) is not None
        except Exception:
            found = False
        if not found:
            missing.append(name)
    return tuple(missing)


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

    def __init__(self, models_config, device=None):
        """
        Args:
            models_config: The NER_MODELS dict from config.py.
            device: Override the resolved device. Left None, each model
                asks the settings when it loads — which is what the run
                wants, since a model is loaded lazily and long after the
                command line was read.
        """
        self._config = models_config
        self._device = device
        self._camembert = None
        self._gliner = None

    @property
    def camembert(self):
        """Flair SequenceTagger for French classical text (wrapped as HF-like pipeline)."""
        if self._camembert is None:
            model_id = self._config["camembert"]["model_id"]
            logger.info("Loading CamemBERT NER model (Flair): %s", model_id)
            import flair
            import torch
            from flair.models import SequenceTagger

            # Flair reads this global at load time and picks its own
            # device, so a --device it never sees is a --device that does
            # nothing for half the models — the French one, which is most
            # of this corpus.
            #
            # Only when one was actually named. Flair's own default reads
            # FLAIR_DEVICE, and overwriting it under "auto" would discard
            # the one setting a Flair user already had for this — silently,
            # and in exactly the situation --device is for: keeping the
            # model off a GPU someone else is using.
            asked = device_asked_for(self._device)
            if asked != "auto":
                flair.device = torch.device(resolve_device(asked))
            logger.info("CamemBERT NER model loading on %s", flair.device)

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

            device = resolve_device(self._device)
            self._gliner = GLiNER.from_pretrained(model_id).to(device)
            logger.info("GLiNER model loaded on %s", device)
        return self._gliner
