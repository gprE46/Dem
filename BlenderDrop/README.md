# Blender Drop

**Envía cualquier asset compatible desde tu móvil directamente a la escena actual de Blender, por tu Wi‑Fi local.**

```
iPhone / móvil ──► Safari ──► Wi‑Fi ──► servidor HTTP del addon ──► cola ──► hilo principal de Blender ──► escena actual
```

- Sin app nativa, sin nube, sin cuentas, sin base de datos, sin servicios externos.
- La página móvil la sirve el propio addon (HTML + CSS + JavaScript, sin frameworks).
- Solo usa la librería estándar de Python y `bpy`. No hace falta instalar nada más.

**Versión recomendada:** Blender **5.2 LTS** (probado con 5.2.3). Compatible con Blender 4.2 o posterior como extensión y con 4.0/4.1 como addon clásico (no probado en 4.x).

---

## Instalación

1. Descarga **`BlenderDrop.zip`** (está en `dist/BlenderDrop.zip`).
2. Abre Blender.
3. Ve a **Edit → Preferences → Add-ons**.
4. Pulsa la flecha **⌄** arriba a la derecha y elige **Install from Disk…**
   (en Blender 4.0/4.1 es el botón **Install…**).
5. Selecciona `BlenderDrop.zip`. **No lo descomprimas.**
6. Comprueba que **Blender Drop** aparece activado (casilla marcada).

> También puedes arrastrar `BlenderDrop.zip` a la ventana de Blender (4.2+).

## Uso

1. Abre tu escena.
2. En el visor 3D pulsa **N** y abre la pestaña **Blender Drop**.
3. Pulsa **Start Server**. Aparecerá `Status: Running` y una URL tipo `http://192.168.1.35:8765`.
4. Pulsa **Show QR** y escanéalo con la cámara del iPhone, o pulsa **Copy URL** y envíate el enlace.
5. Se abre Safari con la página **BLENDER DROP** → debe decir **Conectado ✓**.
6. Pulsa **Seleccionar archivo** y elige, por ejemplo, un `.glb` (de *Archivos*, iCloud Drive, AirDrop…).
7. Espera: verás *Subiendo… 45 %* → *Subido ✓* → *Importando…* → **Importado correctamente ✓**.
8. El asset aparece en Blender dentro de la colección **Blender Drop / NombreDelArchivo**.

Puedes elegir varios archivos a la vez: se envían uno detrás de otro.
Mantén la página abierta y la pantalla del móvil encendida hasta que termine (iOS corta las subidas en segundo plano).

---

## El panel de Blender

| Elemento | Qué hace |
|---|---|
| **Status** | 🟢 Running / 🔴 Stopped |
| **Server URL** | Dirección LAN detectada automáticamente. Si el ordenador tiene varias redes aparece un selector **Address**. |
| **Start Server / Stop Server** | Inicia o para el servidor. Cada inicio genera un **token nuevo**, así que los enlaces antiguos dejan de funcionar. |
| **Copy URL** | Copia la URL completa **con token** al portapapeles. |
| **Show QR** | Muestra un QR con la URL completa. **Open QR Image** lo abre en grande con el visor de imágenes del sistema. |
| **Last received file** | Último archivo recibido y su estado. |
| **Import Options** | ☑ Create collection · ☐ Move imported asset to origin · ☐ Images as planes · tamaño máximo. |
| **Recent Files** | Historial en memoria (✓ / ✗). Se pierde al cerrar Blender. |

Más ajustes en **Preferences → Add-ons → Blender Drop**: puerto (8765 por defecto), tamaño máximo de subida (2048 MB), carpeta temporal, conservar archivos recibidos, iniciar el servidor al abrir Blender y log detallado.

---

## Formatos

Cada formato usa el importador oficial de Blender. El addon comprueba al arrancar qué importadores existen en **tu** versión de Blender: la página móvil rechaza antes de subir los que no estén disponibles, y el panel los lista en *Import Options*.

