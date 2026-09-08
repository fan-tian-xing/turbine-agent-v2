"""Stage 3 minimum vertical-slice research runtime."""

from .corpus import load_corpus
from .pipeline import answer_question
from .real_trial import load_confirmed_real_corpus

__all__ = ["answer_question", "load_confirmed_real_corpus", "load_corpus"]
