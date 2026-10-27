# Tekken 6 (PSP) — mapa de memoria verificado en vivo

**Build:** `ULES01376` v1.00 · PPSSPP 1.19.3 · Practice mode · savestate slot 0
**Método:** snapshots externos (`ReadProcessMemory`) sincronizados a ~59-60 Hz + películas de RAM
**Estado:** núcleo de frame data **validado** contra TekkenDocs.

---

## 0. Corrección crítica de la investigación previa

`tasks/02-tekken6-memory.md` §5.1 citaba bien la traducción CWCheat
(`(valor + 0x08800000) & 0x3FFFFFFF`) pero luego **no la aplicó**:

| Reporte anterior | Real |
|---|---|
| `0x088078C0` (salud P1) | **`0x08C078C0`** (`0x004078C0 + 0x08800000`) |
| `0x088081F0` (salud P2) | **`0x08C081F0`** |
| `0x0880796C` (state) | **`0x08C0796C`** |

`0x0880xxxx` cae dentro del EBOOT (código MIPS); por eso allí solo había instrucciones.

**Regla general:** toda dirección del reporte anterior se desplaza **+0x400000**.

---

## 1. Base del struct de jugador

El struct **no** está anclado en `0x08806FD0`. Está anclado en la **dirección de salud**:

```
P1_BASE = 0x08C078C0      (u32: 0x00B40000 = 180 HP; valor u16 en +0x2)
P2_BASE = 0x08C081F0
STRIDE  = 0x930
```

Confirmación independiente: el bloque P1 contiene en `base+0x30` un puntero a
`0x08C08190` (posición de P2) y el bloque P2 contiene en `base+0x30` el puntero
`0x08C07860` (posición de P1). Es decir, cada bloque apunta a la posición del rival.

---

## 2. Campos verificados

Offsets relativos a `BASE` (sirven igual para P1 con `0x08C078C0` y P2 con `0x08C081F0`).

| Campo | Offset | Tipo | P1 | P2 | Evidencia |
|---|---|---|---|---|---|
| salud | `+0x02` | u16 | `0x08C078C2` | `0x08C081F2` | 180 → 172 al comer un jab de 8 |
| **contador de estado** | `+0x7C` | u32 | **`0x08C0793C`** | **`0x08C0826C`** | +1 por frame; se reinicia a 1 en cada cambio de estado |
| **estado** | `+0xAC` | u16 | **`0x08C0796C`** | **`0x08C0829C`** | `6482` STANDING, `14633` CROUCHING, `2114` ataque de pie, `10273` ataque agachado |
| id de animación | `+0x158` | u32 | `0x08C07A18` | `0x08C08348` | `32769` en reposo → `440` (`1`) / `492` (`2`) |
| descriptor de move A | `+0x144` | u32 | `0x08C07A04` | `0x08C08334` | `0` reposo → `7` (`1`) / `10` (`2`) |
| descriptor de move B | `+0x168` | u32 | `0x08C07A28` | `0x08C08358` | `0` reposo → `8` (`1`) / `12` (`2`) |
| descriptor de move C | `+0x180` | u32 | `0x08C07A40` | `0x08C08370` | `80` reposo → `27` (`1`) / `28` (`2`) |
| **libre / bloqueado** | `+0x294` | u8 | `0x08C07B54` | `0x08C08484` | `1` = puede actuar, `0` = bloqueado; **parpadea a 0 un frame en reposo** |
| **cola de recuperación** | `+0x295` | u8 | `0x08C07B55` | `0x08C08485` | sube a `1` **exactamente** en el frame accionable y dura hasta el fin de la animación |
| pad emulado | — | u16 | `0x08BFE4AC` | — | bits PSP: `0x8000` □, `0x1000` △, `0x4000` ✕, `0x2000` ○ |

Los campos `+0x158`, `+0x144`, `+0x168` y `+0x180` se fijan al empezar el
movimiento y **permanecen constantes** durante todo él. El tracker usa `+0x144`
como **ID de movimiento** (no una tabla de nombres): `7`=`1`, `10`=`2`, `14`=`3`,
`15`=`4`, `13`=`1+2`, `20`=`3+4`, `12`=`4,3`.

