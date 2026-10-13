/**
 * framework-tools — macht die Werkzeuge von AI_Framework_Thomas zu echten
 * Pi-Werkzeugen.
 *
 * WARUM DAS NÖTIG IST (gemessen, nicht vermutet)
 * ----------------------------------------------
 * Der erste Ansatz war „skill = CLI + README": Pi bekommt eine Beschreibung und
 * ruft die Framework-Kommandozeile über sein bash-Werkzeug auf. Mit einem
 * lokalen 4B-Modell hat das NICHT getragen — das Modell rief sein eigenes
 * web_search auf, bekam einen Fehler und **erfand** dann die Zahlen
 * („~204–253 Nm" statt der korrekten 87,4 Nm aus VDI 2230). Selbstbewusst
 * formatiert und völlig falsch.
 *
 * Als echte Werkzeuge mit Namen, Beschreibung und Parameter-Schema trifft das
 * Modell die Auswahl dagegen zuverlässig — dieselbe Lektion wie beim
 * `kind`-Enum von `mechanik_calc`: eine Auswahl aus einer Liste ist die Stärke
 * kleiner Modelle, freies Formulieren ihre Schwäche.
 *
 * WIE
 * ---
 * Beim Sitzungsstart wird `GET /api/tools/list` des laufenden Frameworks
 * gelesen und JEDES Werkzeug mit seinem echten JSON-Schema registriert
 * (Präfix `fw_`). Ausgeführt wird über `POST /api/tools/run`. Das Framework
 * bleibt damit die einzige Quelle der Wahrheit: neue Chat-Werkzeuge stehen Pi
 * automatisch zur Verfügung, ohne diese Datei anzufassen.
 *
 * Dazu kommt `fw_cli` für die lesenden Verben (Projekte, Pläne, Aufgaben,
 * Wissensdatenbanken, mehrstufige Recherche) — die laufen über die CLI, nicht
 * über die Werkzeug-Registry.
 *
 * Keine Abhängigkeiten: nur `fetch` und Node-Standard. Kein npm install, kein
 * Build. Wird mit `--extension` geladen.
 *
 * Umgebung (setzt der Pi-Tab des Frameworks):
 *   AIF_BASE_URL   z. B. http://127.0.0.1:8780
 *   AIF_CLI        absoluter Pfad zu pi-agent/framework_cli.py
 *   AIF_PYTHON     Python der Framework-venv
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";

const BASE = (process.env.AIF_BASE_URL || "http://127.0.0.1:8780").replace(/\/+$/, "");
const CLI = process.env.AIF_CLI || "";
const PY = process.env.AIF_PYTHON || "python";

/** Text aus einem Werkzeugergebnis, auf eine vernünftige Länge begrenzt. */
function kurz(s: string, n = 12000): string {
	if (!s) return "";
	return s.length <= n ? s : s.slice(0, n) + "\n… (gekürzt)";
}

async function frameworkFetch(pfad: string, init?: RequestInit): Promise<any> {
	const resp = await fetch(BASE + pfad, init);
	const roh = await resp.text();
	let daten: any = roh;
	try {
		daten = JSON.parse(roh);
	} catch {
		/* Text belassen */
	}
	if (!resp.ok) {
		const detail = (daten && daten.detail) || roh || `HTTP ${resp.status}`;
		throw new Error(`HTTP ${resp.status}: ${detail}`);
	}
	return daten;
}

/** Die CLI aufrufen (für die lesenden Verben). */
function cliAufruf(args: string[], timeoutMs = 180000): Promise<string> {
	return new Promise((resolve) => {
		if (!CLI) {
			resolve("Fehler: AIF_CLI ist nicht gesetzt — die Kommandozeile ist nicht erreichbar.");
			return;
		}
		const pr = spawn(PY, [CLI, ...args], {
			stdio: ["ignore", "pipe", "pipe"],
			env: { ...process.env, PYTHONIOENCODING: "utf-8" },
		});
		let out = "";
		let err = "";
		const t = setTimeout(() => {
			try {
				pr.kill();
			} catch {
				/* egal */
			}
			resolve(out + "\n[Zeitlimit erreicht]");
		}, timeoutMs);
		pr.stdout.on("data", (d) => (out += d.toString("utf8")));
		pr.stderr.on("data", (d) => (err += d.toString("utf8")));
		pr.on("close", (code) => {
			clearTimeout(t);
			if (code !== 0 && !out.trim()) resolve(err.trim() || `Exit-Code ${code}`);
			else resolve(out + (err.trim() ? `\n[Hinweis: ${err.trim()}]` : ""));
		});
		pr.on("error", (e) => {
			clearTimeout(t);
			resolve(`Start fehlgeschlagen: ${String(e)}`);
		});
	});
}

/** Leeres Objekt-Schema, falls ein Werkzeug keine Parameter meldet. */
const LEER = { type: "object", properties: {}, additionalProperties: true };

