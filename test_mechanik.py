# -*- coding: utf-8 -*-
"""Misst, ob KLEINE lokale Modelle das Werkzeug `mechanik_calc` richtig aufrufen.

Zweck: Die Maschinenbau-Rechenkerne (tools/mechanik.py) sollen auch von 3-4B-
Modellen bedienbar sein. Ob das klappt, haengt am Werkzeug-Schema und an den
Wachtern im Modul -- das laesst sich nur messen, nicht annehmen. Dieses Skript
misst es gegen die echte Tool-Definition.

Aufruf (Ollama muss laufen, Modelle installiert):

    python test_mechanik.py                      # granite4.2:3b + qwen3.5:4b
    python test_mechanik.py qwen3.5:4b           # nur ein Modell

Es zieht die Tool-Definition per `ast` aus routers/chat.py (ohne die App zu
laden), stellt deutsche Ingenieursfragen und fuehrt den zurueckgegebenen Aufruf
durch tools.mechanik.calc aus. Zwei Runden wie im echten Chat-Loop: scheitert
der erste Aufruf, bekommt das Modell die Fehlermeldung des Werkzeugs zurueck und
darf korrigieren.

Die Fragen enthalten gezielt die gemessenen Fallen: Kragarm gegen Traeger auf
zwei Stuetzen (Faktor 4 im Biegemoment), C/P/Drehzahl beim Waelzlager,
Moment gegen Spannung, vertauschte Passfedermasse.

WICHTIG beim Lesen der Ausgabe: "OK" heisst nur, dass eine Rechnung zustande
kam -- nicht, dass die richtige Rechenart gewaehlt wurde. Pruefe die
ausgegebenen Annahmen mit. Die Streuung zwischen Laeufen ist spuerbar; ein
einzelner Lauf ist kein Urteil.

Referenzmessung (Stand der Einfuehrung): qwen3.5:4b 7/8 -> 8/8,
granite4.2:3b 4/8 -> 5/8.
"""
import ast
import io
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from tools.mechanik import calc  # noqa: E402

OLLAMA = "http://localhost:11434/api/chat"


def tool_def():
    """mechanik_calc-Definition als Dict aus der Quelle holen."""
    tree = ast.parse(io.open("routers/chat.py", encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "TOOL_DEFS" for t in node.targets
        ):
            for el in node.value.elts:
                d = ast.literal_eval(el)
                if d.get("function", {}).get("name") == "mechanik_calc":
                    return d
    raise SystemExit("mechanik_calc nicht in TOOL_DEFS gefunden")


SYSTEM = (
    "Du bist ein Maschinenbauingenieur. Fuer jede Festigkeits-, Wellen-, "
    "Verbindungs- oder Antriebsgroesse benutzt du das Werkzeug mechanik_calc. "
    "Du rechnest solche Werte NIE selbst im Kopf. Uebernimm alle Zahlen und "
    "Einheiten aus der Frage vollstaendig in den Aufruf."
)

FRAGEN = [
    # Lastfall-Falle: Kragarm, nicht Traeger auf zwei Stuetzen
    "Ein Kragarm aus Stahl ist 1 m lang und wird am Ende mit 1 kN belastet. "
    "Der Querschnitt ist ein Rechteck 50 mm breit und 100 mm hoch. "
    "Wie gross ist die Biegespannung?",
    # Lastfall-Falle in die andere Richtung
    "Ein Traeger liegt auf zwei Stuetzen, 2 m Spannweite, mittig 5 kN. "
    "Profil I-Traeger h=200 b=100 Steg 8 Flansch 12. Biegespannung?",
    "Welche Torsionsspannung hat eine Vollwelle mit 30 mm Durchmesser bei 250 Nm?",
    "Ein Motor hat 7,5 kW bei 1450 1/min. Welches Drehmoment?",
    # Verwechslungs-Falle C/P/Drehzahl
    "Wie lange haelt ein Kugellager mit C = 20 kN bei P = 4 kN und 1500 1/min?",
    "Eine Stuetze aus Rohr 60x50 mm ist 2 m lang, beidseitig gelenkig, "
    "belastet mit 50 kN. Ist sie knicksicher?",
    "Welchen Durchmesser braucht eine Welle fuer 250 Nm bei tau_zul = 40 MPa?",
    "Passfeder: 250 Nm, Welle 40 mm, Feder 12x8 mm, 50 mm lang, Form A. Pressung?",
]


def ollama(model, messages, td):
    payload = {
        "model": model, "stream": False, "think": False,
        "messages": messages, "tools": [td],
    }
    req = urllib.request.Request(
        OLLAMA, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def args_of(call):
    a = call.get("function", {}).get("arguments") or {}
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except Exception:
            a = {"werte": a}
    return a


def run(model, td):
    ok1 = ok2 = 0
    for q in FRAGEN:
        print("\nFrage:", q[:68] + ("…" if len(q) > 68 else ""))
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]
        for runde in (1, 2):
            try:
                data = ollama(model, msgs, td)
            except Exception as e:
                print(f"  R{runde} FEHLER:", type(e).__name__, e)
                break
            msg = data.get("message") or {}
            calls = msg.get("tool_calls") or []
            if not calls:
                print(f"  R{runde} kein Aufruf:",
                      (msg.get("content") or "").strip()[:110].replace("\n", " "))
                break
            a = args_of(calls[0])
            w = a.get("werte")
            if w is None:
                w = {k: v for k, v in a.items() if k != "kind"}
            out = calc(str(a.get("kind", "")), w)
            erg = [l for l in out.split("\n") if l.startswith("- **")]
            gut = bool(erg) and "unvollständig" not in out and "Widersprüchlich" not in out \
                and "Unbekannte" not in out
            print(f"  R{runde} {a.get('kind')!r} | {str(w)[:78]}")
            print(f"     -> {'OK  ' if gut else 'FEHL'} {(erg[0] if erg else out.splitlines()[0])[:100]}")
            if runde == 1 and gut:
                ok1 += 1
                ok2 += 1
                break
            if gut:
                ok2 += 1
                break
            if runde == 2:
                break
            # Fehlermeldung zurueckgeben — wie im echten Werkzeug-Loop
            msgs.append({"role": "assistant", "content": "", "tool_calls": calls})
            msgs.append({"role": "tool", "content": out})
    n = len(FRAGEN)
    print(f"\n== {model}: 1. Versuch {ok1}/{n} · mit Korrekturrunde {ok2}/{n} ==\n")


def main():
    td = tool_def()
    for model in sys.argv[1:] or ["granite4.2:3b", "qwen3.5:4b"]:
        print("=" * 72)
        print("MODELL:", model)
        print("=" * 72)
        run(model, td)


if __name__ == "__main__":
    main()