> `+0x144` puede **parpadear** con un valor intermedio (p. ej. `15 → 16 → 12` en
> el mismo frame al encadenar `4,3`). Hay que aceptar un ID solo si sobrevive un
> segundo muestreo o cruza al frame siguiente.

### Valores de estado observados

| Valor | Significado | Nota |
|---|---|---|
| `6482` (`0x1952`) | STANDING | coincide con el enum de PS3 |
| `6466` (`0x1942`) | STANDING del dummy | variante de guardia/reposo |
| `14633` (`0x3929`) | CROUCHING | coincide con el enum de PS3 |
| `2114` (`0x0842`) | ataque de pie / stun | **mismo valor para atacante y defensor** |
| `10273` (`0x2821`) | ataque desde agachado | |

> El estado por sí solo **no** distingue atacante de defensor: ambos usan `2114`.
> El contexto (quién cambió de estado primero y quién perdió vida) resuelve la ambigüedad.

---

## 3. El ID de movimiento y el contador de animación

`+0x7C` incrementa **exactamente 1 por frame** y se reinicia a `1` en cada
cambio de animación (de pie → agachado, de pie → ataque, ataque → reposo).
`+0x144` es el ID del movimiento actual.

```
inicio = frame en que aparece un ID de movimiento nuevo (o cambia de segmento)
startup = frame_de_impacto - inicio + 1
```

**El frame de impacto es el de la vida.** Al recibir un golpe, el estado de
reacción del defensor llega **un frame después** de que el hitbox conecta:

| frame | `+0x7C` P1 | `+0x144` P1 | vida P2 | estado P2 |
|---|---|---|---|---|
| 3 | 1 | 7 | 180 | 6466 |
| 12 | 10 | 7 | **172** | 6466 |
| 13 | 11 | 7 | 172 | **2114** |

`12 − 3 + 1 = 10` → `i10` (jab de Bryan). Si se mide en el frame de la reacción
sale `i11`. Por eso el golpe se detecta **en la bajada de vida**. En bloqueo no
hay bajada: la guardia (`4178`) y el reinicio del contador del defensor llegan en
el **mismo** frame (comprobado frame a frame), así que ahí el impacto es ese.

Verificado contra TekkenDocs (Bryan, T6): `1` → 10, `2` → 10, `3` → 16,
`4` → 12, `1+2` → 14, `3+4` → 18.

Durante el reposo el contador sigue subiendo (es "frames en la animación actual"),
así que un valor grande = reposo prolongado.

---

## 4. Cálculo de la ventaja (validado)

Cronología de un movimiento, en frames de juego:

```
inicio ──startup──► CONTACTO ──recuperación──► +0x295 sube (atacante libre)
                        └────── stun ────────► +0x295 sube (defensor libre)
```

El flag `+0x295` **sube exactamente en el frame en que ese jugador puede actuar**
y se mantiene hasta el fin de la animación (que sigue ~12 frames más). Por eso:

```
ventaja = t(+0x295 de P2) − t(+0x295 de P1)
```

No depende de cuándo se detecte el contacto: son dos sucesos absolutos medidos
en la misma rejilla de frames.

### Detección del contacto

Se detecta con el **defensor**, con dos señales inequívocas que llegan en el
frame exacto del impacto:

- **golpe** → su vida (`+0x02`) baja en ese frame;
- **bloqueo** → su estado (`+0xAC`) pasa a guardia `4178`, o su contador de
  animación (`+0x7C`) vuelve a `1` estando ya en `4178` (2.º golpe de un string).

`estado ≠ idle` **no** es señal válida: `2114` es ataque *y* hitstun, y el juego
escribe el estado varias muestras antes que el ID de movimiento. Al empezar el
defensor su propio ataque se veía `estado=2114` con `id=0` durante varias
muestras y se colaba como "reacción" (impactos y ventajas absurdas tras cada
jab). `+0x294` tampoco se usa: cae 1–2 frames más tarde y parpadea a 0.

Comprobado frame a frame: al iniciar un bloqueo, el contador de animación y el
estado `4178` aparecen en la **misma** muestra.

