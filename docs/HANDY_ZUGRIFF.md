# LOCAL AI aufs Handy holen — Schritt für Schritt (für Einsteiger)

Diese Anleitung erklärt **ohne Fachkenntnisse**, wie du die Anwendung **LOCAL AI**
vom **Handy** aus benutzt. Die eigentliche Software (das „Backend") läuft weiter auf
deinem **Ubuntu-Rechner**; das Handy zeigt nur die Bedienoberfläche an. Dadurch nutzt
du am Telefon die volle Rechenleistung des PCs.

Es gibt drei Stufen — von „nur ausprobieren" bis „läuft dauerhaft wie eine App":

1. **Schnell testen** – im Browser des Handys öffnen (gleiches WLAN).
2. **Als App installieren** – mit „Zum Startbildschirm hinzufügen" (braucht HTTPS).
3. **Dauerbetrieb** – der Rechner startet die Software automatisch beim Hochfahren.

---

## Voraussetzung: Handy und Rechner im selben Netz

Das Handy muss im **selben WLAN** hängen wie der Ubuntu-Rechner (beide an deiner
**FritzBox**). Dann „sehen" sich beide Geräte direkt — es ist **keine** Freigabe im
Internet nötig.

**Die Adresse deines Rechners herausfinden** (am Rechner im Terminal):

```bash
hostname -I
```

Die erste Zahl, die mit `192.168.` beginnt, ist die Adresse — z. B.
`192.168.178.49`. Alternativ vergibt die FritzBox auch einen Namen wie
`grosserrechner.fritz.box`.

---

## Stufe 1 — Schnell im Handy-Browser testen

1. Am Rechner die Software so starten, dass sie **im Netz erreichbar** ist:

   ```bash
   AI_HOST=0.0.0.0 ./start.sh
   ```

2. Am Handy den Browser öffnen und die Adresse eingeben:

   ```
   http://192.168.178.49:8780
   ```

   (deine Zahl aus `hostname -I` einsetzen). Fertig — die Oberfläche erscheint.

> **Klappt nicht?** Meist blockiert die Firewall des Rechners den Zugang. Einmalig
> freigeben:
> ```bash
> sudo ufw allow 8780/tcp
> ```

---

## Stufe 2 — Als App installieren (empfohlen)

Damit sich LOCAL AI wie eine echte App „**Zum Startbildschirm hinzufügen**" lässt,
verlangen Handys eine **gesicherte Verbindung (HTTPS)**. Dafür erstellst du **einmalig**
ein eigenes Sicherheitszertifikat.

### a) Zertifikat erstellen (einmalig, am Rechner)

```bash
./scripts/gen_cert.sh
```

Das Skript merkt sich automatisch die Adresse(n) deines Rechners und legt zwei Dateien
im Ordner `certs/` an. Am Ende zeigt es dir die fertigen Handy-Adressen an.

### b) Software mit HTTPS starten

```bash
AI_HOST=0.0.0.0 AI_SSL_CERT=certs/cert.pem AI_SSL_KEY=certs/key.pem ./start.sh
```

### c) Am Handy öffnen und installieren

1. Browser öffnen, Adresse mit **`https://`** eingeben:

   ```
   https://192.168.178.49:8780
   ```

2. Es erscheint eine **Sicherheitswarnung** (weil das Zertifikat selbst erstellt ist —
   das ist normal und ungefährlich im eigenen Heimnetz). Bestätigen:
   *Android-Chrome:* **„Erweitert"** → **„Weiter zu … (unsicher)"**.

3. Im Browser-Menü (drei Punkte) **„Zum Startbildschirm hinzufügen"** wählen. Jetzt
   liegt ein **LOCAL-AI-Symbol** auf dem Handy wie eine normale App.

> **Tipp – Wischen am Handy:** Im Tab **🧩 Morph-Kasten** gibt es **🃏 Ideen wischen**:
> Die KI denkt sich Ideen aus, du wischst **nach links = gut**, **nach rechts = schlecht**
> (mit kurzer Begründung). Das ist genau für die Touch-Bedienung am Handy gemacht.

---

## Stufe 3 — Automatisch starten (Dauerbetrieb)

Damit LOCAL AI **immer läuft** — auch nach einem Neustart des Rechners, ohne dass du
das Terminal öffnen musst — richtest du einen **Dienst** ein. Das geht in einem Schritt:

```bash
./scripts/install_service.sh
```

Das Skript fragt einmal nach deinem **Passwort** (für die Systemeinrichtung) und sorgt
dann dafür, dass die Software beim Hochfahren automatisch mit HTTPS startet (sofern ein
Zertifikat aus Stufe 2 vorhanden ist).

**Nützliche Befehle danach:**

| Zweck | Befehl |
|---|---|
| Läuft alles? | `systemctl status ai-framework` |
| Live-Protokoll ansehen | `journalctl -u ai-framework -f` |
| Vorübergehend stoppen | `sudo systemctl stop ai-framework` |
| Wieder starten | `sudo systemctl start ai-framework` |
| Dienst entfernen | `./scripts/uninstall_service.sh` |

Der Rechner muss eingeschaltet sein und **Ollama** (die KI-Modelle) muss laufen — das
startet auf diesem System ebenfalls automatisch als Dienst.

---

## Von unterwegs zugreifen (außerhalb des Heim-WLANs)

Bist du **nicht zu Hause** (z. B. mit mobilen Daten), kommst du nicht direkt an den
Rechner. Es gibt zwei Wege über die FritzBox:

### Empfohlen: VPN der FritzBox (WireGuard)

Ein **VPN** verbindet dein Handy sicher mit deinem Heimnetz — danach ist alles so, als
wärst du zu Hause im WLAN.

1. In der FritzBox-Oberfläche (`http://fritz.box`): **Internet → Freigaben → VPN
   (WireGuard) → Verbindung hinzufügen**.
2. Den angezeigten **QR-Code** mit der App **„WireGuard"** auf dem Handy scannen.
3. VPN am Handy einschalten — dann LOCAL AI wieder über die normale Adresse öffnen,
   z. B. `https://192.168.178.49:8780`.

Das ist die **sichere** Variante: Niemand sonst kommt an deinen Rechner.

### Nicht empfohlen: Portfreigabe ins Internet

Man könnte den Zugang per **Portfreigabe** direkt ins Internet stellen. **Davon raten
wir ab:** LOCAL AI hat **kein Passwort/Login** — damit stünde deine private KI offen im
Netz. Nutze stattdessen das VPN oben.

---

## Häufige Fragen

**Die Adresse funktioniert nicht.**
- Läuft die Software mit `AI_HOST=0.0.0.0` (nicht nur `127.0.0.1`)?
- Firewall freigeben: `sudo ufw allow 8780/tcp`
- Sind Handy und Rechner im **selben** WLAN?

**Warum die Sicherheitswarnung im Browser?**
Weil das Zertifikat von dir selbst erstellt wurde und nicht von einer offiziellen
Stelle. Im eigenen Heimnetz ist das unbedenklich — einfach bestätigen.

**„Zum Startbildschirm hinzufügen" fehlt / die Installation klappt nicht.**
Das gibt es nur mit **`https://`** (Stufe 2). Über `http://` läuft die Oberfläche zwar,
ist aber nicht installierbar.

**Muss der Rechner anbleiben?**
Ja. Das Handy ist nur die Anzeige; gerechnet wird auf dem Ubuntu-Rechner. Er muss
eingeschaltet sein und der Dienst (Stufe 3) bzw. `./start.sh` laufen.
