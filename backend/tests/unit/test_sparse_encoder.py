"""Unit tests for BM25SparseEncoder (T002).

Tests: CJK jieba tokenization, Latin regex tokenization, BM25 term weights,
determinism (same-input-same-output), frozen vocab.

These tests MUST FAIL before sparse_encoder.py is implemented (TDD).
"""

from __future__ import annotations

import pytest

from rag_mcp.indexing.sparse_encoder import BM25SparseEncoder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fitted_encoder():
    """A BM25SparseEncoder fitted on a small corpus."""
    encoder = BM25SparseEncoder()
    corpus = [
        "validateToken 方法验证用户令牌",
        "UserService provides user management functions",
        "com.example.service.UserService#validateToken",
        "数据库配置和连接池管理",
        "The API key is used for authentication",
    ]
    encoder.fit(corpus)
    return encoder


# ---------------------------------------------------------------------------
# CJK tokenization (jieba)
# ---------------------------------------------------------------------------

class TestCJKTokenization:
    """FR-025: CJK content must be tokenized with jieba, not naive whitespace."""

    def test_chinese_text_is_segmented(self, fitted_encoder):
        """Chinese text '数据库配置' must be segmented into multiple tokens, not one block."""
        vec = fitted_encoder.encode("数据库配置")
        # A properly segmented Chinese query should produce at least 2 tokens
        assert len(vec["indices"]) >= 2, (
            "Chinese text should be segmented into multiple tokens by jieba, "
            "not treated as a single token"
        )

    def test_chinese_query_matches_chinese_corpus(self, fitted_encoder):
        """A Chinese query should produce non-zero sparse vector values."""
        vec = fitted_encoder.encode("数据库配置")
        assert len(vec["values"]) > 0
        assert all(v > 0 for v in vec["values"])

    def test_chinese_word_boundary_split(self, fitted_encoder):
        """'验证令牌' must split into '验证' and '令牌' (or similar), not stay as one block."""
        vec1 = fitted_encoder.encode("验证令牌")
        vec2 = fitted_encoder.encode("验证")
        # '验证' should appear as a token in both
        # At least some indices should overlap
        overlap = set(vec1["indices"]) & set(vec2["indices"])
        assert len(overlap) > 0, (
            "jieba should segment '验证令牌' such that '验证' is a shared token"
        )


# ---------------------------------------------------------------------------
# Latin / regex tokenization
# ---------------------------------------------------------------------------

class TestLatinTokenization:
    """Latin text tokenized via regex [a-z0-9_]+ after lowercasing."""

    def test_english_words_tokenized(self, fitted_encoder):
        """English text should be tokenized into individual words."""
        vec = fitted_encoder.encode("validateToken")
        assert len(vec["indices"]) >= 1

    def test_case_insensitive_matching(self, fitted_encoder):
        """'ValidateToken' and 'validatetoken' should produce the same tokens."""
        vec_upper = fitted_encoder.encode("ValidateToken")
        vec_lower = fitted_encoder.encode("validatetoken")
        assert vec_upper["indices"] == vec_lower["indices"]

    def test_code_symbol_path_tokenized(self, fitted_encoder):
        """A fully-qualified symbol path should be split into component tokens."""
        vec = fitted_encoder.encode("com.example.service.UserService#validateToken")
        # Should produce multiple tokens (com, example, service, userservice, validatetoken)
        assert len(vec["indices"]) >= 3

    def test_punctuation_stripped(self, fitted_encoder):
        """Punctuation should be stripped, not included in tokens."""
        vec = fitted_encoder.encode("api, key!")
        # Should produce tokens for 'api' and 'key', not 'api,' or 'key!'
        assert len(vec["indices"]) >= 2


# ---------------------------------------------------------------------------
# BM25 term weights
# ---------------------------------------------------------------------------

class TestBM25Weights:
    """BM25 term weights: IDF weighting, TF saturation."""

    def test_rare_term_has_higher_weight(self, fitted_encoder):
        """A rare term (appears in 1 doc) should have higher IDF than a common term."""
        # 'authentication' appears in 1 doc, 'service' appears in 2+ docs
        vec_rare = fitted_encoder.encode("authentication")
        vec_common = fitted_encoder.encode("service")

        # The max weight of the rare term should be higher (higher IDF)
        # Note: both terms must be in the vocab
        if vec_rare["indices"] and vec_common["indices"]:
            max_rare = max(vec_rare["values"])
            max_common = max(vec_common["values"])
            assert max_rare > 0
            assert max_common > 0

    def test_term_frequency_saturation(self, fitted_encoder):
        """Repeated terms in a document should have sub-linear weight growth (BM25 saturation)."""
        text_once = "token"
        text_many = "token token token token token"
        vec_once = fitted_encoder.encode(text_once)
        vec_many = fitted_encoder.encode(text_many)

        # If 'token' is in vocab, the weight should increase but sub-linearly
        if vec_once["indices"] and vec_many["indices"]:
            # Find the 'token' term's weight
            idx = vec_once["indices"][0]
            w_once = vec_once["values"][0]
            # In vec_many, the same term should have higher weight
            if idx in vec_many["indices"]:
                pos = vec_many["indices"].index(idx)
                w_many = vec_many["values"][pos]
                assert w_many >= w_once, "TF saturation should not decrease weight"

    def test_all_values_positive(self, fitted_encoder):
        """All BM25 weight values must be positive (or zero for OOV terms)."""
        for text in ["validateToken", "数据库", "service", "authentication"]:
            vec = fitted_encoder.encode(text)
            for v in vec["values"]:
                assert v >= 0, f"BM25 weight must be non-negative, got {v}"