| Formato | Operador de Blender | Blender 5.2 |
|---|---|---|
| `.glb` `.gltf` | `import_scene.gltf` | ✅ |
| `.fbx` | `import_scene.fbx` (alternativa: `wm.fbx_import`) | ✅ |
| `.obj` | `wm.obj_import` | ✅ |
| `.stl` | `wm.stl_import` (4.0/4.1: `import_mesh.stl`) | ✅ |
| `.ply` | `wm.ply_import` | ✅ |
| `.usd` `.usda` `.usdc` `.usdz` | `wm.usd_import` | ✅ |
| `.abc` | `wm.alembic_import` | ✅ |
| `.svg` | `import_curve.svg` (alternativa: Grease Pencil SVG) | ✅ |
| `.bvh` | `import_anim.bvh` | ✅ |
| `.vdb` | `object.volume_import` | ✅ |
| `.blend` | `bpy.data.libraries.load` (append) | ✅ |
| `.png` `.jpg` `.jpeg` `.tif` `.tiff` `.exr` `.hdr` `.bmp` `.tga` `.webp` | `bpy.data.images.load` | ✅ |
| `.zip` | se descomprime y se importa lo que contiene | ✅ |
| `.dae` (Collada) | `wm.collada_import` | ❌ **Blender lo eliminó en la 5.0** (sí funciona en 4.x) |
| `.x3d` `.wrl` | `import_scene.x3d` | ⚠️ solo si instalas la extensión oficial *Web3D X3D/VRML2* |

### `.zip`: modelos con archivos externos
Un `.gltf` con su `.bin` y texturas, o un `.obj` con su `.mtl` y texturas, **son varios archivos**. Envíalos en un `.zip` (o usa `.glb`, que va en un solo archivo). Blender Drop descomprime el zip de forma segura (ignora rutas peligrosas, enlaces simbólicos y `__MACOSX`) e importa los modelos que encuentre. Si el zip solo tiene imágenes, las carga como imágenes.

### `.blend`: nunca reemplaza tu escena
Un `.blend` recibido **no se abre**: se hace **append** de su contenido.
1. Se añade temporalmente la primera escena del archivo.
2. Sus colecciones (con su jerarquía) y objetos sueltos se enlazan en `Blender Drop / NombreDelArchivo`.
3. La escena temporal se elimina. Tu escena, tus objetos y tus ajustes no se tocan.

Límites: solo se trae la primera escena del archivo (por orden alfabético). Los mundos, el compositor y la configuración de render de esa escena no se traen. Las texturas externas que el `.blend` no tenga empaquetadas quedarán rotas (empaquétalas con *File → External Data → Pack Resources* antes de enviarlo).

### Imágenes
Las imágenes se cargan en Blender (*Image Editor → Browse*), se **empaquetan** dentro del `.blend` y se marcan con *Fake User* para que no se pierdan al guardar. Con **Images as planes** se crea además un plano con la textura.

### Organización y posición
- Cada importación va a su propia colección: `Blender Drop / Robot`. Si el importador crea colecciones propias, se meten dentro de esa.
- Los objetos existentes no se mueven ni se modifican.
- Por defecto el asset **conserva las coordenadas del archivo**. *Move imported asset to origin* lo centra en el origen.
- Cada importación crea un paso de deshacer: **Ctrl+Z** la quita.
- Si estabas en modo Edición, Blender Drop pasa a modo Objeto antes de importar (los importadores lo necesitan).

---

## Seguridad

- Al pulsar **Start Server** se genera un **token aleatorio** de sesión (128 bits). La URL lo incluye: `http://192.168.1.35:8765/?token=…`.
- Sin token válido el servidor rechaza `/upload` y `/status` (401/403). Al reiniciar el servidor cambia el token.
- Escucha en `0.0.0.0` (toda tu red local). No uses Blender Drop en redes públicas o de desconocidos: cualquiera que vea la URL con el token podría enviarte archivos.
- Los nombres de archivo se sanean (sin rutas, sin caracteres de control, sin nombres reservados de Windows), conservando espacios, acentos y Unicode.
- Los archivos se guardan en una carpeta propia del addon, uno por subcarpeta, y se **borran después de importarlos**. Solo se conservan si Blender sigue leyéndolos (p. ej. la caché de un `.abc`) o si activas *Keep received files*.
- Límite de tamaño configurable, comprobación de espacio libre en disco y límite de tamaño al descomprimir zips.
- Un `.blend` puede contener drivers o scripts de Python. Blender no los ejecuta salvo que tengas activado *Auto Run Python Scripts*. Acepta `.blend` solo de fuentes de confianza.

