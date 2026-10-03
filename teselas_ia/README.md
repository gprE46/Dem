# Teselas ilustradas con IA (Gemini)

Convierte ortofotos PNOA (GeoTIFF) en una imagen ilustrada con perspectiva
isométrica para usarla de fondo en el mapa de fábricas.

Gemini solo funciona bien con zonas pequeñas, así que el proceso es:

1. **Preparar**: se unen las teselas, se baja la resolución, se inclina la vista
   (aplastando el eje vertical) y se corta en trozos de 1024×1024 que se solapan.
2. **Estilizar**: cada trozo pasa por Gemini con el mismo prompt y las mismas
   imágenes de referencia.
3. **Unir**: los trozos estilizados se funden en una sola imagen sin costuras.

Como la inclinación se aplica antes de la IA, todos los trozos comparten la misma
cámara y encajan entre sí. La IA solo tiene que levantar las fachadas y poner
el estilo.

## Requisitos

```bash
pip install pillow numpy
```

## 1. Preparar

```bash
python preparar.py T01.tif T02.tif T03.tif ... --salida trozos
```

Opciones:

| Opción | Por defecto | Qué hace |
|---|---|---|
| `--res` | `0.5` | metros por píxel. Con `1` salen 4 veces menos trozos |
| `--inclinacion` | `0.75` | `1` = cenital, `0.75` ≈ cámara a 49° (como la referencia 4), `0.5` = isométrica clásica |
| `--tam` | `1024` | lado de cada trozo |
| `--solape` | `256` | píxeles compartidos entre vecinos (más solape = uniones más suaves) |

Genera `trozos/trozo_fFF_cCC.png`, `mosaico_inclinado.jpg` y `manifiesto.json`.

## 2. Estilizar en Gemini

En cada conversación adjunta, **siempre en el mismo orden**:

1. el trozo (`trozo_f00_c00.png`…),
2. una o dos imágenes de referencia de estilo (las ilustraciones cenitales),
3. la imagen de referencia de perspectiva (la vista oblicua del polígono).

A partir del segundo trozo, añade también **el primer trozo ya estilizado**
como referencia para que todos salgan con el mismo estilo.

Prompt (en inglés funciona mejor):

```
Redraw IMAGE 1 as a hand-painted illustrated map, keeping its exact layout.

STRICT RULES
- Keep every building, road, roundabout, railway, parking lot, tree and field
  in exactly the same position, size and shape as in IMAGE 1. Do not add, remove
  or move anything. Do not crop, zoom, rotate or change the framing.
- Output a square image with the same framing as IMAGE 1, with no borders, text,
  labels, logos or watermark.

PERSPECTIVE
- IMAGE 1 is an oblique aerial view looking north, camera tilted about 50 degrees.
- Give each building real volume: keep the roof where it is and draw its
  south-facing wall hanging down toward the bottom of the image, with a height
  proportional to the building size (large warehouses ~12 m). Walls are light
  beige corrugated metal panels with loading docks and roller doors.
- Shadows always fall toward the bottom-right. Same light direction everywhere.

STYLE (like IMAGES 2 and 3)
- Clean dark ink outlines, flat painted colours with soft texture.
- Warm palette: ochre and terracotta dirt, warm grey asphalt with white road
  markings, cream/beige roofs with darker skylight squares, solar panels as
  dark blue grids, round stylised green trees, dry scrub as small olive dots.
- Detailed and crisp, like a high-quality strategy game map.
```

Guarda cada resultado con **el mismo nombre que el trozo original** en una
carpeta (por ejemplo `estilizados/trozo_f00_c00.png`).

Consejos:
- Si un trozo sale con otro encuadre o ha movido cosas, repítelo; no lo corrijas a mano.
- Si un trozo sale con otro tono, pídele: *"match the colours and line weight of
  the reference exactly"*.
- Si no te convence el resultado, puedes ajustar el prompt antes de seguir:
  - Si las fachadas salen demasiado altas o se comen la calle de abajo, baja la
    altura en el prompt (`~8 m`).
  - Si quieres más efecto 3D, prepara con `--inclinacion 0.6`.

## 3. Unir

```bash
python unir.py trozos/manifiesto.json estilizados/ --salida poligono_ilustrado.png
```

Si falta algún trozo estilizado se usa el original, así puedes ir viendo el
resultado a medida que avanzas.

## Coordenadas para las zonas clicables

`manifiesto.json` guarda los límites UTM, la resolución y la inclinación.
Para situar una nave (coordenadas UTM `E`, `N`) sobre la imagen final:

```
x = (E - oeste) / res
y = (norte - N) / res * inclinacion
```
