"""
Engineering tools: unit conversion, symbolic solver, plotting, bolt calculation.
"""

import base64
import io
import json
import math
import re


def unit_convert(value: float, from_unit: str, to_unit: str) -> str:
    try:
        from pint import UnitRegistry, UndefinedUnitError, DimensionalityError
        ureg = UnitRegistry()
        qty = value * ureg(from_unit)
        result = qty.to(to_unit)
        return f"{value} {from_unit} = {result:.6g}"
    except Exception as e:
        return f"Umrechnungsfehler: {e}"


def solve_equation(expression: str, variable: str = "x") -> str:
    try:
        import sympy
        from sympy import symbols, solve, simplify, nsimplify
        from sympy.parsing.sympy_parser import (
            parse_expr, standard_transformations, implicit_multiplication_application
        )

        transformations = standard_transformations + (implicit_multiplication_application,)
        sym = symbols(variable)
        local = {variable: sym}

        if "=" in expression:
            lhs_str, rhs_str = expression.split("=", 1)
            lhs = parse_expr(lhs_str.strip(), local_dict=local, transformations=transformations)
            rhs = parse_expr(rhs_str.strip(), local_dict=local, transformations=transformations)
            eq = lhs - rhs
        else:
            eq = parse_expr(expression.strip(), local_dict=local, transformations=transformations)

        solutions = solve(eq, sym)
        if not solutions:
            return f"Keine Lösung für {variable} gefunden."

        lines = [f"Lösungen für {variable}:"]
        for sol in solutions:
            try:
                num = complex(sol.evalf())
                if num.imag == 0:
                    lines.append(f"  {variable} = {sol}  ≈  {num.real:.6g}")
                else:
                    lines.append(f"  {variable} = {sol}  ≈  {num:.6g}")
            except Exception:
                lines.append(f"  {variable} = {sol}")
        return "\n".join(lines)
    except Exception as e:
        return f"Fehler beim Lösen: {e}"


def plot_chart(
    x_data: list,
    y_data: list,
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    chart_type: str = "line",
    series_label: str = "",
    y2_data: list | None = None,
    y2_label: str = "",
) -> str:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 5.5))
        fig.patch.set_facecolor("#1e1e2e")
        ax.set_facecolor("#252535")

        for spine in ax.spines.values():
            spine.set_edgecolor("#444")
        ax.tick_params(colors="#ccc", labelsize=10)
        ax.xaxis.label.set_color("#ccc")
        ax.yaxis.label.set_color("#ccc")
        ax.title.set_color("#fff")
        ax.grid(True, color="#333", linewidth=0.8, alpha=0.7)

        color1 = "#4fc3f7"
        color2 = "#f48fb1"

        if chart_type == "bar":
            ax.bar(x_data, y_data, color=color1, label=series_label or y_label or "Werte", width=0.6)
        elif chart_type == "scatter":
            ax.scatter(x_data, y_data, color=color1, s=60, zorder=3,
                       label=series_label or y_label or "Werte")
        else:
            ax.plot(x_data, y_data, color=color1, linewidth=2,
                    label=series_label or y_label or "Werte")

        if y2_data:
            ax2 = ax.twinx()
            ax2.tick_params(colors="#ccc", labelsize=10)
            ax2.yaxis.label.set_color(color2)
            ax2.set_ylabel(y2_label, fontsize=11)
            ax2.plot(x_data, y2_data, color=color2, linewidth=2, linestyle="--",
                     label=y2_label or "Reihe 2")
            for spine in ax2.spines.values():
                spine.set_edgecolor("#444")

        if title:
            ax.set_title(title, fontsize=13, pad=10)
        if x_label:
            ax.set_xlabel(x_label, fontsize=11)
        if y_label:
            ax.set_ylabel(y_label, fontsize=11)

        handles, labels = ax.get_legend_handles_labels()
        if y2_data:
            h2, l2 = ax2.get_legend_handles_labels()
            handles += h2
            labels += l2
        if len(labels) > 1 or (series_label):
            ax.legend(handles, labels, facecolor="#2a2a3e", labelcolor="#ccc",
                      edgecolor="#555", fontsize=10)

        fig.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        plt.close(fig)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode()
        return json.dumps({"type": "image", "data": f"data:image/png;base64,{b64}"})
    except Exception as e:
        return f"Plot-Fehler: {e}"


