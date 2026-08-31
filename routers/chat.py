"""Router: Chat (Tool-Loop, TOOL_DEFS, _execute_tool, _chat_generator)

Aus ``main.py`` ausgelagert (reines Backend-Refactoring, kein Verhaltenswechsel).
Geteilte Namen kommen ueber ``from core import *``.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import httpx
from fastapi import (APIRouter, Body, Depends, File, Form, HTTPException, Query,
                     Request, UploadFile)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse, Response,
                               StreamingResponse)
from pydantic import BaseModel

import db as _db
from tools import llm as _llm
from tools import transcribe as _transcribe

from core import *  # noqa: F401,F403  (geteilte Kernflaeche)
import core as _core  # noqa: F401

router = APIRouter()


# ── Tool-Definitionen für Ollama ──────────────────────────────────────────────

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Sucht im Internet nach aktuellen Informationen, Fakten, News oder technischen Inhalten. "
                "Verwende dieses Tool immer, wenn aktuelle oder unbekannte Informationen benötigt werden."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Die Suchanfrage"},
                    "num_results": {"type": "integer", "default": 6, "description": "Anzahl Ergebnisse"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": (
                "Führt Python-Code für Berechnungen aus. Gibt numerische Ergebnisse, Statistiken "
                "oder Tabellen aus. Nutze print() für Ausgaben. math und numpy sind verfügbar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Auszuführender Python-Code"}
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_presentation",
            "description": (
                "Erstellt eine Präsentation mit mehreren Folien. "
                "Die Folien werden auf dem HTML5-Canvas gerendert und können als PPTX exportiert werden."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "theme": {
                        "type": "string",
                        "enum": ["dark", "blue", "light", "green"],
                        "default": "dark",
                    },
                    "slides": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "layout": {
                                    "type": "string",
                                    "enum": ["title", "bullets", "two-column", "section", "blank"],
                                },
                                "title": {"type": "string"},
                                "content": {"type": "string"},
                                "bullets": {"type": "array", "items": {"type": "string"}},
                                "left": {"type": "string"},
                                "right": {"type": "string"},
                            },
                        },
                    },
                },
                "required": ["title", "slides"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unit_convert",
            "description": (
                "Rechnet physikalische Einheiten um. Unterstützt Länge, Masse, Kraft, Druck, "
                "Energie, Temperatur, Drehmoment, Leistung, Fläche, Volumen und mehr."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "number", "description": "Zahlenwert"},
                    "from_unit": {"type": "string", "description": "Quelleinheit (z.B. 'MPa', 'kN', 'inch', 'lbf')"},
                    "to_unit": {"type": "string", "description": "Zieleinheit (z.B. 'Pa', 'N', 'mm', 'N')"},
                },
                "required": ["value", "from_unit", "to_unit"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "solve_equation",
            "description": (
                "Löst algebraische Gleichungen oder Gleichungssysteme symbolisch. "
                "Gibt exakte und numerische Lösungen zurück. Beispiel: '2*x**2 + 3*x - 5 = 0'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Gleichung als String, z.B. 'x**2 - 4 = 0' oder 'sin(x) - 0.5'"
                    },
                    "variable": {
                        "type": "string",
                        "default": "x",
                        "description": "Variable nach der aufgelöst wird"
                    },
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plot_chart",
            "description": (
                "Erstellt ein 2D-Diagramm aus DISKRETEN Wertepaaren (Mess-/Datenpunkte) — Linien-, "
                "Balken- oder Streudiagramm — und zeigt es direkt an. Ideal für tabellarische Messdaten, "
                "Kennlinien aus Datenpunkten etc. NICHT für mathematische Funktionen verwenden "
                "(f(x)=…, sin(x), x^2, sqrt(x)) — dafür ist plot_function da; sonst entsteht aus wenigen "
                "Stützpunkten ein grober Zickzack-Linienzug statt einer glatten Kurve."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "x_data": {"type": "array", "items": {"type": "number"}, "description": "X-Werte"},
                    "y_data": {"type": "array", "items": {"type": "number"}, "description": "Y-Werte (Hauptreihe)"},
                    "title": {"type": "string", "description": "Diagrammtitel"},
                    "x_label": {"type": "string", "description": "Bezeichnung X-Achse"},
                    "y_label": {"type": "string", "description": "Bezeichnung Y-Achse"},
                    "chart_type": {
                        "type": "string",
                        "enum": ["line", "bar", "scatter"],
                        "default": "line",
                    },
                    "series_label": {"type": "string", "description": "Legende Hauptreihe"},
                    "y2_data": {"type": "array", "items": {"type": "number"}, "description": "Optional: zweite Y-Reihe (gestrichelt)"},
                    "y2_label": {"type": "string", "description": "Legende zweite Reihe"},
                },
                "required": ["x_data", "y_data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plot_function",
            "description": (
                "Zeichnet den Graphen einer mathematischen Funktion und zeigt ihn direkt an. "
                "IMMER verwenden, wenn der Nutzer eine Funktion nennt (z. B. f(x)=x^2, sin(x), "
                "sqrt(x), 2x+1) oder einen Graphen/Plot/Verlauf/eine Kennlinie wünscht. "
                "Versteht ^ als Potenz, implizite Multiplikation (2x) und einen 'f(x)='/'y='-Vorsatz; "
                "mehrere Funktionen mit ';' trennen, um sie zu vergleichen."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Funktionsterm, z. B. 'x^2', 'sin(x)', 'sqrt(x)'. Mehrere mit ';' getrennt."},
                    "var": {"type": "string", "default": "x", "description": "Variable (Standard: x)"},
                    "x_min": {"type": "number", "default": -10, "description": "Untere Bereichsgrenze"},
                    "x_max": {"type": "number", "default": 10, "description": "Obere Bereichsgrenze"},
                    "title": {"type": "string", "description": "Diagrammtitel (optional)"},
                    "x_label": {"type": "string", "description": "Bezeichnung X-Achse (optional)"},
                    "y_label": {"type": "string", "description": "Bezeichnung Y-Achse (optional)"},
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "material_lookup",
            "description": (
                "Sucht Werkstoffeigenschaften in der integrierten Datenbank: E-Modul, Streckgrenze, "
                "Zugfestigkeit, Dichte, Wärmeausdehnung etc. Unterstützt Stähle, Alu, Titan, "
                "Gusseisen, Kunststoffe, NE-Metalle."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Werkstoffbezeichnung, z.B. 'S355', '42CrMo4', '1.4301', 'AlMg3', 'PEEK'"
                    },
                    "prop": {
                        "type": "string",
                        "description": "Optionale spezifische Eigenschaft, z.B. 'E_GPa', 'Rm_MPa', 'density'"
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bolt_calculator",
            "description": (
                "Schraubenauslegung nach VDI 2230 (vereinfacht): berechnet Spannungsquerschnitt, "
                "Zugspannung, Vergleichsspannung, Anzugsmoment und Auslastung."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "d_nom": {"type": "number", "description": "Nenndurchmesser [mm], z.B. 12 für M12"},
                    "pitch": {"type": "number", "description": "Gewindesteigung [mm], z.B. 1.75 für M12"},
                    "f_axial": {"type": "number", "description": "Axialkraft / Betriebskraft [kN]"},
                    "mu": {"type": "number", "default": 0.15, "description": "Reibungszahl (Standard: 0,15)"},
                    "material_class": {
                        "type": "string",
                        "enum": ["4.6", "5.6", "6.8", "8.8", "10.9", "12.9"],
                        "default": "8.8",
                        "description": "Festigkeitsklasse der Schraube",
                    },
                    "f_transverse": {"type": "number", "default": 0, "description": "Querkraft [kN] (optional)"},
                },
                "required": ["d_nom", "pitch", "f_axial"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": (
                "Erstellt einen formatierten Ingenieurbericht als PDF (LaTeX) oder DOCX. "
                "Enthält Titelseite, Inhaltsverzeichnis, Abschnitte mit Text, Gleichungen und Tabellen. "
                "Gibt einen Download-Link zurück."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Berichtstitel"},
                    "author": {"type": "string", "description": "Autor (optional)"},
                    "sections": {
                        "type": "array",
                        "description": "Abschnitte des Berichts",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "content": {"type": "string", "description": "Fließtext (Absätze mit Leerzeile trennen)"},
                                "equations": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "LaTeX-Gleichungen (ohne $), z.B. ['\\\\sigma = F/A']"
                                },
                                "table": {
                                    "type": "object",
                                    "properties": {
                                        "headers": {"type": "array", "items": {"type": "string"}},
                                        "rows": {"type": "array", "items": {"type": "array"}},
                                    },
                                },
                                "subsections": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "heading": {"type": "string"},
                                            "content": {"type": "string"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
                "required": ["title", "sections"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_spreadsheet",
            "description": (
                "Erstellt eine Tabelle/Spreadsheet mit Spaltenüberschriften und Datenzeilen. "
                "Wird im Canvas gerendert und kann als XLSX exportiert werden."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "headers": {"type": "array", "items": {"type": "string"}},
                    "rows": {"type": "array", "items": {"type": "array"}},
                },
                "required": ["headers", "rows"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_planner",
            "description": (
                "Berechnet eine Route von einem Ort A zu einem Ort B über OpenStreetMap "
                "(Geocoding via Nominatim, Routing via OSRM) und zeigt sie als interaktive "
                "Karte an. Verwende dieses Tool immer, wenn nach dem Weg, der Strecke, "
                "der Fahrzeit oder der Route zwischen zwei Orten gefragt wird."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Startort, z.B. 'Stuttgart' oder 'Hauptbahnhof München'"},
                    "destination": {"type": "string", "description": "Zielort, z.B. 'Berlin' oder 'Marienplatz München'"},
                    "profile": {
                        "type": "string",
                        "enum": ["driving", "walking", "cycling"],
                        "default": "driving",
                        "description": "Fortbewegungsart (Auto, zu Fuß, Fahrrad)",
                    },
                },
                "required": ["origin", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_diagram",
            "description": (
                "Erstellt ein Datenfluss-, Ablauf-, Sequenz-, Klassen- oder "
                "Zustandsdiagramm mit Mermaid-Syntax. Verwende dieses Tool immer, "
                "wenn du einen Prozess, Datenfluss, eine Systemarchitektur oder "
                "Abhängigkeiten zwischen Komponenten grafisch darstellen möchtest."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "diagram_type": {
                        "type": "string",
                        "enum": ["flowchart", "sequenceDiagram", "classDiagram",
                                 "stateDiagram-v2", "erDiagram", "gantt", "pie"],
                        "description": "Mermaid-Diagrammtyp",
                    },
                    "definition": {
                        "type": "string",
                        "description": "Vollständige Mermaid-Diagrammdefinition",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optionaler Titel des Diagramms",
                    },
                },
                "required": ["diagram_type", "definition"],
            },
        },
    },
]

ALL_TOOL_NAMES = {t["function"]["name"] for t in TOOL_DEFS}

# Code-Interpreter-Tool für den Chat — bewusst NICHT in TOOL_DEFS, damit es nur dann
# angeboten wird, wenn das Profil-Häkchen »Erweiterte Chat-Werkzeuge« gesetzt ist
# (und die serverseitige Ausführung erlaubt ist). Führt Python in derselben Sandbox
# wie der Code-Tab aus (stdout/stderr + matplotlib-Bilder).
_RUN_PYTHON_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": (
            "Führt Python-Code in einer sicheren Sandbox aus, um eine Aufgabe rechnerisch zu "
            "LÖSEN oder zu PRÜFEN (Berechnungen, Datenanalyse, Simulationen, Diagramme mit "
            "matplotlib). Liefert stdout/stderr; erzeugte Plots werden dem Nutzer angezeigt. "
            "Nutze es bei komplexen/zahlenlastigen Fragen statt selbst zu rechnen. Kein Datei- "
            "oder Netzzugriff."
        ),
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Auszuführender Python-Code"}},
            "required": ["code"],
        },
    },
}

# Bild-Werkzeug für den Chat — nur im Assistent-Modus angeboten (und nur wenn ein
# Bildmodell konfiguriert ist). Erzeugt ein Bild direkt aus der Unterhaltung.
_GENERATE_IMAGE_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "generate_image",
        "description": (
            "Erzeugt ein BILD aus einer Text-Beschreibung (Foto, Illustration, Konzept, Logo …). "
            "Verwende es, wenn der Nutzer ein Bild/eine Grafik/ein Motiv erzeugt haben möchte. "
            "Formuliere eine anschauliche englische oder deutsche Prompt-Beschreibung."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Bildbeschreibung"},
                "size": {"type": "string", "enum": ["square", "landscape", "portrait"],
                         "description": "Seitenverhältnis (Standard square)"},
            },
            "required": ["prompt"],
        },
    },
}

# Wissensdatenbank-Werkzeug (Assistent-Modus): durchsucht die persönlichen RAG-Sammlungen
# des Nutzers. Nur angeboten, wenn mindestens eine nicht-leere Sammlung existiert. Die
# Einbettung der Anfrage braucht lokales Ollama (Embedding-Modell) — schlägt sie fehl,
# meldet das Werkzeug das klar zurück. NICHT in TOOL_DEFS (gated).
_SEARCH_KB_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": (
            "Durchsucht die persönliche WISSENSDATENBANK des Nutzers (hinterlegte Dokumente, "
            "Notizen, PDFs in den RAG-Sammlungen) nach relevanten Textstellen und liefert die "
            "besten Treffer mit Quellenangabe. Nutze es, wenn sich die Frage auf eigene/interne "
            "Unterlagen bezieht oder der Nutzer etwas aus seinen hinterlegten Dokumenten wissen "
            "will. Stütze deine Antwort dann auf die gefundenen Stellen und nenne die Quelle."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Suchbegriff/Frage in natürlicher Sprache"},
            },
            "required": ["query"],
        },
    },
}

# Patentrecherche-Werkzeug (Assistent-Modus): sucht Patente (EPO-OPS falls konfiguriert,
# sonst Google-Patents-Fallback). Braucht Web-Zugang → nur wenn Websuche erlaubt ist
# (im Geheim-/Hartman-Modus aus). NICHT in TOOL_DEFS (gated).
_SEARCH_PATENTS_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "search_patents",
        "description": (
            "Recherchiert PATENTE zu einem technischen Thema und liefert die wichtigsten "
            "Treffer (Nummer, Titel, Anmelder, Datum, Kurzfassung). Nutze es, wenn der Nutzer "
            "nach Patenten, Schutzrechten oder Stand der Technik zu einer Technologie fragt."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Technisches Stichwort / Thema (Boolesch AND/OR/NOT erlaubt)"},
                "assignee": {"type": "string", "description": "Optional: Anmelder/Rechteinhaber"},
                "ipc": {"type": "string", "description": "Optional: IPC-/CPC-Klasse, z. B. B60L"},
                "max_results": {"type": "integer", "description": "Anzahl Treffer (Standard 8, max 20)"},
            },
            "required": ["query"],
        },
    },
}

# ── Assistent-Modus: weitere Tabs als aufrufbare Agenten ──────────────────────
# Diese vier Werkzeuge kapseln Tab-Fähigkeiten. Drei davon (deep_research,
# run_workflow, ask_todo) sind TERMINAL-STREAMING-Agenten: ihr Ergebnis wird live in
# die Chat-Blase gestreamt (Fortschritt + Endantwort), danach endet der Turn — das
# Modell paraphrasiert NICHT nach. solve_math ist ein normales Werkzeug (verifiziertes
# SymPy-Ergebnis zurück ans Modell). Alle NICHT in TOOL_DEFS (gated → nur im Assistent-Modus).

# Namen der Terminal-Streaming-Agenten (Ergebnis = Antwort, Loop endet danach).
_STREAM_AGENTS = {"deep_research", "run_workflow", "ask_todo"}

_DEEP_RESEARCH_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "deep_research",
        "description": (
            "Führt eine TIEFE, mehrstufige WEB-RECHERCHE zu einem Thema durch: zerlegt es "
            "automatisch in Teilaspekte, durchsucht das Web je Aspekt und schreibt einen "
            "quellen-gestützten Recherchebericht. Nutze es für gründliche Recherchen mit "
            "aktuellen Fakten (Marktüberblick, Technologievergleich, Hintergrundbericht) — "
            "NICHT für einfache Einzelfragen (dafür web_search)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Recherche-Thema"},
                "focus": {"type": "string", "description": "Optionaler Schwerpunkt/Blickwinkel"},
                "depth": {"type": "integer", "description": "Zahl der Teilaspekte (3–12, Standard 6)"},
                "words": {"type": "integer", "description": "Ungefähre Länge des Berichts (200–4000, Standard 900)"},
            },
            "required": ["topic"],
        },
    },
}

_RUN_WORKFLOW_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "run_workflow",
        "description": (
            "Arbeitet eine AUFGABE IN MEHREREN SCHRITTEN ab: jede Teilaufgabe wird "
            "nacheinander gelöst, Zwischenergebnisse fließen als Kontext in den nächsten "
            "Schritt, am Ende entsteht ein zusammenhängendes Gesamtergebnis. Nutze es für "
            "komplexe, mehrteilige Aufträge, die sich sinnvoll in eine Schrittfolge zerlegen "
            "lassen (z. B. „recherchiere X, vergleiche mit Y, leite eine Empfehlung ab“). "
            "Formuliere die Schritte selbst als knappe Anweisungen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {"type": "array", "items": {"type": "string"},
                          "description": "Geordnete Liste knapper Schritt-Anweisungen (2–12)"},
                "goal": {"type": "string", "description": "Optionales übergeordnetes Ziel des Ablaufs"},
                "web": {"type": "boolean", "description": "Bei true je Schritt eine Websuche (nur wenn Websuche erlaubt)"},
            },
            "required": ["steps"],
        },
    },
}

_ASK_TODO_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "ask_todo",
        "description": (
            "Beantwortet eine Frage über den persönlichen TO-DO-/PROJEKTBESTAND des Nutzers "
            "(Aufgaben, Fristen, Zuständige/Kollegen, Abhängigkeiten). Nutze es, wenn sich die "
            "Frage auf die eigenen Aufgaben/Projekte/Termine oder auf beteiligte Personen "
            "bezieht (z. B. „Was ist diese Woche fällig?“, „Woran arbeitet Kollege X?“). "
            "Läuft lokal/vertraulich."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Frage in natürlicher Sprache"},
            },
            "required": ["question"],
        },
    },
}

_SOLVE_MATH_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "solve_math",
        "description": (
            "Löst/prüft eine mathematische Aufgabe DETERMINISTISCH mit SymPy (Gleichung lösen, "
            "Ableitung, Stammfunktion, Faktorisieren, Vereinfachen) und liefert das verifizierte "
            "Ergebnis. Nutze es für symbolische/algebraische Aufgaben, damit die Lösung "
            "garantiert korrekt ist. Gib den Ausdruck SymPy-auswertbar an (Potenz mit **, "
            "Gleichungen mit =, keine Worte), z. B. \"x**2-5*x+6=0\" oder \"sin(x)*x\"."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "SymPy-auswertbarer Ausdruck/Gleichung"},
                "goal": {"type": "string", "enum": ["solve", "diff", "integrate", "factor", "simplify"],
                         "description": "Was zu tun ist (Standard: passend zum Ausdruck)"},
            },
            "required": ["expression"],
        },
    },
}


# ── Pydantic-Modelle ──────────────────────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str
    content: str
    files: Optional[List[str]] = None


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: str = DEFAULT_MODEL
    agent_id: Optional[str] = None
    use_tools: bool = True
    web_search: bool = False   # Websuche-Tool nur anbieten, wenn der Schalter aktiv ist
    tools: Optional[List[str]] = None   # explizite Werkzeug-Wahl (z. B. Matrix-Spalte): active_tools darauf einschränken
    conversation_id: Optional[str] = None
    rag_collections: List[str] = []
    science: bool = False   # Wissenschaftsmodus (z. B. Matrix-Recherche)
    show_thinking: bool = False   # Denkprozess des Modells als eigene SSE-Frames mitsenden








@router.post("/api/chat")
async def chat(request: ChatRequest):
    return StreamingResponse(
        _chat_generator(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )





# Plot-Absicht + Funktion(en) deterministisch aus dem Nutzertext ziehen, damit ein
# Funktionsgraph auch dann erscheint, wenn das kleine Modell plot_function NICHT von
# selbst aufruft. Wird als Fallback genutzt (nur wenn das Modell nicht schon geplottet hat).
_PLOT_INTENT = re.compile(
    r"(?i)\b(plotte?|plotten|zeichne|graph|graf|grafik|verlauf|kennlinie|skizziere|plot)\b")
_PLOT_RANGE = re.compile(
    r"(?i)(?:von|from)\s*(-?\d+(?:[.,]\d+)?)\s*(?:bis|to|–|—|\.\.+|-)\s*(-?\d+(?:[.,]\d+)?)")


def _extract_plot_request(text: str):
    """Gibt (expression, x_min, x_max) zurück, wenn der Text einen Funktionsplot
    verlangt, sonst None. expression kann mehrere Terme mit ``;`` enthalten."""
    if not text or not _PLOT_INTENT.search(text):
        return None
    x_min, x_max = -10.0, 10.0
    work = text
    m = _PLOT_RANGE.search(text)
    if m:
        try:
            x_min = float(m.group(1).replace(",", "."))
            x_max = float(m.group(2).replace(",", "."))
        except ValueError:
            pass
        work = text[:m.start()] + " ; " + text[m.end():]
    # Plot-Verben + typische Füllwörter entfernen, damit nur die Funktion übrig bleibt
    work = _PLOT_INTENT.sub(" ", work)
    work = re.sub(r"(?i)\b(den|der|die|das|von|vom|im|bereich|funktion|term|kurve|"
                  r"mir|bitte|einmal|mal|als|graphen?)\b", " ", work)
    # Verbindungen → Trenner, damit „x^2 und cos(x)" zwei Funktionen werden
    work = re.sub(r"(?i)\s+(und|and|sowie|,)\s+", " ; ", work)
    exprs = []
    # 1) explizite f(x)=… / y=… Definitionen
    for mm in re.finditer(r"(?:[A-Za-z]\w*\s*\([^)]*\)|y)\s*=\s*([^;]+)", work):
        exprs.append(mm.group(1))
    # 2) sonst: math-artige Tokens, die die Variable x enthalten
    if not exprs:
        for tok in re.split(r"[;]+", work):
            tok = tok.strip()
            if "x" in tok and re.search(r"(?i)[\^*/+]|x\d|\dx|sin|cos|tan|sqrt|exp|log|abs|x\^|x\b", tok):
                cand = re.search(r"[0-9A-Za-z_.^*/+()\-\s]*x[0-9A-Za-z_.^*/+()\-\s]*", tok)
                if cand:
                    exprs.append(cand.group(0))
    # säubern: Rand-Whitespace/Satzzeichen weg, muss die Variable x enthalten
    cleaned = []
    for e in exprs:
        e = e.strip(" .:;,").strip()
        if e and "x" in e and re.search(r"[0-9x)]\s*$", e):
            cleaned.append(e)
    if not cleaned:
        return None
    return ";".join(cleaned[:4]), x_min, x_max


async def _force_answer(messages: list, model: str, num_ctx: int) -> tuple:
    """Rettungsaufruf, wenn der Werkzeug-Loop endet, ohne sichtbaren Text zu liefern
    (Reasoning-Modell steckt alles ins »Denken«, oder Max-Iterationen bei tiefer
    Web-Recherche erreicht). Erzwingt EINE finale Antwort ohne Werkzeuge/Denken aus dem
    bereits gesammelten Kontext (inkl. der Tool-Ergebnisse). Gibt (text, tok_in, tok_out)."""
    msgs = list(messages) + [{
        "role": "user",
        "content": ("Beantworte jetzt die ursprüngliche Frage VOLLSTÄNDIG und direkt auf "
                    "Deutsch – nutze die bereits gesammelten Informationen/Suchergebnisse. "
                    "KEINE weiteren Werkzeugaufrufe, KEIN internes Nachdenken (<think>), "
                    "sondern unmittelbar die ausformulierte Antwort."),
    }]
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": msgs, "options": {"num_ctx": num_ctx}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        j = resp.json()
        ti, to = _llm_tok(j)
        txt = ((j.get("message", {}) or {}).get("content", "") or "")
        txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.DOTALL)
        txt = re.sub(r"</?think>", "", txt).strip()
        return txt, ti, to
    except Exception:
        return "", 0, 0


async def _chat_generator(request: ChatRequest):
    system_prompt: Optional[str] = None
    active_tools = TOOL_DEFS
    model = request.model
    _agent_fixed_model = False   # Agent gibt explizit ein Modell vor
    code_capable = False   # Programmier-Agent → Code aus der Antwort in die IDE übernehmen
    _presenter_dedicated = False   # echter Präsentations-Agent (Canvas-Fallback erlaubt)
    _log_t0 = time.time()
    _tools_called: list = []
    _last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
    # Kontextfenster früh bestimmen, damit ALLE Aufrufe dieses Ablaufs (inkl. der
    # adaptiven Prompt-Ableitung) dasselbe num_ctx nutzen → kein Neuladen des Modells.
    _num_ctx = _profile_num_ctx()

    # Adaptiver Agent: erst die Frage analysieren, dann einen fragespezifischen
    # Experten-System-Prompt ableiten, der anschließend die Antwort erzeugt.
    _ad_tok = {"in": 0, "out": 0}   # Tokenverbrauch der adaptiven Ableitung (→ Gesamtzähler)
    if request.agent_id == "__adaptive__":
        role, derived = await _derive_adaptive_prompt(_last_user, model, _num_ctx, tok=_ad_tok)
        if derived:
            system_prompt = derived
            yield _sse({"type": "adaptive", "role": role})
    # Agenten-Konfiguration laden (sucht nach ID unabhängig vom Dateinamen)
    elif request.agent_id:
        agent_file = _agent_path_by_id(request.agent_id)
        if agent_file and agent_file.exists():
            agent = json.loads(agent_file.read_text(encoding="utf-8"))
            system_prompt = agent.get("system_prompt") or None
            if agent.get("model"):
                model = agent["model"]
                _agent_fixed_model = True
            # Fest an den Agenten gebundene Wissensdatenbank(en) automatisch aktivieren
            # (z. B. Gesetzes-/Regel-Agent mit hinterlegtem Normtext). Doppelte vermeiden.
            _agent_rag = agent.get("rag_collections") or []
            if _agent_rag:
                request.rag_collections = list(dict.fromkeys(list(request.rag_collections) + _agent_rag))
            allowed = set(agent.get("tools", list(ALL_TOOL_NAMES)))
            active_tools = [t for t in TOOL_DEFS if t["function"]["name"] in allowed]
            # Marker-„Tool" code_ide kennzeichnet den Programmier-Agenten (kein echtes
            # Ollama-Tool, daher nicht in active_tools — nur Fähigkeits-Flag).
            code_capable = "code_ide" in allowed
            # „Echter" Präsentations-Agent: nur für diesen (oder bei klarer Nutzer-
            # Absicht) darf der Canvas-Fallback aus Fließtext eine Präsentation bauen.
            # Verhindert, dass allgemeine Antwort-Modi (z. B. Felix/Sandra), die das
            # create_presentation-Tool nur „dabei" haben, zu schnell ins Canvas springen.
            _presenter_dedicated = (
                request.agent_id == "presenter"
                or agent.get("category") == "Präsentation"
            )

    # Websuche ist standardmäßig AUS und nur über den Schalter (oder Wissenschafts-
    # modus) verfügbar. Alle übrigen Werkzeuge (Plot, Rechner, Einheiten …) bleiben
    # davon unberührt – sonst „malt" das Modell mangels plot_function selbst Linien.
    if _hartman() or not (request.web_search or request.science):
        # Persona »Hartman« sperrt die Websuche komplett (alles rein lokal).
        active_tools = [t for t in active_tools
                        if t["function"]["name"] != "web_search"]

    # Ob plot_function dem Modell als Tool angeboten wird, entscheidet sich weiter unten
    # NACH der endgültigen Modellwahl (nur fähige externe Modelle bekommen es, siehe
    # Kommentar dort). Kleine lokale Modelle erzeugen dabei ungültige LaTeX-Escapes
    # (\( … \)), an denen Ollama mit HTTP 500 scheitert.

    # Rollen-Modell wählen, sofern der Agent keines fest vorgibt:
    #  • Programmier-Agent (code_ide) → Programmier-Modell
    #  • Wissenschaftsmodus → Wissenschafts-Modell (außer der Nutzer wählte gezielt
    #    ein anderes als das Allgemein-/Standardmodell)
    #  • sonst → angefordertes/Allgemein-Modell
    if not _agent_fixed_model:
        _req = _pick_model(request.model)
        if code_capable:
            model = _model_for("coding")
        elif request.science and _req in (DEFAULT_MODEL, _model_for("general")):
            model = _model_for("science")
        else:
            model = _req
        # Mathe-Weiche: Läuft nur das schwache Standardmodell und sieht die Nachricht
        # nach einer Matheaufgabe aus, an das (stärkere) Mathe-Modell (Rolle
        # „Programmieren / Mathe") weiterreichen. Greift nur, wenn dort tatsächlich ein
        # anderes Modell hinterlegt ist – sonst bliebe es ein wirkungsloser Umweg.
        if (model == DEFAULT_MODEL and _math_autoroute_enabled()
                and _looks_like_math(_last_user)):
            _math_model = _model_for("coding")
            if _math_model and _math_model != DEFAULT_MODEL:
                model = _math_model

    # Profil-Schalter „Recherche lokal": Wissenschafts-/Recherchekontext (Matrix-Zellen
    # laufen mit science=true) zwingend auf ein lokales Modell umbiegen, auch wenn die
    # Rolle ein externes API-Modell ist. Ist kein lokales LLM da → Fehlerframe.
    if request.science and _research_local_only() and _llm.is_remote(model) and not _llm.is_local(model):
        _loc = await _local_model(model)
        if not _loc:
            yield _sse({"type": "error", "message": "Kein lokales LLM verfügbar – „Web-Recherche lokal“ ist im Profil aktiv."})
            return
        model = _loc

    # plot_function nur FÄHIGEN externen Modellen (OpenRouter/OpenAI/… — Namensschema
    # „provider::modell") anbieten: sie erzeugen glatte Funktionsgraphen (400 Stützstellen)
    # und haben den LaTeX-Escape-Bug kleiner lokaler Modelle nicht. Für lokale Modelle
    # bleibt es ausgeblendet — dort greift der deterministische Fallback
    # (_extract_plot_request → plot_function nach der Antwort). So werden Funktionen nicht
    # mehr als grober Polygonzug über plot_chart „gemalt".
    _is_remote_model = "::" in (model or "")
    if not _is_remote_model:
        active_tools = [t for t in active_tools
                        if t["function"]["name"] != "plot_function"]

    # Erweiterte Chat-Werkzeuge (Profil-Häkchen): Code-Interpreter (run_python) + autonome
    # Web-Recherche, damit das Modell komplexe Aufgaben rechnend/recherchierend löst.
    # Standard aus — kleine Modelle sind mit dem Werkzeug-Loop oft überfordert.
    _agent_tools_on = _chat_agent_tools()
    if _agent_tools_on:
        _names = {t["function"]["name"] for t in active_tools}
        # Code-Interpreter nur, wenn serverseitige Python-Ausführung erlaubt ist
        if ALLOW_PYTHON_EXEC and "run_python" not in _names:
            active_tools = active_tools + [_RUN_PYTHON_TOOL_DEF]
        # Web-Recherche autonom anbieten (unabhängig vom 🔍-Schalter), sofern erlaubt
        if _web_search_allowed() and "web_search" not in _names:
            _web_def = next((t for t in TOOL_DEFS if t["function"]["name"] == "web_search"), None)
            if _web_def:
                active_tools = active_tools + [_web_def]

    # Assistent-Modus: Bild-Werkzeug freischalten, wenn ein Bildmodell konfiguriert ist,
    # damit das Modell auf Wunsch selbst ein Bild erzeugen kann.
    _assist_on = _assistant_mode()
    if _assist_on and _image_model() and not any(t["function"]["name"] == "generate_image" for t in active_tools):
        active_tools = active_tools + [_GENERATE_IMAGE_TOOL_DEF]

    # Assistent-Modus: Wissensdatenbank-Suche freischalten, wenn mindestens eine
    # nicht-leere RAG-Sammlung existiert (sonst hätte das Werkzeug nichts zu durchsuchen).
    if _assist_on and not any(t["function"]["name"] == "search_knowledge_base" for t in active_tools):
        try:
            _kb = await _db.rag_list_collections()
        except Exception:
            _kb = []
        if any(int(c.get("n_chunks", 0) or 0) > 0 for c in _kb):
            active_tools = active_tools + [_SEARCH_KB_TOOL_DEF]

    # Assistent-Modus: Patentrecherche freischalten, wenn Web-Zugang erlaubt ist
    # (Geheim-/Hartman-Modus sperrt die Websuche → auch dieses Werkzeug entfällt).
    if _assist_on and _web_search_allowed() and not any(t["function"]["name"] == "search_patents" for t in active_tools):
        active_tools = active_tools + [_SEARCH_PATENTS_TOOL_DEF]

    # Assistent-Modus: weitere Tabs als aufrufbare Agenten.
    if _assist_on:
        # SymPy-Solver – immer verfügbar (rein lokal/deterministisch, kein LLM/Netz nötig).
        if not any(t["function"]["name"] == "solve_math" for t in active_tools):
            active_tools = active_tools + [_SOLVE_MATH_TOOL_DEF]
        # Mehrstufiger Arbeitsablauf – braucht ein (lokales oder API-)LLM, immer anbietbar.
        if not any(t["function"]["name"] == "run_workflow" for t in active_tools):
            active_tools = active_tools + [_RUN_WORKFLOW_TOOL_DEF]
        # Tiefe Recherche – nur mit Web-Zugang (Geheim-/Hartman-Modus sperrt sie).
        if _web_search_allowed() and not any(t["function"]["name"] == "deep_research" for t in active_tools):
            active_tools = active_tools + [_DEEP_RESEARCH_TOOL_DEF]
        # To-Do-Bestand befragen – nur wenn überhaupt Projekte/Aufgaben vorhanden sind.
        if not any(t["function"]["name"] == "ask_todo" for t in active_tools):
            try:
                _tp = await _db.todo_projects_all()
            except Exception:
                _tp = []
            if any(p.get("id") not in (None, "", "root") for p in _tp):
                active_tools = active_tools + [_ASK_TODO_TOOL_DEF]

    # Explizite Werkzeug-Wahl (z. B. eine Matrix-Spalte wählt „Websuche" oder „Wissensdatenbank"):
    # active_tools auf die gewünschten Namen einschränken – mit denselben Freigaben/Gates wie oben.
    if request.tools:
        _want = [str(t).strip() for t in request.tools if str(t).strip()]
        if _want:
            _registry = {t["function"]["name"]: t for t in TOOL_DEFS}
            for _gd in (_RUN_PYTHON_TOOL_DEF, _GENERATE_IMAGE_TOOL_DEF,
                        _SEARCH_KB_TOOL_DEF, _SEARCH_PATENTS_TOOL_DEF,
                        _SOLVE_MATH_TOOL_DEF, _RUN_WORKFLOW_TOOL_DEF,
                        _DEEP_RESEARCH_TOOL_DEF, _ASK_TODO_TOOL_DEF):
                _registry[_gd["function"]["name"]] = _gd
            _picked = []
            for _n in _want:
                _def = _registry.get(_n)
                if not _def:
                    continue
                if _n == "web_search" and not _web_search_allowed():
                    continue
                if _n == "run_python" and not ALLOW_PYTHON_EXEC:
                    continue
                if _n == "generate_image" and not _image_model():
                    continue
                _picked.append(_def)
            if _picked:
                active_tools = _picked

    # Nachrichten aufbauen – Modus-Brille (falls aktiv) dem System-Prompt voranstellen
    messages: list = []
    _sci = _SCIENCE_PROMPT if request.science else ""
    # Assistent-Modus: Intent-Router als Systemhinweis – das Modell entscheidet selbst,
    # welche Fähigkeit es nutzt.
    _router_hint = ""
    if _assist_on:
        _caps = []
        if any(t["function"]["name"] == "run_python" for t in active_tools):
            _caps.append("Rechnen/Code/Datenanalyse → run_python")
        if any(t["function"]["name"] == "web_search" for t in active_tools):
            _caps.append("aktuelle Fakten/Recherche → web_search")
        if any(t["function"]["name"] == "generate_image" for t in active_tools):
            _caps.append("Bild/Grafik/Motiv erzeugen → generate_image")
        if any(t["function"]["name"] == "search_knowledge_base" for t in active_tools):
            _caps.append("eigene Dokumente/Unterlagen durchsuchen → search_knowledge_base")
        if any(t["function"]["name"] == "search_patents" for t in active_tools):
            _caps.append("Patente/Schutzrechte recherchieren → search_patents")
        if any(t["function"]["name"] == "create_diagram" for t in active_tools):
            _caps.append("Ablauf/Architektur/Beziehungen → create_diagram (Mermaid)")
        if any(t["function"]["name"] == "create_presentation" for t in active_tools):
            _caps.append("Folien/Präsentation → create_presentation")
        if any(t["function"]["name"] == "create_spreadsheet" for t in active_tools):
            _caps.append("Tabelle/Kalkulation → create_spreadsheet")
        if any(t["function"]["name"] == "route_planner" for t in active_tools):
            _caps.append("Route/Fahrtzeit → route_planner")
        if any(t["function"]["name"] == "solve_math" for t in active_tools):
            _caps.append("Gleichung/Ableitung/Integral symbolisch lösen → solve_math")
        if any(t["function"]["name"] == "deep_research" for t in active_tools):
            _caps.append("gründlicher, mehrstufiger Recherchebericht → deep_research")
        if any(t["function"]["name"] == "run_workflow" for t in active_tools):
            _caps.append("komplexe mehrteilige Aufgabe in Schritten abarbeiten → run_workflow")
        if any(t["function"]["name"] == "ask_todo" for t in active_tools):
            _caps.append("eigene Aufgaben/Termine/Kollegen aus dem To-Do-Bestand → ask_todo")
        _router_hint = ("ASSISTENT-MODUS: Du bist ein universeller Assistent und entscheidest "
                        "SELBST, welches Werkzeug eine Aufgabe am besten löst — rufe es dann "
                        "eigenständig auf, statt nur zu beschreiben. Verfügbare Fähigkeiten: "
                        + "; ".join(_caps) + ". Für einfache Wissens-/Gesprächsfragen antworte "
                        "direkt ohne Werkzeug. Für Aufgaben, die eine Datei-Auswahl oder einen "
                        "geführten Dialog brauchen und die du nicht selbst ausführen kannst, "
                        "verweise den Nutzer auf den passenden Chat-Befehl: gewichtete "
                        "Entscheidung/Variantenvergleich → /paarvergleich, zwei Excel-Tabellen "
                        "vergleichen → /excelvergleich, geführte Präsentation → /praesentation, "
                        "mehrstufige Web-Recherche → /recherche, ein Objekt in einem Bild "
                        "suchen/markieren → /finde. Eine Gesamtübersicht liefert /hilfe. "
                        "Sind ZWEI Excel-/CSV-Dateien angehängt, biete von dir aus den zellenweisen "
                        "Tabellenvergleich an (/excelvergleich). Ist ein BILD angehängt und der Nutzer "
                        "will darauf etwas suchen/markieren/lokalisieren, biete die Objektmarkierung "
                        "an (/finde <Objekt>).")
    _agent_hint = ""
    if _agent_tools_on and ALLOW_PYTHON_EXEC:
        _agent_hint = ("Du hast einen Code-Interpreter: Für rechen-/datenlastige oder komplexe "
                       "Aufgaben schreibe und führe Python über das Werkzeug run_python aus, "
                       "statt selbst zu rechnen; nutze das Ergebnis für deine Antwort.")

    # Ist die Websuche verfügbar (per 🔍-Schalter, Wissenschaftsmodus oder erweiterte
    # Werkzeuge), konkrete/überprüfbare Angaben AKTIV recherchieren statt aus dem
    # Gedächtnis zu raten (Detaildaten sind dort oft falsch).
    _web_hint = ""
    if any(t["function"]["name"] == "web_search" for t in active_tools):
        _web_hint = ("Für KONKRETE, überprüfbare Angaben (technische Daten wie Leistung/PS, "
                     "Baujahre, Maße, Preise, Eigennamen, aktuelle Fakten) nutze web_search und "
                     "stütze dich auf die gefundenen Quellen — verlasse dich NICHT auf dein "
                     "Gedächtnis, das bei solchen Detaildaten häufig falsch liegt.")

    # Mathematische/rechnerische Fragen VORZUGSWEISE per Code lösen (vermeidet Rechenfehler
    # kleiner Modelle): run_python, falls der Code-Interpreter aktiv ist, sonst das
    # immer verfügbare calculate-Werkzeug.
    _math_hint = ""
    if _looks_like_math(_last_user):
        _tnames = {t["function"]["name"] for t in active_tools}
        _mtool = "run_python" if "run_python" in _tnames else ("calculate" if "calculate" in _tnames else "")
        if _mtool:
            _math_hint = (
                f"Diese Frage ist mathematisch/rechnerisch: Löse sie VORZUGSWEISE mit dem "
                f"Code-Werkzeug »{_mtool}« — führe die Rechnung als Code aus und stütze deine "
                f"Antwort auf das berechnete Ergebnis, statt im Kopf zu rechnen. Nenne dem "
                f"Nutzer das Ergebnis klar und knapp erklärt."
            )
    _sys = "\n\n".join(p for p in (_sci, _augment_prefix(_last_user), system_prompt, _router_hint, _agent_hint, _web_hint, _math_hint) if p)
    if _sys:
        messages.append({"role": "system", "content": _sys})

    # RAG: relevante Passagen aus den gewählten Sammlungen vorab einblenden
    if request.rag_collections:
        try:
            from tools.rag import query_collections
            hits = await query_collections(request.rag_collections, _last_user)
        except Exception as e:
            hits = []
            yield _sse({"type": "error", "message": f"RAG-Suche fehlgeschlagen: {e}"})
        if hits:
            ctx = "\n\n".join(
                f"[Quelle {i + 1}: {h['filename']}]\n{h['text']}" for i, h in enumerate(hits)
            )
            # Strengste Vorgabe unter den gewählten Sammlungen anwenden (Regler „kreativ↔korrekt")
            _rank = {"kreativ": 0, "ausgewogen": 1, "korrekt": 2}
            _strict = "ausgewogen"
            for _cid in request.rag_collections:
                _c = await _db.rag_get_collection(_cid)
                if _c and _rank.get(_c.get("strictness", "ausgewogen"), 1) > _rank.get(_strict, 1):
                    _strict = _c["strictness"]
            _rag_instr = {
                "korrekt": (
                    "Beantworte die Frage AUSSCHLIESSLICH anhand der folgenden Auszüge aus den "
                    "Wissensdatenbanken des Nutzers und nenne die Quelle (Dateiname). Steht die "
                    "Antwort nicht in den Auszügen, sage das klar und rate nicht."),
                "ausgewogen": (
                    "Beantworte die Frage vorrangig anhand der folgenden Auszüge aus den "
                    "Wissensdatenbanken des Nutzers und nenne die Quelle (Dateiname); ergänze nur "
                    "bei Bedarf mit gesichertem Wissen."),
                "kreativ": (
                    "Nutze die folgenden Auszüge aus den Wissensdatenbanken des Nutzers als "
                    "Grundlage und ergänze sie bei Bedarf mit eigenem Wissen. Nenne die Quelle "
                    "(Dateiname), wenn du dich darauf stützt."),
            }[_strict]
            messages.append({"role": "system", "content": _rag_instr + "\n\n" + ctx})
            yield _sse({"type": "rag", "sources": [
                {"filename": h["filename"], "collection": h["collection_name"], "score": h["score"],
                 **({"image_url": h["image_url"]} if h.get("image_url") else {})}
                for h in hits
            ]})

    for msg in request.messages:
        content = msg.content
        images: list = []

        if msg.files:
            for fid in msg.files:
                fp = UPLOADS_DIR / fid
                if not fp.exists():
                    continue
                if _is_image(fp):
                    images.append(base64.b64encode(fp.read_bytes()).decode())
                else:
                    extracted = _extract_text(fp)
                    content += f"\n\n[Datei: {fp.name}]\n{extracted}"

        entry: dict = {"role": msg.role, "content": content}
        if images:
            entry["images"] = images
        messages.append(entry)

    # Ist der aktive Agent präsentationsfähig? (für den Canvas-Fallback)
    presentation_capable = any(
        t.get("function", {}).get("name") == "create_presentation" for t in active_tools
    )
    canvas_emitted = False   # über alle Loop-Iterationen: wurde schon ein Canvas gesendet?
    image_emitted = False    # wurde schon ein Funktionsgraph/Diagramm-Bild gesendet?

    # Niedrige Temperatur reduziert Halluzinationen kleiner Modelle deutlich und
    # macht das Tool-Calling zuverlässiger. Für Wissenschaft/Recherche noch strenger.
    _temp = 0.1 if request.science else 0.3
    # _num_ctx wurde bereits oben (vor der adaptiven Ableitung) bestimmt.
    # Denkprozess anfordern? Wird abgeschaltet, falls das Modell 'think' nicht unterstützt.
    _think_on = bool(request.show_thinking)
    _tok_in = _ad_tok["in"]    # summierte Prompt-Tokens über alle Loop-Iterationen
    _tok_out = _ad_tok["out"]  # summierte Antwort-Tokens (inkl. adaptiver Ableitung)
    # Agentic Loop
    for _iter in range(8):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": _temp, "num_ctx": _num_ctx},
            "keep_alive": KEEP_ALIVE,
            "tools": active_tools if request.use_tools else [],
        }
        if _think_on:
            payload["think"] = True

        try:
            async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
                resp = await _llm.chat(client,payload)
                # Modelle ohne Reasoning lehnen 'think' mit 400 ab → ohne erneut versuchen
                if resp.status_code == 400 and _think_on:
                    _think_on = False
                    payload.pop("think", None)
                    resp = await _llm.chat(client,payload)
                resp.raise_for_status()
                result = resp.json()
                _tok_in += int(result.get("prompt_eval_count") or 0)
                _tok_out += int(result.get("eval_count") or 0)
        except Exception as e:
            # Bekannte Ollama-Fragilität: kleine Modelle erzeugen beim Tool-Calling
            # gelegentlich ungültige Escapes (\( … ) → HTTP 500. Wollte der Nutzer einen
            # Funktionsplot, liefern wir ihn deterministisch, statt nur einen Fehler zu zeigen.
            if not image_emitted:
                _pr = _extract_plot_request(_last_user)
                if _pr:
                    try:
                        from tools.engineering import plot_function
                        _pe, _pmn, _pmx = _pr
                        _ppi = json.loads(plot_function(_pe, x_min=_pmn, x_max=_pmx))
                        if _ppi.get("type") == "image":
                            yield _sse({"type": "image", "data": _ppi["data"]})
                            yield _sse({"type": "text", "content": "Hier ist der Graph der Funktion."})
                            yield _sse({"type": "done"})
                            return
                    except Exception:
                        pass
            yield _sse({"type": "error", "message": str(e)})
            return

        msg_obj = result.get("message", {})
        tool_calls = msg_obj.get("tool_calls") or []

        # Auch <call_tool> Inline-Format parsen (manche Modelle nutzen dies)
        content_raw = msg_obj.get("content", "")
        if not tool_calls:
            inline_calls = _extract_inline_tool_calls(content_raw)
            if inline_calls:
                tool_calls = inline_calls
                content_raw = _strip_inline_tool_calls(content_raw)
                msg_obj["content"] = content_raw

        # Denkprozess einsammeln: aus dem nativen 'thinking'-Feld (Reasoning-Modelle)
        # UND aus inline <think>…</think>-Tags. In jedem Fall aus dem sichtbaren Text
        # entfernen, damit er nie in die Antwort leckt.
        _think_parts = []
        _native_think = (msg_obj.get("thinking") or "").strip()
        if _native_think:
            _think_parts.append(_native_think)
        if content_raw and "<think" in content_raw.lower():
            _think_parts += [m.strip() for m in
                             re.findall(r"<think>(.*?)</think>", content_raw, flags=re.DOTALL)]
            content_raw = re.sub(r"<think>.*?</think>", "", content_raw, flags=re.DOTALL)
            # unvollständig (kein schließendes Tag): Rest ab <think> als Denken werten
            _unclosed = re.search(r"<think>(.*)$", content_raw, flags=re.DOTALL)
            if _unclosed:
                _think_parts.append(_unclosed.group(1).strip())
                content_raw = content_raw[:_unclosed.start()]
            content_raw = re.sub(r"</?think>", "", content_raw).strip()
            msg_obj["content"] = content_raw
        _think_text = "\n\n".join(p for p in _think_parts if p).strip()
        if request.show_thinking and _think_text:
            yield _sse({"type": "thinking", "content": _think_text})

        # Diagnose: Roh-Antwort des Modells protokollieren (hilft bei „keine Antwort")
        _write_log({
            "type": "llm_response", "model": model, "iter": _iter,
            "content_len": len(content_raw or ""),
            "tool_calls": [tc.get("function", {}).get("name") for tc in tool_calls],
            "done_reason": result.get("done_reason"),
        })

        if not tool_calls:
            content = content_raw

            # Leere sichtbare Antwort (häufig: Reasoning-Modell steckt alles ins »Denken«,
            # oder kleines Modell liefert nur Tool-Calls). Vor der Fehlermeldung EINEN
            # Rettungsaufruf ohne Werkzeuge/Denken versuchen → aus dem gesammelten Kontext
            # (inkl. Web-Ergebnissen) doch noch eine Antwort formulieren.
            if not (content or "").strip():
                _fa, _fti, _fto = await _force_answer(messages, model, _num_ctx)
                _tok_in += _fti
                _tok_out += _fto
                if _fa.strip():
                    content = _fa   # weiter unten normal streamen/speichern
                else:
                    _hinweis = (
                        f"Das Modell '{model}' hat eine leere Antwort geliefert"
                        + (" (nach Tool-Aufrufen)." if _tools_called else ".")
                        + " Bitte erneut senden oder ein anderes Modell wählen."
                    )
                    yield _sse({"type": "error", "message": _hinweis})
                    _write_log({"type": "empty_response", "model": model,
                                "tools_called": _tools_called})
                    yield _sse({"type": "done"})
                    return

            # Canvas-Daten extrahieren falls vorhanden
            canvas_data = _extract_canvas_json(content)
            if canvas_data:
                yield _sse({"type": "canvas", "data": canvas_data})
                canvas_emitted = True
                content = _strip_canvas_json(content)

            # Text wortweise streamen
            words = content.split(" ")
            for i, word in enumerate(words):
                yield _sse({"type": "text", "content": word + (" " if i < len(words) - 1 else "")})
                await asyncio.sleep(0.004)

            # Fallback: präsentationsfähiger Agent lieferte nur Fließtext (kein Tool-Aufruf)
            # → Text per zweitem Aufruf in Folien umwandeln, damit dennoch eine
            #   Canvas-Präsentation entsteht. NUR wenn der Nutzer eine Präsentation
            #   wollte ODER es der dedizierte Präsentations-Agent ist — damit allgemeine
            #   Antwort-Modi nicht bei jedem „Agenda"/„Gliederung" ins Canvas springen.
            _wants_pres = bool(re.search(
                r"(?i)präsentation|präsentier|foliensatz|folien|\bslides?\b|slide-?deck|"
                r"powerpoint|pptx|vortrag",
                _last_user))
            if (not canvas_emitted and presentation_capable and len(content) > 300
                    and (_wants_pres or _presenter_dedicated)
                    and re.search(r"(?i)folie|slide|präsentation|agenda|gliederung|inhaltsverzeichnis", content)):
                _pf_tok = {"in": 0, "out": 0}
                conv = await _text_to_presentation(content, model, tok=_pf_tok)
                _tok_in += _pf_tok["in"]
                _tok_out += _pf_tok["out"]
                if conv:
                    canvas_data = conv
                    yield _sse({"type": "canvas", "data": canvas_data})
                    canvas_emitted = True

            # Deterministischer Plot-Fallback: hat das Modell trotz Plot-Wunsch nicht
            # selbst geplottet, ziehen wir die Funktion aus dem Nutzertext und zeichnen
            # sie serverseitig (kleine Modelle rufen plot_function oft nicht zuverlässig auf).
            if not image_emitted:
                _plot_req = _extract_plot_request(_last_user)
                if _plot_req:
                    try:
                        from tools.engineering import plot_function
                        _expr, _xmin, _xmax = _plot_req
                        _pres = plot_function(_expr, x_min=_xmin, x_max=_xmax)
                        _pimg = json.loads(_pres)
                        if _pimg.get("type") == "image":
                            yield _sse({"type": "image", "data": _pimg["data"]})
                            image_emitted = True
                    except Exception:
                        pass

            # Programmier-Agent: Code aus der Antwort als Basis in die Code-IDE übernehmen
            if code_capable:
                code_block = _extract_code_block(content)
                if code_block:
                    _cname = re.sub(r"\s+", " ", _last_user).strip()[:40] or "Chat-Programm"
                    yield _sse({"type": "code", "code": code_block, "name": _cname})

            # Konversation in DB speichern (inkl. Canvas-JSON)
            if request.conversation_id:
                messages.append({"role": "assistant", "content": content})
                await _db.save_conversation(
                    request.conversation_id,
                    messages,
                    model=model,
                    agent_id=request.agent_id,
                    canvas_json=json.dumps(canvas_data, ensure_ascii=False) if canvas_data else None,
                )

            _write_log({
                "type": "chat", "model": model,
                "msg_count": len(request.messages),
                "resp_len": len(content),
                "tools_called": _tools_called,
                "ms": int((time.time() - _log_t0) * 1000),
                "tok_in": _tok_in, "tok_out": _tok_out,
            })
            yield _sse({"type": "done", "tokens": {"in": _tok_in, "out": _tok_out}})
            return

        # Tool-Calls ausführen
        messages.append({
            "role": "assistant",
            "content": msg_obj.get("content", ""),
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            fn = tc["function"]["name"]
            args = tc["function"]["arguments"]
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}

            # ── Terminal-Streaming-Agenten (Tab-als-Agent): tiefe Recherche, Arbeitsablauf,
            # To-Do-Bestand befragen. Der zugehörige Kern-Generator (core.py) streamt
            # Fortschritt (tool_progress) UND die Endantwort (text) live in die Chat-Blase;
            # sein Ergebnis IST die Antwort → Turn danach beenden (kein Nach-Paraphrasieren).
            if fn in _STREAM_AGENTS:
                yield _sse({"type": "tool_start", "tool": fn, "args": args})
                _tools_called.append(fn)
                _ag_t0 = time.time()
                _summary = ""
                _ag_err = ""
                try:
                    if fn == "deep_research":
                        _gen = _deep_research_core(
                            str(args.get("topic", "") or ""),
                            depth=int(args.get("depth", 6) or 6),
                            words=int(args.get("words", 900) or 900),
                            focus=str(args.get("focus", "") or ""),
                            model=model if _llm.is_remote(model) else None)
                    elif fn == "run_workflow":
                        _gen = _workflow_core(
                            args.get("steps") or [],
                            goal=str(args.get("goal", "") or ""),
                            model=model,
                            web=bool(args.get("web", False)))
                    else:  # ask_todo
                        _gen = _todo_ask_core(
                            str(args.get("question", "") or ""),
                            model=model if _llm.is_remote(model) else None)
                    async for _fr in _gen:
                        _ft = _fr.get("type")
                        if _ft == "progress":
                            yield _sse({"type": "tool_progress", "tool": fn, "message": _fr.get("message", "")})
                        elif _ft == "notice":
                            yield _sse({"type": "tool_progress", "tool": fn, "message": "ⓘ " + _fr.get("message", "")})
                        elif _ft == "text":
                            yield _sse({"type": "text", "content": _fr.get("content", "")})
                        elif _ft == "image":
                            yield _sse({"type": "image", "data": _fr.get("data", "")})
                            image_emitted = True
                        elif _ft == "error":
                            _ag_err = _fr.get("message", "") or "unbekannter Fehler"
                        elif _ft == "result":
                            _summary = _fr.get("summary", "") or ""
                            _t = _fr.get("tok") or {}
                            _tok_in += int(_t.get("in", 0) or 0)
                            _tok_out += int(_t.get("out", 0) or 0)
                except Exception as _e:
                    _ag_err = str(_e)
                _write_log({"type": "tool", "name": fn, "ms": int((time.time() - _ag_t0) * 1000),
                            "result_len": len(_summary)})
                yield _sse({"type": "tool_done", "tool": fn,
                            "preview": (_summary[:300] or _ag_err[:300])})
                if _ag_err and not _summary.strip():
                    yield _sse({"type": "error", "message": _ag_err})
                    yield _sse({"type": "done", "tokens": {"in": _tok_in, "out": _tok_out}})
                    return
                # Ergebnis als Assistenten-Antwort speichern und Turn beenden.
                if request.conversation_id and _summary.strip():
                    messages.append({"role": "assistant", "content": _summary})
                    await _db.save_conversation(request.conversation_id, messages,
                                                model=model, agent_id=request.agent_id)
                yield _sse({"type": "done", "tokens": {"in": _tok_in, "out": _tok_out}})
                return

            yield _sse({"type": "tool_start", "tool": fn, "args": args})
            _tool_t0 = time.time()
            tool_result = await _execute_tool(fn, args)
            _tool_ms = int((time.time() - _tool_t0) * 1000)
            _tools_called.append(fn)
            _write_log({"type": "tool", "name": fn, "ms": _tool_ms, "result_len": len(tool_result)})
            yield _sse({"type": "tool_done", "tool": fn, "preview": tool_result[:300]})

            # Canvas sofort streamen wenn es ein Präsentations-/Tabellen-Tool ist
            if fn in ("create_presentation", "create_spreadsheet"):
                try:
                    canvas_data = json.loads(tool_result)
                    yield _sse({"type": "canvas", "data": canvas_data})
                    canvas_emitted = True
                    # Canvas in DB speichern (auch wenn das Modell danach noch Text schreibt)
                    if request.conversation_id:
                        await _db.update_canvas(
                            request.conversation_id,
                            json.dumps(canvas_data, ensure_ascii=False),
                        )
                except Exception:
                    pass

            # Diagramm-Bild sofort streamen
            if fn in ("plot_chart", "plot_function"):
                try:
                    img_data = json.loads(tool_result)
                    if img_data.get("type") == "image":
                        yield _sse({"type": "image", "data": img_data["data"]})
                        image_emitted = True
                        tool_result = "Diagramm wurde erstellt und wird angezeigt."
                except Exception:
                    pass

            # Mermaid-Diagramm sofort streamen
            if fn == "create_diagram":
                try:
                    diag = json.loads(tool_result)
                    yield _sse({"type": "diagram", "data": diag})
                    tool_result = (
                        f"Diagramm '{diag.get('title', diag.get('diagram_type', ''))}' "
                        f"wird dem Nutzer bereits angezeigt. "
                        f"Beschreibe es kurz in 1–2 Sätzen."
                    )
                except Exception:
                    pass

            # Route sofort als interaktive Karte streamen
            if fn == "route_planner":
                try:
                    map_data = json.loads(tool_result)
                    if map_data.get("type") == "map":
                        yield _sse({"type": "map", "data": map_data})
                        # Modell erhält nur die Kennzahlen, nicht die ganze Geometrie
                        tool_result = (
                            f"Route von {map_data['start']['name']} nach "
                            f"{map_data['end']['name']} wird dem Nutzer bereits als "
                            f"interaktive Karte angezeigt. "
                            f"Strecke: {map_data['distance_km']} km, "
                            f"Fahrzeit: {map_data['duration_text']} "
                            f"(Profil: {map_data['profile']}). "
                            f"Fasse dem Nutzer NUR diese Eckdaten knapp zusammen und "
                            f"verweise auf die Karte. Erfinde KEINE Wegbeschreibung, "
                            f"keine Straßennamen, Ausfahrten, Brücken, Normen, "
                            f"Tempolimits oder technischen Analysen – diese Angaben "
                            f"liegen dir nicht vor."
                        )
                except Exception:
                    pass

            # Code-Interpreter: erzeugte Diagramme sofort anzeigen, dem Modell nur den Text geben
            if fn == "run_python":
                try:
                    _pyd = json.loads(tool_result)
                    for _img in (_pyd.get("images") or []):
                        yield _sse({"type": "image", "data": _img})
                        image_emitted = True
                    tool_result = _pyd.get("text", "") or "(keine Ausgabe)"
                    if _pyd.get("images"):
                        tool_result += "\n(Das/die Diagramm(e) werden dem Nutzer bereits angezeigt.)"
                except Exception:
                    pass

            # Wissensdatenbank-Suche: Quellen (inkl. Bild-Thumbnails) anzeigen, Modell nur Text geben
            if fn == "search_knowledge_base":
                try:
                    _kbd = json.loads(tool_result)
                    if isinstance(_kbd, dict) and "text" in _kbd:
                        _srcs = _kbd.get("sources") or []
                        if _srcs:
                            yield _sse({"type": "rag", "sources": _srcs})
                        tool_result = _kbd.get("text", "") or tool_result
                except Exception:
                    pass

            # Assistent-Modus: erzeugtes Bild sofort anzeigen, dem Modell nur eine Notiz geben
            if fn == "generate_image":
                try:
                    _imd = json.loads(tool_result)
                    if _imd.get("ok") and _imd.get("image"):
                        yield _sse({"type": "image", "data": _imd["image"]})
                        image_emitted = True
                        tool_result = ("Das Bild wurde erzeugt und wird dem Nutzer bereits "
                                       "angezeigt. Beschreibe es kurz in 1–2 Sätzen.")
                    else:
                        tool_result = "Bildgenerierung fehlgeschlagen: " + str(_imd.get("error", "unbekannt"))
                except Exception:
                    pass

            messages.append({"role": "tool", "content": tool_result})

    # Werkzeug-Loop erschöpft (z. B. tiefe Web-Recherche mit vielen Suchschritten): statt
    # aufzugeben eine finale Antwort ohne Werkzeuge aus dem gesammelten Kontext erzwingen.
    _fa, _fti, _fto = await _force_answer(messages, model, _num_ctx)
    _tok_in += _fti
    _tok_out += _fto
    if _fa.strip():
        _words = _fa.split(" ")
        for _i, _w in enumerate(_words):
            yield _sse({"type": "text", "content": _w + (" " if _i < len(_words) - 1 else "")})
            await asyncio.sleep(0.004)
        if request.conversation_id:
            messages.append({"role": "assistant", "content": _fa})
            await _db.save_conversation(request.conversation_id, messages,
                                        model=model, agent_id=request.agent_id)
        yield _sse({"type": "done", "tokens": {"in": _tok_in, "out": _tok_out}})
        return

    yield _sse({"type": "error", "message":
                "Die Recherche brauchte zu viele Schritte und lieferte keine finale Antwort. "
                "Bitte die Frage etwas eingrenzen oder ein stärkeres Modell wählen."})
    yield _sse({"type": "done", "tokens": {"in": _tok_in, "out": _tok_out}})




# ── Tool-Ausführung ───────────────────────────────────────────────────────────


async def _execute_tool(name: str, args: dict) -> str:
    if name == "web_search":
        from tools.search import search
        return await search(args.get("query", ""), int(args.get("num_results", 6)))

    if name == "calculate":
        return _safe_exec(args.get("code", ""))

    if name == "solve_math":
        # Mathe-Tab als Werkzeug: deterministische, SymPy-verifizierte Grundwahrheit.
        from tools.engineering import sympy_facts
        _expr = str(args.get("expression", "") or "").strip()
        _goal = str(args.get("goal", "") or "").strip().lower()
        _kind = "equation" if ("=" in _expr and "==" not in _expr) else "expression"
        _facts = sympy_facts(_kind, _expr, _goal)
        if not _facts:
            return ("Konnte den Ausdruck nicht deterministisch auswerten. Prüfe die Schreibweise "
                    "(Potenz mit **, Gleichungen mit =, keine Worte) oder löse es per calculate/run_python.")
        return ("Verifiziertes SymPy-Ergebnis (deterministisch berechnet – übernimm es als korrekt "
                "und erkläre es dem Nutzer verständlich):\n" + _facts)

    if name == "run_python":
        # Code-Interpreter (Chat, per Profil-Häkchen): dieselbe Sandbox wie der Code-Tab.
        if not ALLOW_PYTHON_EXEC:
            return "Python-Ausführung ist in dieser Installation deaktiviert."
        out = await asyncio.to_thread(_run_python_code, str(args.get("code", "") or ""), 15.0)
        txt = ""
        if out.get("output"):
            txt += "STDOUT:\n" + out["output"]
        if out.get("error"):
            txt += "\nSTDERR:\n" + out["error"]
        # JSON-Umschlag: der Loop trennt Bilder (→ anzeigen) vom Text (→ ans Modell)
        return json.dumps({"text": (txt.strip() or "(keine Ausgabe)")[:6000],
                           "images": out.get("images") or []}, ensure_ascii=False)

    if name == "generate_image":
        # Bild-Werkzeug (Assistent-Modus): erzeugt ein Bild; der Loop streamt es als image-Frame.
        try:
            r = await _generate_image_core(str(args.get("prompt", "") or ""), "",
                                           str(args.get("size", "square") or "square"))
            return json.dumps({"ok": True, "image": r.get("image", ""),
                               "prompt": r.get("prompt", "")}, ensure_ascii=False)
        except HTTPException as e:
            return json.dumps({"ok": False, "error": str(e.detail)}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    if name == "search_knowledge_base":
        # Wissensdatenbank-Suche (Assistent-Modus): alle nicht-leeren RAG-Sammlungen.
        from tools.rag import query_collections as _qc
        q = str(args.get("query", "") or "").strip()
        if not q:
            return "Keine Suchanfrage angegeben."
        try:
            colls = await _db.rag_list_collections()
        except Exception:
            colls = []
        ids = [c["id"] for c in colls if int(c.get("n_chunks", 0) or 0) > 0]
        if not ids:
            return "Die Wissensdatenbank ist leer — es sind keine durchsuchbaren Sammlungen vorhanden."
        try:
            hits = await _qc(ids, q, top_k_cap=8)
        except Exception as e:
            return ("Wissensdatenbank-Suche fehlgeschlagen (Einbettung braucht lokales Ollama): "
                    + str(e)[:200])
        if not hits:
            return f"Keine passenden Stellen zu '{q}' in der Wissensdatenbank gefunden."
        parts = []
        sources = []
        for i, h in enumerate(hits, 1):
            src = h.get("filename", "") or h.get("collection_name", "")
            coll = h.get("collection_name", "")
            label = f"[{i}] {src}" + (f" · {coll}" if coll and coll != src else "")
            parts.append(f"{label}\n{h.get('text','')}")
            s = {"filename": h.get("filename", ""), "collection": coll, "score": h.get("score")}
            if h.get("image_url"):
                s["image_url"] = h["image_url"]   # Bild-aware RAG: Thumbnail im Chat
            sources.append(s)
        _kb_text = ("Treffer aus der Wissensdatenbank (stütze deine Antwort darauf und nenne die "
                    "Quelle in eckigen Klammern):\n\n" + "\n\n".join(parts))[:8000]
        # JSON-Umschlag: der Loop zeigt die Quellen (inkl. Bild-Thumbnails) an und gibt dem Modell nur den Text.
        return json.dumps({"text": _kb_text, "sources": sources}, ensure_ascii=False)

    if name == "search_patents":
        # Patentrecherche (Assistent-Modus): EPO-OPS falls konfiguriert, sonst Google-Fallback.
        from tools import patente as _patente
        term = str(args.get("query", "") or "").strip()
        if not term:
            return "Kein Suchbegriff angegeben."
        try:
            n = int(args.get("max_results", 8) or 8)
        except Exception:
            n = 8
        n = max(1, min(n, 20))
        # OPS-Zugangsdaten (falls vorhanden) inline lesen — ohne Router-Kopplung.
        _ops = None
        try:
            if EPO_OPS_FILE.exists():
                _d = json.loads(EPO_OPS_FILE.read_text(encoding="utf-8"))
                if _d.get("consumer_key") and _d.get("consumer_secret"):
                    _ops = _d
        except Exception:
            _ops = None
        try:
            async with httpx.AsyncClient() as client:
                results, fehler, quelle = await _patente.search_patents(
                    client, term, str(args.get("assignee", "") or ""), "",
                    n, ipc=str(args.get("ipc", "") or ""),
                    ops_creds=_ops, cache_dir=PAT_CACHE_DIR)
        except Exception as e:
            return "Patentrecherche fehlgeschlagen: " + str(e)[:200]
        if not results:
            return (f"Keine Patente zu '{term}' gefunden."
                    + (f" ({fehler})" if fehler else ""))
        lines = []
        for r in results:
            if r.get("error"):
                continue
            pid = r.get("patent_id", "")
            title = r.get("title", "") or "(ohne Titel)"
            date = r.get("publication_date", "") or r.get("date", "")
            assignee = r.get("assignee", "") or (", ".join(r.get("inventors", []) or [])[:80])
            abstract = (r.get("abstract", "") or "").strip().replace("\n", " ")
            if len(abstract) > 300:
                abstract = abstract[:300].rstrip() + " …"
            head = f"[{pid}] {title}"
            meta = " · ".join(x for x in (assignee, date) if x)
            lines.append(head + (f"\n{meta}" if meta else "") + (f"\n{abstract}" if abstract else ""))
        body = "\n\n".join(lines) or "(keine auswertbaren Treffer)"
        src = "EPO OPS (amtlich)" if quelle == "epo_ops" else "Google Patents (Fallback)"
        return (f"Patent-Treffer zu '{term}' (Quelle: {src}). Fasse die relevantesten für den "
                f"Nutzer zusammen und nenne jeweils die Patentnummer:\n\n" + body)[:8000]

    if name in ("create_presentation", "create_spreadsheet"):
        canvas_type = name.replace("create_", "")
        data = {"type": canvas_type, **args}
        if canvas_type == "presentation":
            data = _normalize_presentation(data)
        return json.dumps(data, ensure_ascii=False)

    if name == "unit_convert":
        from tools.engineering import unit_convert
        return unit_convert(
            float(args.get("value", 0)),
            str(args.get("from_unit", "")),
            str(args.get("to_unit", "")),
        )

    if name == "solve_equation":
        from tools.engineering import solve_equation
        return solve_equation(
            str(args.get("expression", "")),
            str(args.get("variable", "x")),
        )

    if name == "plot_chart":
        from tools.engineering import plot_chart
        return plot_chart(
            x_data=args.get("x_data", []),
            y_data=args.get("y_data", []),
            title=args.get("title", ""),
            x_label=args.get("x_label", ""),
            y_label=args.get("y_label", ""),
            chart_type=args.get("chart_type", "line"),
            series_label=args.get("series_label", ""),
            y2_data=args.get("y2_data"),
            y2_label=args.get("y2_label", ""),
        )

    if name == "plot_function":
        from tools.engineering import plot_function
        return plot_function(
            expression=str(args.get("expression", "")),
            var=str(args.get("var", "x") or "x"),
            x_min=float(args.get("x_min", -10)),
            x_max=float(args.get("x_max", 10)),
            title=str(args.get("title", "")),
            x_label=str(args.get("x_label", "")),
            y_label=str(args.get("y_label", "")),
        )

    if name == "material_lookup":
        from tools.materials import material_lookup
        return material_lookup(
            str(args.get("name", "")),
            str(args.get("prop", "")),
        )

    if name == "bolt_calculator":
        from tools.engineering import bolt_calculator
        return bolt_calculator(
            d_nom=float(args.get("d_nom", 0)),
            pitch=float(args.get("pitch", 0)),
            f_axial=float(args.get("f_axial", 0)),
            mu=float(args.get("mu", 0.15)),
            material_class=str(args.get("material_class", "8.8")),
            f_transverse=float(args.get("f_transverse", 0)),
        )

    if name == "generate_report":
        from tools.report import generate_report
        return generate_report(
            title=str(args.get("title", "Bericht")),
            author=str(args.get("author", "")),
            sections=args.get("sections", []),
        )

    if name == "route_planner":
        from tools.routing import plan_route
        return await plan_route(
            origin=str(args.get("origin", "")),
            destination=str(args.get("destination", "")),
            profile=str(args.get("profile", "driving")),
        )

    if name == "create_diagram":
        return json.dumps({
            "type": "diagram",
            "diagram_type": str(args.get("diagram_type", "flowchart")),
            "definition": str(args.get("definition", "")),
            "title": str(args.get("title", "")),
        }, ensure_ascii=False)

    return f"Unbekanntes Tool: {name}"








# ── Handoff-Aufbereitung („senden an …") ──────────────────────────────────────
# Beim Weiterreichen einer Chat-Antwort an einen anderen Tab muss der Inhalt für das
# Ziel passend AUFBEREITET werden (z. B. Patente → Recherche-Suchbegriff, Mathe →
# reine Aufgabe). Das Frontend fragt zusätzlich „was soll dort passieren?" (action).
# Lokal-bevorzugt (Geheim/Hartman → lokal); deterministischer Rückfall = Originaltext.

_HANDOFF_PREP_SYSTEM = {
    "patente": ("Formuliere aus dem Text einen prägnanten PATENT-RECHERCHE-SUCHBEGRIFF: "
                "die zentrale technische Erfindung als 2–6 Schlagworte (bei Bedarf mit AND/OR), "
                "keine Sätze, kein Fließtext."),
    "mathe": ("Extrahiere NUR die eigentliche mathematische Aufgabe/Gleichung aus dem Text "
              "(ohne Erklärungen), so dass sie direkt gelöst werden kann."),
    "medizin": ("Fasse die medizinische Frage-/Fallbeschreibung knapp und sachlich zusammen, "
                "so dass sie als Eingabe für eine medizinische Auswertung dient."),
    "varianten": ("Formuliere aus dem Text die zu treffende ENTSCHEIDUNG als eine klare "
                  "Fragestellung (eine Zeile), die sich mit Kriterien und Varianten bewerten lässt."),
    "morph": ("Formuliere aus dem Text die GESTALTUNGS-/KONSTRUKTIONSAUFGABE als eine klare "
              "Aufgabenstellung (1–2 Sätze) für einen morphologischen Kasten."),
    "rfq": ("Bereite den Text als ANFRAGE/RFQ auf: worum geht es, was wird benötigt (knapp, sachlich)."),
    "rechnung": ("Extrahiere die abrechenbaren POSITIONEN (Leistung + ggf. Menge/Preis) als kurze "
                 "Aufzählung, so dass daraus eine Rechnung/ein Angebot erstellt werden kann."),
    "zeugnis": ("Fasse die Angaben für ein ARBEITSZEUGNIS zusammen (Rolle, Aufgaben, Leistungen, "
                "Zeitraum) — sachlich, als Stichpunkte."),
}


class HandoffPrepareRequest(BaseModel):
    target: str
    content: str
    action: str = ""
    model: Optional[str] = None


@router.post("/api/handoff/prepare")
async def handoff_prepare(req: HandoffPrepareRequest):
    content = (req.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Kein Inhalt zum Übernehmen.")
    base = _HANDOFF_PREP_SYSTEM.get(req.target)
    # Kein spezielles Aufbereitungsprofil → Text unverändert durchreichen.
    if not base:
        return {"prepared": content, "tokens": {"in": 0, "out": 0}}
    sys = base + (f"\nZusätzlicher Wunsch des Nutzers: {req.action.strip()}" if (req.action or "").strip() else "") + \
        "\nAntworte NUR mit dem aufbereiteten Text, ohne Vor-/Nachrede."
    # Lokal-bevorzugt; ohne lokales LLM (und ohne erlaubtes Remote) → Originaltext.
    model = await _local_model(_pick_model(req.model, _model_for("general")))
    if not model:
        model = _pick_model(req.model, _model_for("general"))
    tok = {"in": 0, "out": 0}
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": sys},
                             {"role": "user", "content": content[:6000]}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        j = resp.json()
        a, b = _llm_tok(j); tok["in"] += a; tok["out"] += b
        prepared = re.sub(r"<think>.*?</think>", "", j.get("message", {}).get("content", ""),
                          flags=re.DOTALL).strip()
    except Exception:
        prepared = ""
    return {"prepared": prepared or content, "tokens": tok}
