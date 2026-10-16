from pathlib import Path

from app.ingestion.chunker import chunk_artifact, chunk_artifacts, split_text
from app.ingestion.loaders import load_dataset

ROOT = Path(__file__).resolve().parents[2]
SENTENCES = [f"Sentence number {i} explains something about the payment gateway pool." for i in range(40)]


def test_short_text_is_one_chunk_and_empty_is_none():
    assert split_text("hello world", 900, 150) == ["hello world"]
    assert split_text("  \n ", 900, 150) == []


def test_chunks_respect_size_and_cover_every_sentence():
    text = " ".join(SENTENCES)
    chunks = split_text(text, 300, 80)
    assert len(chunks) > 3 and all(len(c) <= 300 for c in chunks)
    joined = "\n".join(chunks)
    assert all(s in joined for s in SENTENCES), "no sentence may be lost or cut"


def test_consecutive_chunks_overlap_on_sentence_boundaries():
    chunks = split_text(" ".join(SENTENCES), 300, 100)
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.split(". ")[0] in prev or nxt.split(".")[0] in prev
        assert nxt != prev


def test_zero_overlap_has_no_repeats():
    chunks = split_text(" ".join(SENTENCES), 300, 0)
    seen = [s for c in chunks for s in c.split(". ")]
    assert len(seen) == len(set(seen))


def test_overlong_sentence_and_token_are_split_without_infinite_loop():
    chunks = split_text("word " * 500, 100, 20)
    assert all(len(c) <= 100 for c in chunks) and len(chunks) > 20
    blob = split_text("x" * 1000, 100, 20)
    assert all(len(c) <= 100 for c in blob) and "".join(blob) == "x" * 1000


def test_lines_stay_whole_for_chat_style_text():
    lines = [f"[2026-01-01 10:{i:02d}] Ravi: message {i} " + "word " * 12 for i in range(30)]
    for c in split_text("\n".join(lines), 400, 100):
        assert all(ln.startswith("[2026") for ln in c.splitlines())


def test_sample_dataset_chunks():
    arts = load_dataset(ROOT / "data" / "sample").artifacts
    chunks = chunk_artifacts(arts)
    assert chunks and all(0 < len(c.text) <= 900 for c in chunks)
    assert len({c.id for c in chunks}) == len(chunks)
    assert {c.artifact_id for c in chunks} == {a.id for a in arts}, "every artifact yields a chunk"
    c = chunk_artifact(arts[0])[0]
    assert (c.title, c.author, c.artifact_type, c.timestamp, c.url_or_path) == (
        arts[0].title, arts[0].author, arts[0].type, arts[0].timestamp, arts[0].url_or_path)
    assert chunk_artifact(arts[0]) == chunk_artifact(arts[0])  # deterministic
    # the gateway fix must be retrievable from a single chunk
    assert any("gwctl pool-status" in c.text and "stale" in c.text for c in chunks)
