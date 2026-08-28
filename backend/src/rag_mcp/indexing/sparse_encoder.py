"""BM25 Sparse Encoder with jieba CJK tokenization (002).

Deterministic BM25 sparse vector encoder for Qdrant named sparse vectors.
Tokenizes CJK content with jieba (precise mode) and Latin content with regex.
Vocabulary is frozen after fit() — no online learning (Constitution principle VI).

Blueprint §8.1/§18.2, FR-001/FR-025, data-model.md §5.5/§9.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any

logger = logging.getLogger(__name__)


def _compute_term_id(term: str) -> int:
    """Compute a deterministic 32-bit term ID from the term string.

    Uses SHA-256 and takes the first 4 bytes. This ensures the same term
    always maps to the same ID regardless of fitting order, enabling
    query-time encoding to match ingestion-time stored sparse vectors.
    """
    digest = hashlib.sha256(term.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")

# Unicode CJK Unified Ideographs range
_CJK_RANGES = (
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0x3400, 0x4DBF),    # CJK Extension A
    (0x20000, 0x2A6DF),  # CJK Extension B
)

# Latin token pattern (after lowercasing)
_LATIN_RE = re.compile(r"[a-z0-9_]+")

# Common English stop words to filter (improves sparse precision for code queries)
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "can", "shall",
    "of", "in", "on", "at", "to", "for", "with", "by", "from", "as",
    "into", "about", "over", "after", "under", "between", "through",
    "find", "show", "explain", "describe", "what", "how", "why", "when",
    "where", "who", "which", "that", "this", "these", "those",
    "and", "or", "but", "not", "no", "nor", "so", "if", "then",
    "me", "my", "we", "our", "you", "your", "he", "she", "it", "its",
    "they", "them", "their", "there", "here",
    "section", "content", "purpose", "definition", "implementation",
    "code", "method", "function", "class", "use", "used", "using",
    "get", "set", "return", "returns",
})


def _is_cjk(ch: str) -> bool:
    """Check if a character is a CJK ideograph."""
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _has_cjk(token: str) -> bool:
    """Check if a string contains any CJK character."""
    return any(_is_cjk(ch) for ch in token)


class BM25SparseEncoder:
    """Deterministic BM25 sparse vector encoder.

    Builds a frozen vocabulary and IDF table from a corpus during fit(),
    then encodes text into sparse vectors {indices, values} for Qdrant.

    Constitution principle VI: fully deterministic — same input always
    produces same output; vocabulary is frozen after fit(), no online
    learning.

    Attributes:
        k1: TF saturation parameter (default 1.2, BM25 standard).
        b: Length normalization parameter (default 0.75, BM25 standard).
    """

    def __init__(self, k1: float = 1.2, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._vocab: dict[str, int] = {}       # term -> stable term_id
        self._idf: dict[int, float] = {}         # term_id -> IDF weight
        self._avgdl: float = 0.0                 # average document length (tokens)
        self._frozen: bool = False

    # ------------------------------------------------------------------
    # Fitting (vocabulary construction — frozen after)
    # ------------------------------------------------------------------

    def fit(self, corpus: list[str]) -> None:
        """Build vocabulary and IDF from a corpus. Vocab is frozen after fit().

        Args:
            corpus: List of document texts (chunk content_text).

        Raises:
            ValueError: If fit() is called more than once (vocab already frozen).
        """
        if self._frozen:
            raise ValueError("Vocabulary is already frozen — cannot re-fit (Constitution VI)")

        if not corpus:
            self._frozen = True
            return

        # Tokenize all documents
        tokenized_docs = [self._tokenize(doc) for doc in corpus]

        # Build vocabulary and document frequency
        doc_freq: dict[str, int] = {}
        for doc_tokens in tokenized_docs:
            unique_tokens = set(doc_tokens)
            for token in unique_tokens:
                if token not in self._vocab:
                    # Hash-based term ID: deterministic across encoders
                    self._vocab[token] = _compute_term_id(token)
                doc_freq[token] = doc_freq.get(token, 0) + 1

        # Compute average document length
        n_docs = len(tokenized_docs)
        self._avgdl = sum(len(d) for d in tokenized_docs) / max(n_docs, 1)

        # Compute IDF for each term (Lucene-style: always non-negative)
        for term, df in doc_freq.items():
            term_id = self._vocab[term]
            # Lucene BM25 IDF: log(1 + (N - df + 0.5) / (df + 0.5))
            # Ensures non-negative weights even for terms in >50% of docs
            self._idf[term_id] = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))

        self._frozen = True
        logger.info(
            "BM25SparseEncoder fitted: %d terms, avgdl=%.1f docs=%d",
            len(self._vocab), self._avgdl, n_docs,
        )

    # ------------------------------------------------------------------
    # Encoding
    # ------------------------------------------------------------------

    def encode(self, text: str) -> dict[str, list[int | float]]:
        """Encode text into a sparse vector {indices, values}.

        Out-of-vocabulary terms are silently skipped (vocab is frozen).
        Returns empty {indices: [], values: []} if no known terms match.

        Args:
            text: Input text (credential-redacted chunk content or query).

        Returns:
            Dict with 'indices' (list[int]) and 'values' (list[float]).
        """
        tokens = self._tokenize(text)
        if not tokens or not self._frozen:
            return {"indices": [], "values": []}

        # Count term frequencies (only in-vocab terms)
        tf: dict[str, int] = {}
        for token in tokens:
            if token in self._vocab:
                tf[token] = tf.get(token, 0) + 1

        if not tf:
            return {"indices": [], "values": []}

        dl = len(tokens)  # document length (all tokens, including OOV)
        avgdl = self._avgdl or 1.0

        indices: list[int] = []
        values: list[float] = []

        for term, freq in tf.items():
            term_id = self._vocab[term]
            idf = self._idf.get(term_id, 0.0)
            # BM25 TF saturation
            tf_sat = (freq * (self._k1 + 1)) / (
                freq + self._k1 * (1 - self._b + self._b * dl / avgdl)
            )
            weight = idf * tf_sat
            if weight > 0:
                indices.append(term_id)
                values.append(weight)

        return {"indices": indices, "values": values}

    def encode_query(self, text: str) -> dict[str, list[int | float]]:
        """Encode a query into a sparse vector with binary term presence.

        For Qdrant sparse dot product: query values are 1.0, stored document
        values are BM25 weights (IDF * TF_sat). The dot product gives the sum
        of BM25 weights for matching terms — a valid BM25 score.

        Only in-vocabulary terms are included (terms seen during fit()).
        Requires the encoder to be fitted (vocab frozen).

        Args:
            text: Query text.

        Returns:
            Dict with 'indices' (list[int]) and 'values' (list[float]).
        """
        tokens = self._tokenize(text)
        if not tokens or not self._frozen:
            return {"indices": [], "values": []}

        # Collect unique in-vocab term IDs
        seen_ids: set[int] = set()
        for token in tokens:
            if token in self._vocab:
                seen_ids.add(self._vocab[token])

        if not seen_ids:
            return {"indices": [], "values": []}

        indices = sorted(seen_ids)
        values = [1.0] * len(indices)
        return {"indices": indices, "values": values}

    # ------------------------------------------------------------------
    # Tokenization (jieba CJK + regex Latin)
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text: jieba for CJK, regex for Latin.

        Strategy:
        1. Use jieba.lcut (precise mode) for initial segmentation — jieba
           handles CJK well and splits mixed content at CJK/Latin boundaries.
        2. For CJK tokens (containing CJK chars), keep as-is (lowercased).
        3. For Latin tokens (no CJK chars), extract sub-tokens via regex
           [a-z0-9_]+ after lowercasing (strips punctuation, splits on dots/hashes).
        """
        import jieba

        raw_tokens = jieba.lcut(text, cut_all=False)  # precise mode
        result: list[str] = []

        for token in raw_tokens:
            token = token.strip()
            if not token:
                continue
            if _has_cjk(token):
                # CJK token — jieba already segmented; lowercase for consistency
                result.append(token.lower())
            else:
                # Latin token — extract alphanumeric sub-tokens, filter stop words
                sub_tokens = _LATIN_RE.findall(token.lower())
                result.extend(t for t in sub_tokens if t not in _STOP_WORDS)

        return result

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def vocab_frozen(self) -> bool:
        """True after fit() has been called (vocabulary is immutable)."""
        return self._frozen

    @property
    def vocab_size(self) -> int:
        """Number of terms in the frozen vocabulary."""
        return len(self._vocab)