export default function frameworkTools(pi: ExtensionAPI) {
	let registriert = false;

	async function registriereAlles(ctx: any) {
		if (registriert) return;
		registriert = true;

		// ── 1. Die lesenden Verben über die CLI ─────────────────────────────
		pi.registerTool({
			name: "fw_cli",
			label: "Framework-Kommandozeile",
			description:
				"Liest und steuert AI_Framework_Thomas über seine Kommandozeile. " +
				"Verben: status, modelle, agenten, projekte, plaene [id], " +
				"rag-sammlungen, rag-dokumente <id>, todo-baum, todo-suche <begriff>, " +
				"todo-agenda, todo-frage <frage>, gespraeche [id], gespraech-suche <begriff>, " +
				"patente-projekte, varianten, recherche <thema> (mehrstufige Web-Recherche), " +
				"rechne <kind> <werte> (Maschinenbau), werkstoff <name>, einheit <wert> <von> <nach>. " +
				"Mit Zugriffsstufe write zusätzlich: projekt-neu, todo-neu, todo-setzen, " +
				"rag-neu, rag-notiz. Rufe 'status' auf, wenn etwas unklar ist, und '--hilfe' " +
				"für die vollständige Liste.",
			promptSnippet:
				"Bestand des Nutzers lesen (Projekte, Pläne, Aufgaben, Wissensdatenbanken) " +
				"und mehrstufige Recherchen starten",
			promptGuidelines: [
				"Benutze fw_cli, um den tatsächlichen Bestand des Nutzers zu lesen, statt zu raten.",
				"Für technische Berechnungen sind die fw_*-Werkzeuge direkter als fw_cli rechne.",
			],
			parameters: {
				type: "object",
				properties: {
					verb: { type: "string", description: "Das Verb, z. B. 'projekte' oder 'todo-agenda'" },
					args: {
						type: "array",
						items: { type: "string" },
						description: "Weitere Argumente des Verbs (optional)",
					},
				},
				required: ["verb"],
			},
			async execute(_id: string, params: any) {
				const verb = String(params?.verb || "").trim();
				const args = Array.isArray(params?.args) ? params.args.map(String) : [];
				if (!verb) {
					return {
						content: [{ type: "text", text: "Es fehlt das Verb. '--hilfe' listet alle." }],
						isError: true,
					};
				}
				const text = await cliAufruf([verb, ...args]);
				return { content: [{ type: "text", text: kurz(text) }], details: { verb, args } };
			},
		});

		// ── 2. Die Werkzeug-Registry des Frameworks ─────────────────────────
		let tools: any[] = [];
		try {
			const d = await frameworkFetch("/api/tools/list");
			tools = Array.isArray(d?.tools) ? d.tools : [];
		} catch (e) {
			ctx?.ui?.notify?.(
				`Framework nicht erreichbar (${BASE}) — nur fw_cli registriert: ${String(e)}`,
				"warn",
			);
			return;
		}

		for (const t of tools) {
			const name = String(t?.name || "").trim();
			if (!name) continue;
			const beschreibung = String(t?.description || "").trim();
			const schema =
				t?.parameters && typeof t.parameters === "object" ? t.parameters : LEER;

			pi.registerTool({
				name: `fw_${name}`,
				label: `Framework · ${name}`,
				description: beschreibung,
				promptSnippet: beschreibung.slice(0, 160),
				promptGuidelines: [
					"Dieses Werkzeug rechnet deterministisch im Framework. " +
						"Rechne solche Größen NIE selbst und erfinde keine Zahlen.",
				],
				parameters: schema,
				async execute(_id: string, params: any) {
					try {
						const antwort = await frameworkFetch("/api/tools/run", {
							method: "POST",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify({ name, arguments: params || {} }),
						});
						const text =
							typeof antwort?.result === "string"
								? antwort.result
								: JSON.stringify(antwort, null, 2);
						const dateien: string[] = antwort?.dateien || [];
						const anhang = dateien.length
							? `\n\n[Dateien: ${dateien.map((f) => `${BASE}/api/tools/file/${f}`).join(", ")}]`
							: "";
						return {
							content: [{ type: "text", text: kurz(text) + anhang }],
							details: { tool: name, dateien },
						};
					} catch (e) {
						// Fehlertext zurückgeben statt zu werfen: das Modell soll die
						// Meldung lesen und den Aufruf korrigieren koennen.
						return {
							content: [{ type: "text", text: `Werkzeug '${name}' fehlgeschlagen: ${String(e)}` }],
							isError: true,
						};
					}
				},
			});
		}

		ctx?.ui?.notify?.(
			`${tools.length} Framework-Werkzeuge registriert (fw_*) + fw_cli`,
			"info",
		);
	}

	pi.on("session_start", (_event: any, ctx: any) => {
		void registriereAlles(ctx);
	});
}
