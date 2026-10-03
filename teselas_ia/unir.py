"""Une los trozos ya estilizados por la IA en una sola imagen sin costuras.

Guarda cada resultado de Gemini con el MISMO nombre que el trozo original
(trozo_f00_c00.png...) en una carpeta y ejecuta:
    python unir.py trozos/manifiesto.json estilizados/ --salida resultado.png

Las zonas solapadas se funden con un degradado para que no se noten los cortes.
Si falta algún trozo estilizado se usa el original.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def mascara(tam, solape, izq, arr, der, aba):
    """Peso 1 en el centro y rampa lineal en los bordes que comparten con un vecino."""
    rampa = np.linspace(0, 1, solape + 2)[1:-1] if solape else np.array([])
    eje_x = np.ones(tam)
    eje_y = np.ones(tam)
    if izq: eje_x[:solape] = rampa
    if der: eje_x[tam - solape:] = rampa[::-1]
    if arr: eje_y[:solape] = rampa
    if aba: eje_y[tam - solape:] = rampa[::-1]
    return np.outer(eje_y, eje_x)[..., None]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifiesto")
    ap.add_argument("estilizados", help="carpeta con los trozos devueltos por la IA")
    ap.add_argument("--salida", default="resultado.png")
    args = ap.parse_args()

    ruta_man = Path(args.manifiesto)
    man = json.loads(ruta_man.read_text())
    tam, solape = man["tam"], man["solape"]
    xs = sorted({t["x"] for t in man["trozos"]})
    ys = sorted({t["y"] for t in man["trozos"]})

    suma = np.zeros((man["alto"], man["ancho"], 3))
    pesos = np.zeros((man["alto"], man["ancho"], 1))

    for t in man["trozos"]:
        estilizado = Path(args.estilizados) / t["archivo"]
        if not estilizado.exists():
            estilizado = next(Path(args.estilizados).glob(Path(t["archivo"]).stem + ".*"), None)
        if estilizado is None:
            print(f"  falta {t['archivo']}, uso el original")
            estilizado = ruta_man.parent / t["archivo"]
        # Gemini puede devolver otro tamaño: se reescala al del trozo original
        img = np.asarray(Image.open(estilizado).convert("RGB").resize((tam, tam), Image.LANCZOS), dtype=float)
        x, y = t["x"], t["y"]
        m = mascara(tam, solape, x != xs[0], y != ys[0], x != xs[-1], y != ys[-1])
        suma[y:y + tam, x:x + tam] += img * m
        pesos[y:y + tam, x:x + tam] += m

    final = (suma / np.maximum(pesos, 1e-6)).clip(0, 255).astype(np.uint8)
    Image.fromarray(final).save(args.salida)
    print(f"Imagen final: {args.salida} ({man['ancho']}x{man['alto']})")


if __name__ == "__main__":
    main()