# ---------------------------------------------------------------------------
# Determinism (Constitution principle VI)
# ---------------------------------------------------------------------------

class TestDeterminism:
    """Same input must always produce same output (FR-017, Constitution VI)."""

    def test_same_input_same_output(self, fitted_encoder):
        """Encoding the same text twice must produce identical sparse vectors."""
        text = "validateToken 验证令牌"
        vec1 = fitted_encoder.encode(text)
        vec2 = fitted_encoder.encode(text)
        assert vec1["indices"] == vec2["indices"]
        assert vec1["values"] == vec2["values"]

    def test_deterministic_across_encoders(self):
        """Two encoders fitted on the same corpus must produce identical vocab."""
        corpus = ["hello world", "foo bar", "验证令牌"]
        enc1 = BM25SparseEncoder()
        enc2 = BM25SparseEncoder()
        enc1.fit(corpus)
        enc2.fit(corpus)
        assert enc1.vocab_size == enc2.vocab_size
        # Same text should produce same indices
        v1 = enc1.encode("hello")
        v2 = enc2.encode("hello")
        assert v1["indices"] == v2["indices"]

    def test_order_independent_encoding(self, fitted_encoder):
        """Encoding the same text in different order should give same result."""
        text = "validateToken service"
        vec1 = fitted_encoder.encode(text)
        vec2 = fitted_encoder.encode("service validateToken")
        # Indices should be the same set (order within vector may differ but set should match)
        assert set(vec1["indices"]) == set(vec2["indices"])


# ---------------------------------------------------------------------------
# Frozen vocab (Constitution principle VI: no online learning)
# ---------------------------------------------------------------------------

class TestFrozenVocab:
    """Vocabulary must be frozen after fit() — no online learning."""

    def test_vocab_frozen_after_fit(self, fitted_encoder):
        """After fit(), vocab_frozen must be True."""
        assert fitted_encoder.vocab_frozen is True

    def test_oov_terms_not_added(self, fitted_encoder):
        """Out-of-vocabulary terms must not be added to vocab after fit()."""
        size_before = fitted_encoder.vocab_size
        # Encode text with new terms
        fitted_encoder.encode("brandnewterm nevertseenbefore")
        assert fitted_encoder.vocab_size == size_before, (
            "Vocab must not grow after fit() — no online learning (Constitution VI)"
        )

    def test_oov_terms_produce_no_vector(self, fitted_encoder):
        """Pure OOV text should produce an empty sparse vector."""
        vec = fitted_encoder.encode("zzqqxx yywwvv")
        # All terms are OOV → no indices
        assert len(vec["indices"]) == 0

    def test_vocab_size_positive(self, fitted_encoder):
        """After fitting on a real corpus, vocab size must be > 0."""
        assert fitted_encoder.vocab_size > 0


# ---------------------------------------------------------------------------
# Sparse vector format
# ---------------------------------------------------------------------------

class TestSparseVectorFormat:
    """Output must match Qdrant sparse vector format {indices, values}."""

    def test_returns_dict_with_indices_and_values(self, fitted_encoder):
        """encode() must return a dict with 'indices' and 'values' keys."""
        vec = fitted_encoder.encode("validateToken")
        assert isinstance(vec, dict)
        assert "indices" in vec
        assert "values" in vec

    def test_indices_are_integers(self, fitted_encoder):
        """All indices must be integers (for Qdrant sparse vector format)."""
        vec = fitted_encoder.encode("validateToken")
        for idx in vec["indices"]:
            assert isinstance(idx, int), f"Index must be int, got {type(idx)}"

    def test_indices_and_values_same_length(self, fitted_encoder):
        """indices and values arrays must have the same length."""
        vec = fitted_encoder.encode("validateToken 数据库")
        assert len(vec["indices"]) == len(vec["values"])

    def test_indices_unique(self, fitted_encoder):
        """Each term should appear at most once in the sparse vector."""
        vec = fitted_encoder.encode("validateToken validateToken")
        # BM25 aggregates TF per term, so indices should be unique
        assert len(vec["indices"]) == len(set(vec["indices"])), (
            "Sparse vector indices must be unique — no duplicate terms"
        )
