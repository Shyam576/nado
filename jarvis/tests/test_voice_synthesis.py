"""tests/test_voice_synthesis.py — voice.synthesize_to_file(), without real network/TTS calls."""

import voice


def test_synthesize_to_file_returns_none_for_empty_text():
    assert voice.synthesize_to_file("") is None
    assert voice.synthesize_to_file("   ") is None


def test_synthesize_to_file_uses_edge_tts(monkeypatch, tmp_path):
    written_paths = []

    async def _fake_edge_synthesize(text, out_path):
        written_paths.append(out_path)
        open(out_path, "wb").close()

    monkeypatch.setattr(voice, "_edge_synthesize_async", _fake_edge_synthesize)

    path = voice.synthesize_to_file("Hello there.")
    assert path is not None
    assert path.endswith(".mp3")
    assert path == written_paths[0]

    import os
    os.unlink(path)


def test_synthesize_to_file_falls_back_to_pyttsx3(monkeypatch):
    def _boom(text, out_path):
        raise RuntimeError("no internet")

    monkeypatch.setattr(voice, "_edge_synthesize_async", _boom)

    saved = {}

    class _FakeEngine:
        def save_to_file(self, text, path):
            saved["path"] = path
            open(path, "wb").close()

        def runAndWait(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(voice, "_make_tts_engine", lambda: _FakeEngine())

    path = voice.synthesize_to_file("Hello there.")
    assert path is not None
    assert path.endswith(".wav")
    assert saved["path"] == path

    import os
    os.unlink(path)


def test_synthesize_to_file_returns_none_when_both_engines_fail(monkeypatch):
    def _boom(text, out_path):
        raise RuntimeError("no internet")

    monkeypatch.setattr(voice, "_edge_synthesize_async", _boom)
    monkeypatch.setattr(voice, "_make_tts_engine", lambda: (_ for _ in ()).throw(RuntimeError("no tts")))

    assert voice.synthesize_to_file("Hello there.") is None
