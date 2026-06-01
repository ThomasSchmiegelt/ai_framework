"""
Material database for engineering lookup.
"""

import json

MATERIALS: dict = {
    # Baustähle
    "s235": {
        "name": "S235 (St 37)",
        "category": "Baustahl",
        "E_GPa": 210, "G_GPa": 81, "Rp02_MPa": 235, "Rm_MPa": 360,
        "density_kg_m3": 7850, "thermal_expansion_1e6_K": 12,
        "thermal_conductivity_W_mK": 50, "poisson": 0.3,
        "notes": "Allgemeiner Baustahl; gut schweißbar; EN 10025"
    },
    "s355": {
        "name": "S355 (St 52)",
        "category": "Baustahl",
        "E_GPa": 210, "G_GPa": 81, "Rp02_MPa": 355, "Rm_MPa": 510,
        "density_kg_m3": 7850, "thermal_expansion_1e6_K": 12,
        "thermal_conductivity_W_mK": 50, "poisson": 0.3,
        "notes": "Hochfester Baustahl; EN 10025"
    },
    "42crmo4": {
        "name": "42CrMo4 (1.7225)",
        "category": "Vergütungsstahl",
        "E_GPa": 210, "G_GPa": 81, "Rp02_MPa": 900, "Rm_MPa": 1100,
        "density_kg_m3": 7850, "thermal_expansion_1e6_K": 12,
        "thermal_conductivity_W_mK": 42, "poisson": 0.3,
        "notes": "Häufig für Wellen, Zahnräder, Schrauben; gut härtbar"
    },
    "16mncr5": {
        "name": "16MnCr5 (1.7131)",
        "category": "Einsatzstahl",
        "E_GPa": 210, "G_GPa": 81, "Rp02_MPa": 635, "Rm_MPa": 780,
        "density_kg_m3": 7850, "thermal_expansion_1e6_K": 12,
        "thermal_conductivity_W_mK": 42, "poisson": 0.3,
        "notes": "Einsatzgehärtet, z.B. Zahnräder und Wellen"
    },
    "1.4301": {
        "name": "1.4301 (AISI 304, X5CrNi18-10)",
        "category": "Edelstahl",
        "E_GPa": 200, "G_GPa": 77, "Rp02_MPa": 230, "Rm_MPa": 560,
        "density_kg_m3": 7900, "thermal_expansion_1e6_K": 16,
        "thermal_conductivity_W_mK": 15, "poisson": 0.3,
        "notes": "Austenitischer Edelstahl; korrosionsbeständig; kein Magnet"
    },
    "1.4404": {
        "name": "1.4404 (AISI 316L, X2CrNiMo17-12-2)",
        "category": "Edelstahl",
        "E_GPa": 200, "G_GPa": 77, "Rp02_MPa": 220, "Rm_MPa": 540,
        "density_kg_m3": 7980, "thermal_expansion_1e6_K": 16,
        "thermal_conductivity_W_mK": 15, "poisson": 0.3,
        "notes": "Hochlegierter Edelstahl; säurebeständig; Chemie, Medizin"
    },
    "c45": {
        "name": "C45 (1.0503)",
        "category": "Vergütungsstahl",
        "E_GPa": 210, "G_GPa": 81, "Rp02_MPa": 490, "Rm_MPa": 700,
        "density_kg_m3": 7850, "thermal_expansion_1e6_K": 12,
        "thermal_conductivity_W_mK": 50, "poisson": 0.3,
        "notes": "Häufigster Vergütungsstahl; Wellen, Bolzen, Scheiben"
    },
    # Aluminium
    "almg3": {
        "name": "AlMg3 (EN AW-5754)",
        "category": "Aluminium",
        "E_GPa": 70, "G_GPa": 27, "Rp02_MPa": 80, "Rm_MPa": 190,
        "density_kg_m3": 2670, "thermal_expansion_1e6_K": 23,
        "thermal_conductivity_W_mK": 147, "poisson": 0.33,
        "notes": "Gut schweißbar, korrosionsbeständig; Schiffbau, Fahrzeugbau"
    },
    "en-aw-6061": {
        "name": "EN AW-6061 (AlMgSi1)",
        "category": "Aluminium",
        "E_GPa": 69, "G_GPa": 26, "Rp02_MPa": 275, "Rm_MPa": 310,
        "density_kg_m3": 2700, "thermal_expansion_1e6_K": 23.6,
        "thermal_conductivity_W_mK": 167, "poisson": 0.33,
        "notes": "Ausgehärtet (T6); Strukturbauteile, CNC-Bearbeitung"
    },
    "en-aw-7075": {
        "name": "EN AW-7075 (AlZnMgCu1,5, T6)",
        "category": "Aluminium",
        "E_GPa": 72, "G_GPa": 27, "Rp02_MPa": 503, "Rm_MPa": 572,
        "density_kg_m3": 2810, "thermal_expansion_1e6_K": 23.6,
        "thermal_conductivity_W_mK": 130, "poisson": 0.33,
        "notes": "Hochfestes Alu; Luft-/Raumfahrt, Sportgeräte"
    },
    # Titan
    "ti-6al-4v": {
        "name": "Ti-6Al-4V (Grade 5)",
        "category": "Titan",
        "E_GPa": 114, "G_GPa": 44, "Rp02_MPa": 880, "Rm_MPa": 950,
        "density_kg_m3": 4430, "thermal_expansion_1e6_K": 8.6,
        "thermal_conductivity_W_mK": 7, "poisson": 0.34,
        "notes": "Häufigste Titanlegierung; Luft-/Raumfahrt, Medizin, hohe Festigkeit bei geringem Gewicht"
    },
    # Gusseisen
    "en-gjl-250": {
        "name": "EN-GJL-250 (GG-25)",
        "category": "Grauguss",
        "E_GPa": 110, "G_GPa": 44, "Rp02_MPa": None, "Rm_MPa": 250,
        "density_kg_m3": 7200, "thermal_expansion_1e6_K": 11,
        "thermal_conductivity_W_mK": 48, "poisson": 0.26,
        "notes": "Guter Druckguss, gute Dämpfung; Maschinenbetten, Gehäuse; keine Zugstreckgrenze"
    },
    "en-gjs-400": {
        "name": "EN-GJS-400 (GGG-40, duktil)",
        "category": "Gusseisen",
        "E_GPa": 169, "G_GPa": 68, "Rp02_MPa": 250, "Rm_MPa": 400,
        "density_kg_m3": 7100, "thermal_expansion_1e6_K": 12,
        "thermal_conductivity_W_mK": 36, "poisson": 0.275,
        "notes": "Sphäroguss; zäher als GJL; Kurbelwellen, Pumpen"
    },
    # Kunststoffe
    "pa66": {
        "name": "PA 66 (Nylon 6,6)",
        "category": "Kunststoff",
        "E_GPa": 3.0, "G_GPa": 1.1, "Rp02_MPa": 55, "Rm_MPa": 75,
        "density_kg_m3": 1140, "thermal_expansion_1e6_K": 80,
        "thermal_conductivity_W_mK": 0.25, "poisson": 0.41,
        "notes": "Feuchtigkeitsempfindlich; Zahnräder, Lager, Strukturteile"
    },
    "pom": {
        "name": "POM (Acetal, Delrin)",
        "category": "Kunststoff",
        "E_GPa": 3.2, "G_GPa": 1.2, "Rp02_MPa": 65, "Rm_MPa": 68,
        "density_kg_m3": 1410, "thermal_expansion_1e6_K": 110,
        "thermal_conductivity_W_mK": 0.31, "poisson": 0.44,
        "notes": "Gute Maßhaltigkeit; Präzisionsteile, Gleitlager, Zahnräder"
    },
    "peek": {
        "name": "PEEK",
        "category": "Kunststoff",
        "E_GPa": 3.6, "G_GPa": 1.4, "Rp02_MPa": 91, "Rm_MPa": 100,
        "density_kg_m3": 1320, "thermal_expansion_1e6_K": 47,
        "thermal_conductivity_W_mK": 0.25, "poisson": 0.39,
        "notes": "Hochtemperaturbeständig bis 250°C; Luft-/Raumfahrt, Medizin"
    },
    "pp": {
        "name": "PP (Polypropylen)",
        "category": "Kunststoff",
        "E_GPa": 1.4, "G_GPa": 0.5, "Rp02_MPa": 30, "Rm_MPa": 35,
        "density_kg_m3": 905, "thermal_expansion_1e6_K": 150,
        "thermal_conductivity_W_mK": 0.22, "poisson": 0.42,
        "notes": "Leicht, chemisch beständig; Behälter, Rohre, Verpackung"
    },
    # Kupfer-Legierungen
    "cuzn37": {
        "name": "CuZn37 (Messing Ms63)",
        "category": "NE-Metall",
        "E_GPa": 100, "G_GPa": 37, "Rp02_MPa": 140, "Rm_MPa": 330,
        "density_kg_m3": 8440, "thermal_expansion_1e6_K": 20,
        "thermal_conductivity_W_mK": 120, "poisson": 0.35,
        "notes": "Gut spanbar; Armaturen, Elektrotechnik, Feinmechanik"
    },
    "cuzn5": {
        "name": "CuZn5 (Tombak, Goldbronze)",
        "category": "NE-Metall",
        "E_GPa": 120, "G_GPa": 45, "Rp02_MPa": 80, "Rm_MPa": 250,
        "density_kg_m3": 8860, "thermal_expansion_1e6_K": 18,
        "thermal_conductivity_W_mK": 230, "poisson": 0.34,
        "notes": "Hohe Leitfähigkeit; Münzen, Dekorteile"
    },
}