def plot_function(
    expression: str,
    var: str = "x",
    x_min: float = -10.0,
    x_max: float = 10.0,
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    num: int = 400,
) -> str:
    """Plottet eine (oder mehrere, mit ``;`` getrennte) mathematische Funktion(en)
    über einen Wertebereich. Versteht ``^`` als Potenz, implizite Multiplikation
    (``2x``) und einen ``f(x)=``/``y=``-Vorsatz."""
    try:
        import numpy as np
        import sympy
        from sympy.parsing.sympy_parser import (
            parse_expr, standard_transformations,
            implicit_multiplication_application, convert_xor,
        )
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        transformations = standard_transformations + (
            implicit_multiplication_application, convert_xor)
        sym = sympy.symbols(var)

        raw_list = [e.strip() for e in re.split(r"[;\n]+", str(expression)) if e.strip()]
        if not raw_list:
            return "Plot-Fehler: keine Funktion angegeben."

        xs = np.linspace(float(x_min), float(x_max), max(50, int(num)))
        series = []   # (label, ys)
        for raw in raw_list:
            # „f(x) =" oder „y =" am Anfang entfernen
            term = re.sub(r"^\s*[A-Za-z]\w*\s*\([^)]*\)\s*=\s*", "", raw)
            term = re.sub(r"^\s*[A-Za-z]\w*\s*=\s*", "", term)
            expr = parse_expr(term, local_dict={var: sym}, transformations=transformations)
            f = sympy.lambdify(sym, expr, modules=["numpy"])
            with np.errstate(all="ignore"):
                ys = f(xs)
            ys = np.broadcast_to(np.asarray(ys, dtype=float), xs.shape).astype(float).copy()
            ys[~np.isfinite(ys)] = np.nan
            series.append((raw, ys))

        fig, ax = plt.subplots(figsize=(10, 5.5))
        fig.patch.set_facecolor("#1e1e2e")
        ax.set_facecolor("#252535")
        for spine in ax.spines.values():
            spine.set_edgecolor("#444")
        ax.tick_params(colors="#ccc", labelsize=10)
        ax.xaxis.label.set_color("#ccc")
        ax.yaxis.label.set_color("#ccc")
        ax.title.set_color("#fff")
        ax.grid(True, color="#333", linewidth=0.8, alpha=0.7)
        ax.axhline(0, color="#666", linewidth=1)
        ax.axvline(0, color="#666", linewidth=1)

        palette = ["#4fc3f7", "#f48fb1", "#aed581", "#ffb74d", "#ba68c8", "#4db6ac"]
        for i, (label, ys) in enumerate(series):
            ax.plot(xs, ys, color=palette[i % len(palette)], linewidth=2, label=label)

        ax.set_title(title or ("Funktion: " + ", ".join(r for r, _ in series)),
                     fontsize=13, pad=10)
        ax.set_xlabel(x_label or var, fontsize=11)
        if y_label:
            ax.set_ylabel(y_label, fontsize=11)
        if len(series) > 1:
            ax.legend(facecolor="#2a2a3e", labelcolor="#ccc",
                      edgecolor="#555", fontsize=10)

        fig.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        plt.close(fig)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode()
        return json.dumps({"type": "image", "data": f"data:image/png;base64,{b64}"})
    except Exception as e:
        return f"Plot-Fehler: {e}"