### Validación contra TekkenDocs (Bryan, T6, dummy en Guard All)

| Move | startup medido | startup oficial | ventaja medida | oficial |
|---|---|---|---|---|
| `1` | **10** | 10 | **+1** | +1 |
| `2` | **10** | 10 | **+1** / 0 | +1 |
| `3` | **16** | 16 | **−4** | −4 |
| `4` | **12** | 12 | **−5** / −6 | −5 |
| `1+2` | **14** | 14 | **−10** | −10 |
| `3+4` | **18** | 18 | **−8** / −7 | −8 |
| `4,3` (2.º golpe) | **24** | — | **−7** | −7 |

El ±1 de la ventaja es **real**: depende de qué frame activo conecta y del
hitstop, no del medidor.

### Strings

En `4,3` el ID cambia a `12` **en el mismo frame del primer contacto** y el
defensor vuelve a bloquear 25 frames después. El motor emite un resultado por
contacto y, si un contacto interrumpe la medición anterior, la publica sin
ventaja (la cadena sigue):

```
impact 4    i12   —   BLOCK
impact 4,3  i24   —   BLOCK
final  4,3  i24  −7   BLOCK
```

### On hit (validado)

Con el dummy en `DE PIE` (sin guardia) la vida baja en el frame del impacto; el
estado de reacción llega uno después. El tracker marca `HIT` **en la bajada de
vida** y toma ese frame como impacto (así el `1` mide `i10`, no `i11`).

| Move | startup | ventaja en golpe | oficial |
|---|---|---|---|
| `1` | **10** | **+8** | +8 |

En bloqueo el mismo `1` mide `+1` y se etiqueta `BLOCK`.

### Defensa y PUNISH (validado)

El dummy P2 ataca y P1 bloquea. Un **segundo `Tracker`** recibe la muestra
invertida (`mirror`), así que el mismo motor mide al atacante P2 sin duplicar
lógica. Los dos trackers reciben **el mismo frame de juego** (`feed`), así que
sus eventos se comparan directamente.

```
P2 ataca        contacto ────────────────┐
P1 bloquea      U1 (libre) ─────┐       │
P2 recupera                    │       │
                            ventana = U2 − U1
```

| string del dummy (Bruce) | startup | bloqueo |
|---|---|---|
| `7` / `10` / `12` | i11 / i19 / i20 | **−15** (solo el `12`) |
| `15, 18` | i15 / i16 | **−11** |
| `11, 21` | i14 / i24 | **−6** |
| `7, 8, 7, 14` | i11 / i13 / i20 / i20 | **−12** |

**PUNISH**: solo se marca **cuando el golpe conecta**. Se guarda el último
bloqueo real (aunque ya haya publicado su `final`) y se exige, con el reloj
compartido:

1. `start ≥ u_p` (P1 empezó después de quedar libre);
2. `impact ≤ u_d + 1` (conectó mientras el dummy seguía bloqueado);
3. `startup ≤ |u_p − u_d| + 1` (el golpe cabe en la desventaja del dummy).

Validado con un jab sobre el ender `−15`: `P1 1 i10 +8 HIT PUNISH`. No se avisa
de la ventana antes de tiempo: la ventaja solo se conoce en el frame en que el
dummy se libera.

Cuando el castigo entra, el `final` del dummy se publica sin valor (`—`):
su recuperación quedó cortada y el número del bloqueo ya no aplica (antes se
publicaban valores basura como `−55` sacados de un pulso espurio de la cola
durante el hitstun).

**Reloj compartido (fallo de raíz, corregido).** Cada tracker contaba frames
con el contador `+0x7C` de *su* jugador. Ese contador **repite valores** (salta
de `10` a `12`, se queda igual una muestra en algunos arranques), así que los
dos relojes derivaban: **~17 frames** en una sesión real de 10 minutos
(6980 frames registrados). La comprobación de castigo comparaba entonces un
frame de P1 con un frame de P2, es decir, dos relojes distintos, y la ventana
salía desplazada. Ahora `TrackerThread.feed` mantiene **un solo contador** (el
de P1, que cambia exactamente una vez por frame de juego) y se lo pasa a los
dos trackers con `Tracker.step(s, frame)`.