## Arquitectura (threads)

```
Hilo HTTP (ThreadingHTTPServer)            Hilo principal de Blender
───────────────────────────────            ────────────────────────────────
recibe la subida en streaming      queue   bpy.app.timers (cada 0,25 s)
guarda el archivo en disco   ──put(id)──►  get_nowait() → estado IMPORTING
responde 202 {job}                         importer.py → operador oficial de bpy
                                           estado IMPORTED / ERROR → redibuja UI
```

- `server.py`, `multipart.py`, `jobs.py`, `qr.py` y `utils.py` **no importan `bpy`**. El hilo HTTP nunca toca la escena.
- `runtime.py`, `importer.py` y `ui.py` usan `bpy` **solo desde el hilo principal** (operadores, timer).
- Los estados se comparten mediante un `JobStore` protegido con un `threading.Lock`.
- El parser multipart es propio y en streaming (el módulo `cgi` ya no existe en el Python 3.13 de Blender 5.x): una subida de 1 GB usa unos pocos MB de memoria.
- Un error en un archivo nunca para el servidor ni bloquea Blender.

Estados que ve el móvil: `UPLOADING → UPLOADED → QUEUED → IMPORTING → IMPORTED` o `ERROR`. La página consulta `GET /status?job=<id>` (polling HTTP sencillo, sin WebSockets).

API: `GET /` (página), `GET /api/ping`, `GET /status`, `GET /status?job=<id>`, `POST /upload` (`multipart/form-data`, o el cuerpo tal cual con la cabecera `X-Filename`). Todas menos la página requieren `?token=` o la cabecera `X-BlenderDrop-Token`.

---

## Solución de problemas

**La página no carga / "Blender no responde"**
- El móvil y el ordenador tienen que estar **en la misma Wi‑Fi**. Una red de invitados o con *aislamiento de clientes (AP isolation)* impide que los dispositivos se vean: usa la red principal.
- Desactiva las VPN en el móvil y en el ordenador mientras pruebas.
- Comprueba que el panel dice **Running**.
- Usa `http://`, no `https://`. Safari puede mostrar "No seguro": es normal en una red local.

**Firewall**
- **Windows:** la primera vez que pulses *Start Server* aparecerá *Windows Defender Firewall*. Permite a **Blender** en **redes privadas**. Si lo rechazaste: *Panel de control → Firewall de Windows Defender → Permitir una aplicación… → Blender → Privada*. Tu Wi‑Fi debe estar configurada como red *Privada*, no *Pública*.
- **macOS:** si macOS pregunta si Blender puede *aceptar conexiones entrantes*, pulsa **Permitir**. Revisa *Ajustes del Sistema → Red → Firewall → Opciones*. En macOS 15 o posterior, permite también a Blender en *Privacidad y seguridad → Red local*.
- **Linux:** abre el puerto si usas un firewall, p. ej. `sudo ufw allow 8765/tcp`.
- Blender Drop **no modifica el firewall** por sí mismo.

**IP local incorrecta**
- Si el ordenador tiene varias redes (Ethernet + Wi‑Fi, máquinas virtuales, VPN…), elige la correcta en **Address** (suele empezar por `192.168.` o `10.`). Pulsa el botón ⟳ de *Recent Files* para detectarlas de nuevo si cambiaste de red.

**"Port 8765 is already in use"**
- Otro programa (u otra instancia de Blender) usa el puerto. Cambia **Port** (p. ej. 8766) y pulsa *Start Server*.

**"Invalid link" / "Enlace no válido"**
- El servidor se reinició y el token cambió. Vuelve a escanear el QR o a copiar la URL.

**Formato no soportado / importador no disponible**
- Mira la tabla de formatos. `.dae` ya no existe en Blender 5.x: conviértelo a `.glb` o `.fbx`. Para `.x3d`/`.wrl` instala la extensión oficial *Web3D X3D/VRML2*.
- Si desactivaste los addons incluidos *glTF 2.0*, *FBX* o *SVG*, Blender Drop los activa solo para la sesión actual cuando los necesita.

