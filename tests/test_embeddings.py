"""Embedding-model tests.

`SentenceTransformer` is replaced with a fake, so nothing here downloads the model
or runs a forward pass. What matters is the contract the scoring engine depends on:
the model is constructed **once per process**, and vectors come back normalized.

These tests opt out of the autouse doubles in conftest (which patch ``embed_texts``
itself) — they need the real functions under test.
"""

import math

import pytest

from app.services import embeddings

pytestmark = pytest.mark.no_stubs


class FakeSentenceTransformer:
    """Counts constructions, so "loaded once" is a testable claim."""

    instances = 0
    dim = 4

    def __init__(self, name: str, device: str | None = None) -> None:
        type(self).instances += 1
        self.name = name
        self.device = device

    def get_sentence_embedding_dimension(self) -> int:
        return self.dim

    def encode(self, texts, normalize_embeddings=False, convert_to_numpy=True, **kwargs):
        import numpy as np

        # Deterministic, non-normalized vectors derived from the text length.
        raw = np.array(
            [[float(len(t) + i + 1) for i in range(self.dim)] for t in texts],
            dtype=float,
        )
        if normalize_embeddings:
            norms = np.linalg.norm(raw, axis=1, keepdims=True)
            raw = raw / norms
        return raw


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    FakeSentenceTransformer.instances = 0
    monkeypatch.setattr(embeddings, "SentenceTransformer", FakeSentenceTransformer)
    embeddings.get_embedding_model.cache_clear()
    yield
    embeddings.get_embedding_model.cache_clear()


class TestModelIsLoadedOnce:
    """Spec section 2: loaded once per worker process, never per task."""

    def test_repeated_calls_return_the_same_instance(self):
        assert embeddings.get_embedding_model() is embeddings.get_embedding_model()

    def test_model_is_constructed_only_once(self):
        for _ in range(5):
            embeddings.get_embedding_model()

        assert FakeSentenceTransformer.instances == 1

    def test_many_embed_calls_do_not_reload_the_model(self):
        for index in range(10):
            embeddings.embed_text(f"resume number {index}")

        assert FakeSentenceTransformer.instances == 1

    def test_model_name_comes_from_settings(self):
        from app.core.config import get_settings

        model = embeddings.get_embedding_model()

        assert model.name == get_settings().embedding_model

    def test_device_is_pinned_from_settings(self):
        """Must not be left to auto-detect: MPS/CUDA state does not survive a fork,
        which crashes Celery's prefork children on startup."""
        from app.core.config import get_settings

        model = embeddings.get_embedding_model()

        assert model.device == get_settings().embedding_device

    def test_default_device_is_cpu(self):
        from app.core.config import get_settings

        assert get_settings().embedding_device == "cpu"


class TestEmbedTexts:
    def test_returns_one_vector_per_text(self):
        vectors = embeddings.embed_texts(["a", "bb", "ccc"])

        assert len(vectors) == 3

    def test_vectors_are_plain_python_lists(self):
        vectors = embeddings.embed_texts(["hello"])

        assert isinstance(vectors, list)
        assert isinstance(vectors[0], list)
        assert all(isinstance(value, float) for value in vectors[0])

    def test_vectors_are_unit_length(self):
        """Normalized vectors let cosine similarity reduce to a dot product."""
        for vector in embeddings.embed_texts(["short", "a much longer piece of text"]):
            assert math.sqrt(sum(v * v for v in vector)) == pytest.approx(1.0)

    def test_empty_list_short_circuits_without_touching_the_model(self):
        assert embeddings.embed_texts([]) == []
        assert FakeSentenceTransformer.instances == 0

    def test_is_deterministic(self):
        assert embeddings.embed_texts(["same text"]) == embeddings.embed_texts(["same text"])

    def test_batches_in_a_single_encode_call(self, monkeypatch):
        calls = []
        original = FakeSentenceTransformer.encode

        def counting_encode(self, texts, **kwargs):
            calls.append(list(texts))
            return original(self, texts, **kwargs)

        monkeypatch.setattr(FakeSentenceTransformer, "encode", counting_encode)

        embeddings.embed_texts(["one", "two", "three"])

        assert len(calls) == 1
        assert calls[0] == ["one", "two", "three"]


class TestEmbedText:
    def test_returns_a_single_vector(self):
        vector = embeddings.embed_text("a resume")

        assert isinstance(vector, list)
        assert len(vector) == FakeSentenceTransformer.dim

    def test_matches_the_batch_form(self):
        assert embeddings.embed_text("hello") == embeddings.embed_texts(["hello"])[0]