def bolt_calculator(
    d_nom: float,
    pitch: float,
    f_axial: float,
    mu: float = 0.15,
    material_class: str = "8.8",
    f_transverse: float = 0.0,
) -> str:
    """Schraubenauslegung nach VDI 2230 (vereinfacht)."""
    try:
        strength_map = {
            "4.6": (240, 400), "5.6": (300, 500), "6.8": (480, 600),
            "8.8": (640, 800), "10.9": (900, 1000), "12.9": (1080, 1200),
        }
        Rp02, Rm = strength_map.get(material_class, (640, 800))

        # Flankendurchmesser und Kerndurchmesser
        d2 = d_nom - 0.6495 * pitch
        d3 = d_nom - 1.2269 * pitch
        As = math.pi / 4 * ((d2 + d3) / 2) ** 2  # Spannungsquerschnitt [mm²]

        # Steigungswinkel und Reibungswinkel
        beta = math.atan(pitch / (math.pi * d2))
        rho = math.atan(mu / math.cos(math.radians(30)))

        # Hebelarm Gewindereibung
        lever = d2 / 2 * math.tan(beta + rho)

        # Plastisches Widerstandsmoment am Kernquerschnitt
        Wp = math.pi / 16 * d3 ** 3

        # VDI 2230: Vorspannkraft aus kombiniertem Von-Mises-Kriterium (σv = 0.9·Rp02)
        # σv = (Fv/As) · √(1 + 3·(As·lever/Wp)²) = 0.9·Rp02
        k_factor = math.sqrt(1 + 3 * (As * lever / Wp) ** 2)
        F_v = 0.9 * Rp02 * As / k_factor / 1000  # kN

        # Anzugsmoment MA [Nm]  (d_Km ≈ 1.4·d_nom für Standard-Sechskantschraube)
        d_Km_half = 0.7 * d_nom
        MA = F_v * 1000 * (lever + mu * d_Km_half) / 1000

        # Montagespannungen
        sigma_z_inst = F_v * 1000 / As
        tau_t = F_v * 1000 * lever / Wp if Wp > 0 else 0
        sigma_v_inst = math.sqrt(sigma_z_inst ** 2 + 3 * tau_t ** 2)
        auslastung_montage = sigma_v_inst / Rp02 * 100  # ≈ 90%

        # Betriebsspannung (keine Torsion im Betrieb — relaxiert nach Anzug)
        sigma_z_betrieb = f_axial * 1000 / As
        auslastung_betrieb = sigma_z_betrieb / Rp02 * 100

        # Abscherung (falls Querkraft vorhanden)
        tau_a = f_transverse * 1000 / As if f_transverse > 0 else 0

        lines = [
            f"Schraubenauslegung  M{d_nom} × {pitch}  —  FK {material_class}",
            "─" * 54,
            f"Spannungsquerschnitt As :       {As:.2f} mm²",
            f"Streckgrenze Rp0,2:             {Rp02} MPa",
            "",
            f"── Montage ──────────────────────────────────────",
            f"Vorspannkraft Fv (90% Rp0,2):  {F_v:.2f} kN",
            f"Anzugsmoment MA (µ={mu}):       {MA:.1f} Nm",
            f"Zugspannung σz (Montage):       {sigma_z_inst:.1f} MPa",
            f"Torsionsspannung τt:            {tau_t:.1f} MPa",
            f"Vergleichsspannung σv:          {sigma_v_inst:.1f} MPa",
            f"Montage-Auslastung:             {auslastung_montage:.1f} %",
            "",
            f"── Betrieb ──────────────────────────────────────",
            f"Axialkraft FA:                  {f_axial:.2f} kN",
            f"Zugspannung σz (Betrieb):       {sigma_z_betrieb:.1f} MPa",
            f"Betriebs-Auslastung:            {auslastung_betrieb:.1f} %",
        ]

        if f_transverse > 0:
            lines.append(f"Abscherspannung τa:             {tau_a:.1f} MPa")

        lines.append("─" * 54)
        auslastung = max(auslastung_montage, auslastung_betrieb)
        if auslastung < 70:
            lines.append("✓ Gut dimensioniert")
        elif auslastung < 100:
            lines.append("✓ Ausreichend dimensioniert")
        else:
            lines.append("⚠  Überlastet — größere Schraube oder höhere Festigkeitsklasse wählen!")

        return "\n".join(lines)
    except Exception as e:
        return f"Berechnungsfehler: {e}"