**El modelo llega sin texturas**
- El archivo hacía referencia a texturas externas que no se enviaron. Envía un `.glb` o un `.zip` con el modelo y sus texturas. El aviso aparece en el detalle del estado.

**Error de importación**
- El mensaje del importador de Blender se muestra en el móvil y en el panel. Para más detalle abre la consola de Blender (*Window → Toggle System Console* en Windows, o arranca Blender desde una terminal en macOS/Linux).

**Archivo demasiado grande**
- Sube el límite en *Import Options → Max upload (MB)*.

---

## Desarrollo

```
BlenderDrop/
├── addon/blender_drop/
│   ├── __init__.py            registro (bl_info para 4.0/4.1)
│   ├── blender_manifest.toml  manifiesto de extensión (4.2+)
│   ├── server.py              servidor HTTP (sin bpy)
│   ├── multipart.py           parser multipart en streaming (sin bpy)
│   ├── jobs.py                cola + estados thread-safe (sin bpy)
│   ├── qr.py                  generador QR + PNG en Python puro (sin bpy)
│   ├── utils.py               nombres, extensiones, IP LAN (sin bpy)
│   ├── runtime.py             arranque/parada, timer del hilo principal
│   ├── importer.py            handlers por extensión → operadores de Blender
│   ├── ui.py                  panel, operadores, preferencias
│   └── web/                   index.html, style.css, app.js
├── tests/
├── dist/BlenderDrop.zip       ZIP instalable
├── build_addon.py
├── LICENSE                    GPL-3.0-or-later (obligatoria para addons que usan bpy)
└── README.md
```

**Generar el ZIP** (cualquier Python 3, sin dependencias):

```bash
python build_addon.py      # → dist/BlenderDrop.zip
```

**Tests sin Blender** (servidor real por sockets, parser, saneado, QR):

```bash
python -m unittest discover -s tests -v
```

**Tests con Blender real** (con el binario de Blender o con el módulo `pip install bpy==5.2.3` en Python 3.13):

```bash
blender -b --factory-startup --python tests/blender_integration.py          # importa todos los formatos de verdad
blender -b --factory-startup --python tests/blender_ui_draw_test.py         # dibuja el panel contra la API real
blender -b --factory-startup --python tests/blender_install_test.py -- dist/BlenderDrop.zip extension
python tests/web_e2e.py      # con bpy + Playwright: Chromium (emulando iPhone) sube un .glb
```

### Comprobado automáticamente (Blender 5.2.3 LTS)
- Instalación del ZIP como extensión y como addon clásico, validación con `blender --command extension validate`, activar/desactivar.
- Inicio, parada y reinicio del servidor; el puerto se libera; desactivar el addon para el servidor y el timer.
- Importación real por HTTP de `.glb`, `.fbx`, `.obj`, `.stl`, `.ply`, `.usd/.usda/.usdc/.usdz`, `.abc`, `.svg`, `.blend` (con jerarquía), `.zip` (gltf + bin + textura, obj + mtl + textura), `.png`, `.jpg`, `.exr`.
- Errores: glb/png corruptos, `.gltf` sin `.bin`, `.dae` no disponible, token incorrecto, extensión desconocida, archivo vacío o demasiado grande, conexión cortada a mitad de la subida, subidas simultáneas.
- Los objetos existentes no cambian; las carpetas temporales se borran; las texturas recibidas se empaquetan.
- Subida de 1 GB: memoria estable (~1 MB extra).
- La página móvil en Chromium emulando un iPhone 13, en español y en inglés.

### Por probar a mano (necesita interfaz gráfica, red real o un iPhone)
- [ ] El panel se ve en la barra lateral (N) y se actualiza solo al recibir archivos.
- [ ] La vista previa del QR se ve en el panel y el iPhone lo escanea (alternativa: *Open QR Image*).
- [ ] *Copy URL* copia al portapapeles del sistema.
- [ ] Safari en un iPhone real por Wi‑Fi: elegir un archivo de *Archivos*/iCloud, ver el progreso y el resultado.
- [ ] Aviso del firewall de Windows/macOS la primera vez.
- [ ] Ctrl+Z deshace una importación.

## Licencia

GPL-3.0-or-later. Ver [LICENSE](LICENSE).
