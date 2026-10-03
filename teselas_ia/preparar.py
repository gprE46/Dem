"""Prepara ortofotos (GeoTIFF) para estilizarlas con IA por trozos.

1. Une una o varias teselas GeoTIFF en un mosaico usando su georreferencia.
2. Baja la resolución (por defecto 0,5 m/píxel).
3. Aplasta el eje vertical para simular una cámara inclinada con el norte arriba
   (como una vista isométrica) antes de pasar por la IA.
4. Corta el resultado en trozos cuadrados solapados que Gemini sí procesa bien.

Uso:
    python preparar.py T01.tif T02.tif ... --salida trozos
"""
import argparse
import json
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None

TAG_ESCALA = 33550    # ModelPixelScaleTag
TAG_TIEPOINT = 33922  # ModelTiepointTag


def georreferencia(img):
    escala = img.tag_v2.get(TAG_ESCALA)
    tie = img.tag_v2.get(TAG_TIEPOINT)
    if not escala or not tie:
        raise SystemExit(f"{img.filename}: no tiene georreferencia GeoTIFF")
    # Esquina superior izquierda (X, Y) y tamaño de píxel en metros
    return tie[3] - tie[0] * escala[0], tie[4] + tie[1] * escala[1], escala[0], escala[1]


def mosaico(rutas, res):
    teselas = []
    for ruta in rutas:
        img = Image.open(ruta)
        x0, y0, px, py = georreferencia(img)
        teselas.append((ruta, img, x0, y0, x0 + img.width * px, y0 - img.height * py))

    oeste = min(t[2] for t in teselas)
    norte = max(t[3] for t in teselas)
    este = max(t[4] for t in teselas)
    sur = min(t[5] for t in teselas)
    lienzo = Image.new("RGB", (round((este - oeste) / res), round((norte - sur) / res)), "white")

    for ruta, img, x0, y0, x1, y1 in teselas:
        tam = (round((x1 - x0) / res), round((y0 - y1) / res))
        pieza = img.convert("RGB").resize(tam, Image.LANCZOS)
        lienzo.paste(pieza, (round((x0 - oeste) / res), round((norte - y0) / res)))
        print(f"  {Path(ruta).name}: {img.width}x{img.height} -> {tam[0]}x{tam[1]}")

    return lienzo, {"oeste": oeste, "norte": norte, "este": este, "sur": sur}


def posiciones(total, tam, paso):
    if total <= tam:
        return [0]
    pos = list(range(0, total - tam, paso))
    return pos + [total - tam]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("teselas", nargs="+", help="GeoTIFF de entrada")
    ap.add_argument("--salida", default="trozos", help="carpeta de salida")
    ap.add_argument("--res", type=float, default=0.5, help="metros por píxel del resultado (0.5 por defecto)")
    ap.add_argument("--inclinacion", type=float, default=0.75,
                    help="factor de aplastado vertical: 1 = cenital, 0.75 ≈ cámara a 49°, 0.5 = isométrica clásica")
    ap.add_argument("--tam", type=int, default=1024, help="lado de cada trozo en píxeles")
    ap.add_argument("--solape", type=int, default=256, help="píxeles compartidos entre trozos vecinos")
    args = ap.parse_args()

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)

    print("Uniendo teselas...")
    lienzo, limites = mosaico(args.teselas, args.res)
    lienzo = lienzo.resize((lienzo.width, round(lienzo.height * args.inclinacion)), Image.LANCZOS)
    lienzo.save(salida / "mosaico_inclinado.jpg", quality=92)
    print(f"Mosaico inclinado: {lienzo.width}x{lienzo.height}")

    paso = args.tam - args.solape
    trozos = []
    for f, y in enumerate(posiciones(lienzo.height, args.tam, paso)):
        for c, x in enumerate(posiciones(lienzo.width, args.tam, paso)):
            nombre = f"trozo_f{f:02d}_c{c:02d}.png"
            lienzo.crop((x, y, x + args.tam, y + args.tam)).save(salida / nombre)
            trozos.append({"archivo": nombre, "x": x, "y": y})

    manifiesto = {
        "ancho": lienzo.width, "alto": lienzo.height, "tam": args.tam, "solape": args.solape,
        "res": args.res, "inclinacion": args.inclinacion, "limites_utm": limites,
        "crs": "ETRS89 / UTM 30N (EPSG:25830)", "trozos": trozos,
    }
    (salida / "manifiesto.json").write_text(json.dumps(manifiesto, indent=2, ensure_ascii=False))
    print(f"{len(trozos)} trozos de {args.tam}px guardados en {salida}/")


if __name__ == "__main__":
    main()