# Aliases for common alternate names
_ALIASES: dict[str, str] = {
    "st37": "s235", "st52": "s355",
    "304": "1.4301", "316l": "1.4404", "316": "1.4404",
    "7075": "en-aw-7075", "6061": "en-aw-6061",
    "grade5": "ti-6al-4v", "ti6al4v": "ti-6al-4v",
    "gg25": "en-gjl-250", "ggg40": "en-gjs-400",
    "nylon": "pa66", "delrin": "pom", "acetal": "pom",
    "messing": "cuzn37",
}


def material_lookup(name: str, prop: str = "") -> str:
    key = name.strip().lower().replace(" ", "-").replace(".", ".")
    # normalize dots
    key = key.replace(" ", "")

    # resolve alias
    resolved = _ALIASES.get(key, key)
    data = MATERIALS.get(resolved)

    # fuzzy fallback: search by substring
    if data is None:
        matches = [k for k in MATERIALS if key in k or k in key]
        if not matches:
            # try alias values
            for alias_key, alias_val in _ALIASES.items():
                if key in alias_key or alias_key in key:
                    matches.append(alias_val)
        if len(matches) == 1:
            data = MATERIALS[matches[0]]
        elif len(matches) > 1:
            names = ", ".join(MATERIALS[m]["name"] for m in matches[:8])
            return f"Mehrere Treffer gefunden: {names}\nBitte genauer angeben."
        else:
            available = ", ".join(m["name"] for m in MATERIALS.values())
            return (
                f"Werkstoff '{name}' nicht gefunden.\n"
                f"Verfügbare Werkstoffe: {available}"
            )

    if prop:
        prop_key = prop.strip().lower().replace(" ", "_")
        for k, v in data.items():
            if prop_key in k.lower():
                return f"{data['name']} — {k}: {v}"
        return f"Eigenschaft '{prop}' nicht gefunden für {data['name']}."

    # Full data sheet
    lines = [
        f"Werkstoffdatenblatt: {data['name']}",
        f"Kategorie: {data['category']}",
        "─" * 50,
        f"E-Modul:                {data['E_GPa']} GPa",
        f"Schubmodul G:           {data['G_GPa']} GPa",
        f"Querkontraktion ν:      {data['poisson']}",
    ]
    if data.get("Rp02_MPa") is not None:
        lines.append(f"Streckgrenze Rp0,2:     {data['Rp02_MPa']} MPa")
    lines += [
        f"Zugfestigkeit Rm:       {data['Rm_MPa']} MPa",
        f"Dichte:                 {data['density_kg_m3']} kg/m³",
        f"Wärmeausdehn. α:        {data['thermal_expansion_1e6_K']} × 10⁻⁶ /K",
        f"Wärmeleitfähigkeit λ:   {data['thermal_conductivity_W_mK']} W/(m·K)",
        "─" * 50,
        f"Hinweise: {data['notes']}",
    ]
    return "\n".join(lines)
