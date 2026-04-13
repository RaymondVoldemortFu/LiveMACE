import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.agent import memory_pinecone


class _ConflictError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.status = 409


def test_pinecone_init_ignores_already_exists_conflict(monkeypatch):
    class _FakeModel:
        def get_sentence_embedding_dimension(self):
            return 384

    class _FakeListIndexes:
        @staticmethod
        def names():
            return []

    class _FakePinecone:
        def __init__(self, api_key):
            self.api_key = api_key
            self.created = False

        def list_indexes(self):
            return _FakeListIndexes()

        def create_index(self, **kwargs):
            self.created = True
            raise _ConflictError("ALREADY_EXISTS")

        def Index(self, name):
            return {"index_name": name}

    monkeypatch.setattr(memory_pinecone, "PINECONE_AVAILABLE", True)
    monkeypatch.setattr(memory_pinecone, "SENTENCE_TRANSFORMERS_AVAILABLE", True)
    monkeypatch.setattr(memory_pinecone, "SentenceTransformer", lambda _: _FakeModel())
    monkeypatch.setattr(memory_pinecone, "ServerlessSpec", lambda **kwargs: kwargs)
    monkeypatch.setattr(memory_pinecone, "Pinecone", _FakePinecone)

    service = memory_pinecone.PineconeMemory(
        api_key="test-key",
        index_name="agent-memories",
        environment="us-east-1",
    )

    assert service.embedding_dim == 384
    assert service.index == {"index_name": "agent-memories"}