**Filtro de contacto**: `2114` es a la vez ataque y **hitstun**, así que el
estado no sirve para separar ataque de reacción. Las únicas señales válidas son
la **vida que baja** (golpe) y la **guardia `4178`** (bloqueo); el `move id`
del defensor ya no se usa.

---

## 5. Protocolo de muestreo

- `ReadProcessMemory` de 24 KB o de 1 MB cuesta lo mismo (~16.7 ms): la lectura
  se sincroniza con el frame del emulador. Resultado: **~59-60 muestras/s**.
- Una lectura en bloque por frame es suficiente y **no** provoca stutter.
- **No** usar `memory.read` del debugger para el HUD: pausa la CPU y fragmenta.
- Las conexiones WebSocket cortas y repetidas coinciden con cuelgues/crashes de
  PPSSPP 1.19.3 → un solo bus persistente (`tools/ppsspp_ws.py --serve`,
  Python nativo; sustituye al antiguo `t6padd.mjs` de Node).

---

## 6. Herramientas

| Herramienta | Función |
|---|---|
| `tools/psp_mem.py` | localiza la RAM PSP en el proceso y lee memoria |
| `tools/hud.py` | motor de medición + overlay (reloj compartido, PUNISH, `~`) |
| `tools/diag.py` | registro en vivo frame a frame + eventos con motivo de PUNISH |
| `tools/test_tracker.py` | autotest determinista del motor (sin PPSSPP) |
| `tools/t6scan.py` | snapshots, diffs, búsquedas, películas y series temporales |
| `tools/t6padd.mjs` | ⚠️ deprecado: lo sustituye `tools/ppsspp_ws.py` |
| `tools/ppsspp_ws.py` | bus persistente: una sola conexión WS + JSON en `127.0.0.1:8098` |
| `tools/pad.py` | cliente del bus (botones, peticiones, esperas) |
| `tools/scraper.py` | frame data pública -> `data/raw/` |
| `tools/mapper.py` | `raw` + `mappings` -> `data/compiled/` |
| `tools/lookup_engine.py` | búsqueda O(1) Move ID -> frame data |
| `tools/tracker_hybrid.py` | proyección de tabla + confirmación por memoria |
| `tools/learn.py` | modo `--learn` (IDs y estados nuevos) |
| `tools/game_states.py` | `data/states.json` (guardias) |
| `tools/exp_move.py` | captura una película de RAM mientras pulsa un botón |
| `tools/capture-window.ps1` | captura la ventana de PPSSPP |

Ejemplos:

```
python tools/t6scan.py snap idle
python tools/t6scan.py tri32 a b c
python tools/exp_move.py --name jab --button square
python tools/t6scan.py series jab 0x08C0793C --type u32
```

---

## 7. Preguntas abiertas

1. ¿El flag `+0x295` sube siempre en el frame accionable, o hay movimientos con
   blend especial (cancelaciones, stances)? Se ha visto **pulsar** en muestras
   sueltas (no solo en el frame accionable), por eso el desbloqueo se toma del
   primer flanco 0→1 tras el contacto y se descarta el contacto si un golpe lo
   interrumpe.
2. Semántica exacta de `+0x144` / `+0x168` / `+0x180` / `+0x158` (el tracker solo
   usa `+0x144`).
3. Valores de estado: `4178` = blockstun, `2114` = ataque **y** hitstun (no
   distinguen). Faltan knockdown y stagger (para separarlos de `HIT`).
4. ¿El contador de animación `+0x7C` se reinicia en todos los multi-hit, o solo
   en los que reinician la animación del defensor?
5. Comportamiento de los contadores durante el hitstop.
6. `0x08C0856C` / `0x08C08580`: cuentan `7 → 0` justo en el impacto. ¿Hitstop?
7. `startup` de los golpes 2+ de un multi-hit con el **mismo id** (el `14` de
   Bruce conecta varias veces sin cambiar `+0x144`): hoy se acumula desde el
   primer golpe. ¿Sirve el reinicio del contador del atacante (`+0x7C`) como
   inicio de segmento?
