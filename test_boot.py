"""
Boot-/Smoke-Test (in-process, ohne laufenden Server oder Ollama).

Faengt die haeufigsten Regressionen nach Backend-Umbauten ab:
- Die App importiert/bootet ohne Import-/NameError.
- Der StaticFiles-Mount ist die LETZTE Route (sonst werden API-Routen verschluckt).
- Alle Feature-Router sind eingehaengt.
- Zentrale, rein lokale GET-Endpunkte antworten mit 200.
- Die llama.cpp-„lokaler Anbieter"-Weiche (is_local) laesst lokale Modelle im
  Geheim-Modus durch und sperrt echte Cloud-Anbieter weiterhin.

Laeuft OHNE zusaetzliche Abhaengigkeit: `python test_boot.py` (nutzt die venv mit
FastAPI/Starlette-TestClient). Ist pytest installiert, funktioniert `pytest test_boot.py`
ebenso (die test_*-Funktionen nutzen nur `assert`).

Kein Ollama noetig: die geprueften Endpunkte sind rein lokal; `/api/models` (Ollama)
wird bewusst nicht geprueft, damit der Test schnell und offline gruen ist.
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import main  # noqa: E402  (Import bootet die App inkl. Startup-Seiteneffekte)
import core  # noqa: E402
from tools import llm as _llm  # noqa: E402

try:
    from fastapi.testclient import TestClient
except Exception as e:  # pragma: no cover
    print("[FEHLER] TestClient nicht verfuegbar:", e)
    raise

_client = TestClient(main.app)

# Rein lokale GET-Endpunkte (kein Ollama, kein Netz) — muessen 200 liefern.
_SMOKE_GETS = [
    "/api/profile",
    "/api/providers",
    "/api/rag/collections",
    "/api/rag/tiers",
    "/api/todo/tree",
    "/api/patente/projects",
    "/api/compare/projects",
    "/api/plans",
    "/api/varianten/projects",
    "/api/capacity/lists",
    "/api/feedback",
    "/api/conversations",
]


def test_app_boots():
    """Die FastAPI-App existiert und hat Routen."""
    assert main.app is not None
    assert len(main.app.routes) > 0


def test_static_mount_is_last():
    """Der StaticFiles-Catch-all-Mount MUSS die letzte Route sein — sonst wird jede
    danach eingehaengte API-Route unerreichbar."""
    last = main.app.routes[-1]
    assert type(last).__name__ == "Mount", (
        f"Letzte Route ist {type(last).__name__}, nicht der StaticFiles-Mount")


def test_all_feature_routers_included():
    """Alle Feature-Router sind via include_router eingehaengt (>= 39)."""
    included = [r for r in main.app.routes if type(r).__name__ == "_IncludedRouter"]
    assert len(included) >= 39, f"Nur {len(included)} Router eingehaengt (erwartet >= 39)"


def test_key_endpoints_ok():
    """Zentrale rein-lokale GET-Endpunkte antworten mit 200."""
    for path in _SMOKE_GETS:
        r = _client.get(path)
        assert r.status_code == 200, f"{path} -> HTTP {r.status_code}"


def test_llama_local_provider_gate():
    """Ein als lokal markierter llama.cpp-Anbieter wird im Geheim-Modus NICHT verworfen,
    ein echter Cloud-Anbieter dagegen schon (Regressionsschutz)."""
    provs = [
        {"id": "localllama", "name": "llama.cpp",
         "base_url": "http://127.0.0.1:8080/v1", "models": ["qwen"], "local": True},
        {"id": "cloud", "name": "OpenRouter",
         "base_url": "https://x/v1", "models": ["gpt-4o"]},
    ]
    _orig_providers = _llm.load_providers
    _orig_profile = core._load_profile
    try:
        _llm.load_providers = lambda: provs
        core._load_profile = lambda: {"local_only_mode": True}  # Geheim-Modus AN
        assert _llm.is_local("localllama::qwen") is True
        assert _llm.is_local("cloud::gpt-4o") is False
        # Geheim-Modus: lokaler llama.cpp bleibt, Cloud faellt auf DEFAULT zurueck
        assert core._pick_model("localllama::qwen") == "localllama::qwen"
        assert core._pick_model("cloud::gpt-4o") == core.DEFAULT_MODEL
    finally:
        _llm.load_providers = _orig_providers
        core._load_profile = _orig_profile


# ── Plain-Script-Runner (ohne pytest) ───────────────────────────────────────────
if __name__ == "__main__":
    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    _failed = 0
    for t in _tests:
        try:
            t()
            print(f"[OK]   {t.__name__}")
        except AssertionError as e:
            _failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            _failed += 1
            print(f"[ERR]  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(_tests) - _failed}/{len(_tests)} Tests gruen.")
    sys.exit(1 if _failed else 0)
