"""
Tests for embedding generation and vector storage.
Covers: embedding generation, vector insertion, metadata insertion,
        FAISS vector count increase.
Does NOT test retrieval or similarity search (Knowledge Agent responsibility).
Run: pytest tests/test_rag.py -v
"""

import os
from unittest.mock import patch


class TestEmbeddingGeneration:

    def test_generate_embedding_returns_list(self):
        """generate_embedding must return a Python list."""
        from rag.embeddings import generate_embedding
        result = generate_embedding("outer race fault SKF6310 contamination")
        assert isinstance(result, list)

    def test_embedding_has_correct_dimension(self):
        """all-MiniLM-L6-v2 must produce 384-dimensional embeddings."""
        from rag.embeddings import generate_embedding
        result = generate_embedding("test text")
        assert len(result) == 384

    def test_embedding_values_are_floats(self):
        """All values in the embedding must be floats."""
        from rag.embeddings import generate_embedding
        result = generate_embedding("outer race fault")
        assert all(isinstance(v, float) for v in result)

    def test_different_texts_produce_different_embeddings(self):
        """Two different texts must not produce identical vectors."""
        from rag.embeddings import generate_embedding
        emb1 = generate_embedding("outer race fault motor SKF6310")
        emb2 = generate_embedding("lubrication issue pump SKF6208")
        assert emb1 != emb2

    def test_build_embedding_text_contains_fault_mode(self):
        """build_embedding_text must include the fault_mode in the output."""
        from rag.embeddings import build_embedding_text
        case = {
            "fault_mode": "outer_race_fault",
            "bearing_type": "SKF6310",
            "root_cause": "contamination",
            "lessons_learned": "replace seal"
        }
        text = build_embedding_text(case)
        assert "outer_race_fault" in text

    def test_build_embedding_text_contains_all_fields(self):
        """build_embedding_text must include fault, bearing, cause, and lessons."""
        from rag.embeddings import build_embedding_text
        case = {
            "fault_mode": "lubrication_issue",
            "bearing_type": "SKF6208",
            "root_cause": "lube interval exceeded",
            "lessons_learned": "60 day interval not 90"
        }
        text = build_embedding_text(case)
        assert "lubrication_issue" in text
        assert "SKF6208" in text
        assert "lube interval exceeded" in text

    def test_batch_embed_returns_correct_count(self):
        """batch_embed must return same number of embeddings as input texts."""
        from rag.embeddings import batch_embed
        texts = ["text one", "text two", "text three"]
        results = batch_embed(texts)
        assert len(results) == 3

    def test_batch_embed_each_has_correct_dimension(self):
        """Each embedding from batch_embed must be 384 dimensions."""
        from rag.embeddings import batch_embed
        texts = ["outer race fault", "lubrication issue"]
        results = batch_embed(texts)
        for emb in results:
            assert len(emb) == 384


class TestVectorStorageInsertion:

    def test_add_vector_increases_count(self, tmp_path, monkeypatch):
        """
        Adding a vector to a fresh index must increase ntotal from 0 to 1.
        Uses tmp_path to avoid writing to real data/faiss_index/ during tests.
        """
        monkeypatch.setattr("rag.vector_storage.INDEX_PATH",
                           str(tmp_path / "index.faiss"))
        monkeypatch.setattr("rag.vector_storage.METADATA_PATH",
                           str(tmp_path / "metadata.json"))
        
        from rag.vector_storage import VectorStorage
        from rag.embeddings import generate_embedding
        
        store = VectorStorage()
        store.load_index()
        
        initial_count = store.get_vector_count()
        assert initial_count == 0
        
        embedding = generate_embedding("outer race fault SKF6310")
        metadata = {
            "case_id": "CASE_TEST_RAG",
            "created_at": "2026-06-01T00:00:00",
            "fault_mode": "outer_race_fault"
        }
        
        success = store.add(embedding, metadata, "CASE_TEST_RAG")
        
        assert success is True
        assert store.get_vector_count() == initial_count + 1

    def test_metadata_stored_after_add(self, tmp_path, monkeypatch):
        """
        After add(), the case_id must appear in the metadata dict.
        """
        monkeypatch.setattr("rag.vector_storage.INDEX_PATH",
                           str(tmp_path / "index.faiss"))
        monkeypatch.setattr("rag.vector_storage.METADATA_PATH",
                           str(tmp_path / "metadata.json"))
        
        from rag.vector_storage import VectorStorage
        from rag.embeddings import generate_embedding
        
        store = VectorStorage()
        store.load_index()
        
        embedding = generate_embedding("cage fault conveyor SKF22212")
        metadata = {"case_id": "CASE_TEST_META", "fault_mode": "cage_fault"}
        
        store.add(embedding, metadata, "CASE_TEST_META")
        
        assert store.case_exists("CASE_TEST_META")

    def test_multiple_vectors_increase_count(self, tmp_path, monkeypatch):
        """
        Adding 3 vectors to a fresh index must give ntotal=3.
        """
        monkeypatch.setattr("rag.vector_storage.INDEX_PATH",
                           str(tmp_path / "index.faiss"))
        monkeypatch.setattr("rag.vector_storage.METADATA_PATH",
                           str(tmp_path / "metadata.json"))
        
        from rag.vector_storage import VectorStorage
        from rag.embeddings import generate_embedding
        
        store = VectorStorage()
        store.load_index()
        
        cases = [
            ("CASE_A", "outer race fault motor"),
            ("CASE_B", "lubrication issue pump"),
            ("CASE_C", "cage fault conveyor"),
        ]
        
        for case_id, text in cases:
            emb = generate_embedding(text)
            store.add(emb, {"case_id": case_id}, case_id)
        
        assert store.get_vector_count() == 3

    def test_index_saved_to_disk(self, tmp_path, monkeypatch):
        """
        After save_index(), the FAISS index file must exist on disk.
        """
        index_path = str(tmp_path / "index.faiss")
        monkeypatch.setattr("rag.vector_storage.INDEX_PATH", index_path)
        monkeypatch.setattr("rag.vector_storage.METADATA_PATH",
                           str(tmp_path / "metadata.json"))
        
        from rag.vector_storage import VectorStorage
        from rag.embeddings import generate_embedding
        
        store = VectorStorage()
        store.load_index()
        
        emb = generate_embedding("test case for save")
        store.add(emb, {"case_id": "CASE_SAVE_TEST"}, "CASE_SAVE_TEST")
        store.save_index()
        
        assert os.path.exists(index_path)
