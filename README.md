# 🥋 FRAMEMOD — Tracker de frame data en vivo para Tekken 6 (PSP)

Herramienta de telemetría para el **modo Practice de Tekken 6 (PSP)** emulado en
**PPSSPP**. Lee la memoria del juego y muestra **en pantalla**, sobre el propio
juego, el arranque (`startup`) del movimiento que acabas de hacer y la **ventaja
o desventaja** que produjo al conectar (bloqueo o golpe).

No usa tablas de movimientos: **mide** los tiempos con los contadores internos
del juego mientras juegas.

```
        ┌──────────────────────────────────────┐
        │ P1   1     i10    +1    BLOCK        │  ← tus golpes
        │ P2   14    i20   −12    BLOCK        │  ← el dummy atacando
        └──────────────────────────────────────┘
                  ↑ flota sobre PPSSPP, sin bordes
        ┌──────────────────────────────────────┐
        │ P1   1     i10    +8    PUNISH       │  ← castigo (rojo)
        └──────────────────────────────────────┘
```

---

## 1. Estado actual

| Pieza | Estado |
|---|---|
| Lectura de memoria a ~1 kHz | ✅ funciona |
| Detección de contacto (golpe / bloqueo) | ✅ funciona |
| `startup` (i10, i16, i12…) | ✅ **exacto**: el impacto se mide en el frame en que baja la vida |
| Ventaja | ✅ **exacta** (±1 real del juego, no del medidor) |
| Strings (`4,3`, `1,2`…) | ✅ un resultado por golpe |
| Overlay transparente sobre PPSSPP | ✅ canvas con sombra de texto y barra de color por jugador |
| Cierre del overlay al cerrar PPSSPP | ✅ el panel se apaga solo |
| Copia única del overlay | ✅ mutex con nombre: nunca se apilan dos paneles |
| Caso **HIT** (dummy en *Stand*) | ✅ validado (`1` → `i10`, `+8`, `HIT`) |
| Medición de la **defensa** (P2 ataca, P1 bloquea) | ✅ funciona (tracker espejo) |
| Aviso **PUNISH** | ✅ solo cuando el golpe **conecta** dentro de la ventana (jab `i10` sobre `−12`) |
| **Reloj único** para los dos jugadores | ✅ un solo contador de frame de juego (antes cada tracker llevaba el suyo y **derivaban**) |
| **Cualquier personaje** | ✅ medición universal; el nombre del golpe es opcional (`#id` por defecto) |
| **Ventaja adelantada** (`~`) | ✅ muestra la última medición del mismo golpe mientras llega la nueva |
| Panel de **doble cara** (P1 y P2 a la vez) | ✅ dos filas en el overlay |
| Knockdown / stagger | ⚠️ se marca como `HIT`, sin distinguir tipos |
| Modo Practice únicamente | ✅ por diseño |

> El modelo antiguo (fin del estado de animación) está **descartado**: la
> animación sigue reproduciéndose ~12 frames después de que el personaje ya
> puede actuar. La medición actual usa los flags internos de accionabilidad
> (`+0x294` / `+0x295`), que sí marcan el frame exacto.

**Panel de doble cara (implementado):** el overlay muestra siempre **dos filas**,
una por personaje. Cuando P2 ataca y tú bloqueas, el dummy enseña su `startup` y
su desventaja (`P2 −12`) y tu fila enseña el lado contrario (`P1 +12`). Si le
metes un golpe antes de que se recupere, su fila cambia a **PUNISH**.

---

## 2. Requisitos

- **PPSSPP 1.19.3** (Windows x64) con el *debugger* activado (WebSocket en el
  puerto **8099**). El HUD **no** usa el debugger para leer memoria, pero el
  daemon de teclado sí lo necesita.
- **Tekken 6 (Europe)** `ULES01376` (también probado el patrón de direcciones con
  `ULES01523`/`ULUS10466`; ver §5).
- **Python 3.11+** (solo biblioteca estándar: `ctypes`, `tkinter`).
- **Node.js** (solo para el daemon de entrada `tools/t6padd.mjs`).
- Un **savestate dentro de Practice** para no navegar menús cada vez.

Arranque recomendado:

```powershell
& "C:\Program Files\PPSSPP\PPSSPPWindows64.exe" `
  --state="C:\Users\Carlos\Documents\PPSSPP\PSP\PPSSPP_STATE\ULES01376_1.00_0.ppst" `
  "C:\Users\Carlos\Documents\PPSSPP\PSP\GAME\Tekken 6 (Europe) (En,Ja,Fr,De,Es,It,Ko,Ru).iso"
```

---

## 3. Uso

```powershell
# 1) HUD en pantalla (no necesita el daemon)
python tools/hud.py

# variantes
python tools/hud.py --debug                 # además imprime en la consola
python tools/hud.py --x 60 --y 90           # posición fija
python tools/hud.py --no-click-through      # permite clics en la ventana
python tools/hud.py --p1-char bryan --p2-char bruce   # nombres de golpe
python tools/hud.py --no-predict            # sin el valor adelantado (~)

# 2) tracker en consola (mismo motor, sin ventana)
python tools/live.py
```

- La ventana se coloca sola sobre PPSSPP (sigue al emulador si lo mueves).
- Es **transparente** (solo se ve el texto) y **click-through**: no roba clics.
- Cada número lleva una **sombra negra** de 2 px para que se lea igual sobre el
  suelo del escenario que sobre el cielo, y cada fila tiene una **barra de
  color** (P1 verde azulado, P2 naranja).
- **Se cierra sola** cuando cierras PPSSPP (el watchdog ve que el proceso ya no
  está y destruye la ventana). No deja un panel huérfano congelado; si PPSSPP
  no aparece en 20 s, también se cierra.
- Solo puede correr **una copia**: la segunda sale con `overlay already running`
  (mutex de Windows). Antes, dos instancias apiladas duplicaban el texto.
- En el impacto aparece el `startup` y, si ese golpe ya se midió antes, su
  ventaja adelantada con `~` (p. ej. `~−12`). La ventaja **exacta** se conoce
  cuando se libera el segundo jugador (~0,5 s después, 30 frames) y sustituye
  al `~`. Se borra a los 5 s.
- Colores: verde = ventaja, blanco = neutro, naranja = −1…−9, rojo = −10 o peor,
  azul = golpe limpio en ventaja.

Para inyectar botones desde fuera (pruebas, no necesario para jugar):

```powershell
python tools/pad.py square --frames 2
python tools/pad.py --combo square+triangle
```

### 3.1 Cualquier personaje

La medición **no depende del personaje**: el `startup` y la ventaja salen de los
contadores internos del juego, no de una tabla de movimientos. Lo único que
cambia entre personajes es el **nombre** del golpe, porque el id de movimiento
es propio de cada uno.

- Por defecto el HUD muestra el id crudo (`#7`, `#21`, `#12`). Sirve igual para
  Bryan, Bruce, Kazuya o cualquier otro, y **nunca etiqueta mal** un golpe.
- Si quieres nombres, crea `tools/notation.json` con un bloque por personaje:

```json
{
  "bruce": { "7": "1", "8": "2", "11": "3" }
}
```

y arranca con `--p1-char bruce --p2-char bruce`. Los ids se ven en el propio
HUD (`#7`) o en la `LISTA DE COMANDOS` del modo Practice.
- `--p1-char bryan` ya viene incluido (`1`, `2`, `3`, `4`, `1+2`, `3+4`, `4,3`).
- No hay que reiniciar ni elegir personaje antes: el HUD lee la estructura del
  jugador, no una lista de movimientos. P1 y P2 pueden ser personajes distintos
  y cada uno lleva su propia tabla.

---

## 4. Cómo funciona

### 4.1 Lectura de memoria

El HUD abre el proceso de PPSSPP y lee **directamente** con
`ReadProcessMemory` una ventana de 4.7 KB que cubre las dos estructuras de
jugador. Una lectura cuesta ~0.04 ms, así que se muestrea a ~1 kHz (unas 16
muestras por frame de juego).

> ⚠️ Nunca usar `memory.read` del debugger para el HUD: pausa la CPU y produce
> tirones. El debugger solo se usa para enviar botones.

PPSSPP mapea la RAM de PSP en su propio espacio de direcciones; `tools/psp_mem.py`
localiza esa vista recorriendo las regiones del proceso (la dirección cambia en
cada arranque del emulador).

### 4.2 Mapa de memoria verificado

| Campo | Dirección / offset | Tipo | Uso |
|---|---|---|---|
| Base jugador 1 | `0x08C078C0` | — | — |
| Base jugador 2 | `0x08C081F0` | — | — |
| Separación entre jugadores | `0x930` | — | — |
| Vida | `+0x002` | `u16` | distinguir `HIT` de `BLOCK` |
| **Contador de animación** | `+0x07C` | `u32` | +1 por frame, **reinicia a 1** al cambiar de animación |
| **Estado** | `+0x0AC` | `u16` | detectar el contacto (ver abajo) |
| **ID de movimiento** | `+0x144` | `u32` | `1`, `2`, `3`, `4`, `1+2`… |
| **Libre / bloqueado** | `+0x294` | `u8` | `1` = puede actuar, `0` = bloqueado |
| **Cola de recuperación** | `+0x295` | `u8` | sube a `1` **exactamente** en el frame accionable |
| Pad emulado | `0x08BFE4AC` | `u16` | pulsaciones reales (bits PSP) |

Valores de estado observados:

| Valor | Significado |
|---|---|
| `6482` | de pie (P1) |
| `6466` | de pie (dummy / P2) |
| `14633` | agachado |
| `2114` | ataque de pie **y** hitstun |
| `10273` | ataque agachado |
| `4178` | **guardia / blockstun** |

> `+0x294` **parpadea a 0** en reposo un frame suelto, así que nunca se usa solo
> como señal de impacto. El impacto se detecta con el estado del defensor.

**`HIT` vs `BLOCK`:** las dos señales son inequívocas y llegan en el frame exacto
del impacto:

- **`HIT`** = la vida del defensor baja en ese frame (`hp2` menor que en la
  muestra anterior). El estado de reacción llega un frame después; la vida, no.
- **`BLOCK`** = el defensor entra en guardia (`4178`) en ese frame, o reinicia su
  contador de animación estando ya en `4178` (segundo golpe de un string).

> ⚠️ `estado ≠ idle` **no** sirve como señal: `2114` es ataque *y* hitstun, y el
> juego escribe el estado unas muestras antes que el ID de movimiento. Cuando el
> defensor empezaba su propio ataque, su cambio de estado se colaba como
> "reacción" y corrompía los impactos (el número raro que salía tras cada jab).

### 4.3 Medición

```
inicio del segmento        ← el ID de movimiento cambia a un valor nuevo
        │
        │  startup = contacto − inicio + 1
        ▼
     CONTACTO              ← la vida de P2 baja (golpe) o P2 entra en guardia
        │                     `4178` (bloqueo): frame exacto del impacto
        │
        ├── P1 desbloqueo  = +0x295 de P1 sube a 1
        └── P2 desbloqueo  = +0x295 de P2 sube a 1

                ventaja = P2 desbloqueo − P1 desbloqueo
```

**El impacto se mide en el frame de la vida, no en el de la reacción.** Al
recibir un golpe, el juego aplica el estado de reacción del defensor **un frame
después** de que el hitbox conecta. Medir el contacto en ese estado sumaba un
frame a todos los golpes: el jab de Bryan salía `i11` en vez de `i10`. La traza
frame a frame de un jab aislado lo deja claro:

| frame | `+0x7C` P1 | `+0x144` P1 | vida P2 | estado P2 |
|---|---|---|---|---|
| 3 | 1 | 7 | 180 | 6466 |
| 12 | 10 | 7 | **172** | 6466 |
| 13 | 11 | 7 | 172 | **2114** |

El movimiento empieza en el frame 3, la vida baja en el 12 y P2 reacciona en el
13 → `12 − 3 + 1 = 10` (`i10`). Por eso el motor detecta el golpe **en la bajada
de vida**, no en la reacción. En bloqueo no hay bajada de vida: la guardia
(`4178`) y el reinicio del contador del defensor aparecen en el **mismo frame**
(verificado frame a frame), así que ahí el impacto es el frame del bloqueo.

Por qué este modelo y no el anterior:

1. **El fin de la animación no es la ventana de acciones.** El personaje puede
   moverse ~12 frames antes de que termine su animación. Por eso el modelo viejo
   (`fin de estado`) daba ventajas demasiado positivas.
2. **`+0x295` marca el frame exacto** en que el jugador puede actuar, en los dos
   jugadores. La ventaja no depende de cuándo se detecte el impacto: es una
   diferencia entre dos sucesos absolutos.
3. **El impacto se detecta con el defensor**, no con el atacante: su vida baja
   (golpe) o entra en guardia (`4178`, bloqueo) — o reinicia su contador de
   animación estando ya en `4178` (segundo golpe de un string). `+0x294` cae 2
   frames más tarde y a veces parpadea solo.
4. **El ID de movimiento parpadea** (p. ej. `15 → 16 → 12` en el mismo frame),
   así que se filtra: un ID solo se acepta si sobrevive a un segundo muestreo o
   cruza al frame siguiente. El segmento empieza en el frame en que apareció.

### 4.4 Reloj compartido (por qué fallaba el PUNISH)

Los dos trackers (P1 atacando y P2 atacando) **comparten un único reloj de
frame de juego**, que avanza cuando cambia el contador `+0x7C` de P1.

Antes cada tracker contaba con el contador de **su** jugador. Ese contador
**repite valores** de vez en cuando (no siempre incrementa: en un arranque de
animación salta de `10` a `12`, y a veces se queda igual una muestra), así que
los dos relojes se iban separando: en una sesión real de 10 minutos la deriva
medida era de **~17 frames**. La comprobación de castigo comparaba un frame de
P1 con un frame de P2, es decir, dos relojes distintos: la ventana salía
desplazada y el `PUNISH` no aparecía aunque el golpe entrara.

Con un solo reloj, `u_p` (frame en que P1 queda libre), `u_d` (frame en que el
dummy queda libre) y el impacto del castigo son directamente comparables:

```
ventana de castigo = |u_p − u_d|          (la desventaja del dummy)
PUNISH  ⇔  el golpe conecta (vida)  y  start ≥ u_p  y  impact ≤ u_d + 1
           y  startup ≤ ventana + 1
```

### 4.5 Ventaja adelantada (`~`)

La ventaja **exacta** solo se puede conocer cuando se libera el segundo
jugador, y eso ocurre ~30 frames (0,5 s) después del impacto: es el tiempo de
recuperación que se está midiendo. Para que el panel no parezca congelado, en
el impacto se muestra la **última ventaja medida para ese mismo golpe y tipo**
(`~−12`), y en cuanto llega la medición real se sustituye por el número exacto.
La caché es por `(id de movimiento, HIT/BLOCK)` y caduca a los 2 minutos.
`--no-predict` la desactiva.

### 4.6 Strings

En un string el juego cambia el ID **en el mismo frame del primer contacto**
(Bryan `4,3`: el ID pasa a `12` justo cuando el `4` conecta). El motor:

- asigna el golpe a la **fase anterior** si el cambio de ID ocurre en el contacto;
- emite **un resultado por contacto**, con la etiqueta del segmento (`4` y luego
  `4,3`);
- si un contacto nuevo interrumpe la medición anterior, la anterior se muestra
  sin ventaja (`—`) y la nueva pasa a ser la buena.

Ejemplo real de `4,3` medido en vivo:

```
impact 4    i12   —   BLOCK      ← primer golpe
impact 4,3  i24   —   BLOCK      ← segundo golpe (string)
final  4,3  i24  −7   BLOCK      ← ventaja del string
```

### 4.7 Overlay

`tkinter` con `-transparentcolor` + `WS_EX_LAYERED | WS_EX_TRANSPARENT`
(click-through). Se reposiciona cada 700 ms sobre el área cliente de PPSSPP.

- **DPI awareness** (`shcore.SetProcessDpiAwareness(2)`, con
  `SetProcessDPIAware` de reserva) al arrancar. Sin esto, en un monitor al
  150 % Tk coloca la ventana en píxeles físicos pero `GetWindowRect` /
  `ClientToScreen` devuelven coordenadas virtuales: el panel cae **1,5×
  desplazado** y acaba encima del cronómetro.
- **Un solo `Canvas`** de 620×96 con las dos filas (P1/P2) y columnas fijas
  (lado, movimiento, startup, valor, tipo). Dibujar en canvas (en vez de
  `Label` empaquetados) permite posicionar cada texto al píxel y añadir la
  sombra.
- **Sombra de texto:** cada texto se pinta dos veces, una copia negra a
  `+2 px`. Es lo que hace legible el panel sobre cualquier fondo sin perder la
  transparencia (no hay caja opaca).
- **Barra de color por jugador** a la izquierda de cada fila (P1 verde
  azulado, P2 naranja) y números grandes en `Consolas` 28.
- **Mutex con nombre** (`Local\T6FramesHud`): si ya hay un overlay, el segundo
  proceso sale sin dibujar. Sin esto, dos HUD apilados duplicaban el texto
  (`ii1101`).
- **Watchdog:** si la lectura falla y el PID que se estaba leyendo ya no
  existe, el hilo marca `dead` y la ventana se destruye sola.
- Se coloca en `x = gx + (gw − w)/2`, `y = gy + 0.19 · gh`: centrado y por
  debajo de las barras de vida y del cronómetro.

### 4.8 Menú de Practice (ES) — configuración del dummy

El menú de práctica se abre con **START** y **el juego se congela mientras está
abierto** (el contador `+0x7C` deja de avanzar; no es un cuelgue).

| Opción | Qué hace |
|---|---|
| `ELEGIR MODO` | modo de práctica. `ENTRENAMIENTO DEFENSIVO` hace que la CPU ataque usando la `LISTA DE COMANDOS`. |
| `LISTA DE COMANDOS` | movimientos que usará la CPU en entrenamiento defensivo (hay que elegir ≥2). |
| `MUÑECO 1` | comportamiento del dummy: `GUARDIA TOTAL` (bloquea todo), `DE PIE` (quieto, **sin guardia**, ideal para probar golpes limpios), `EN GUARDIA DE PIE` (de pie pero bloqueando). Se cambia con ←/→. |
| `MUÑECO 2` | qué hace al caer (`LEVANTARSE`). |
| `CONTRAATAQUES` | `NO`/`SÍ` es el **counter hit** de la práctica; **no** hace que el dummy ataque. |
| `RESTAB. POSICIÓN` | reinicia la posición tras cada intercambio. |
| `ANÁLISIS DE GOLPES` | muestra daño/ventana en pantalla. |

En entrenamiento defensivo aparece el aviso **`SELECT + R EMPEZAR`** (y `PARAR`
mientras corre). El drill se maneja desde fuera con:

```powershell
python tools/pad.py --combo select+rtrigger
```

`rtrigger` es el nombre del botón R en el daemon (`r`/`l` no existen: son
`rtrigger`/`ltrigger`). El mismo comando lo para. El HUD no lo necesita: solo lo
usan los scripts de prueba.

---

## 5. Validación

Contra frame data oficial de **Bryan** (Tekken 6, <https://tekkendocs.com/t6/bryan>),
con el dummy en *Guard All* (la última fila, en *Stand*):

| Movimiento | `startup` medido | oficial | Ventaja medida | oficial |
|---|---|---|---|---|
| `1` | **i10** | i10 | **+1** | +1 |
| `2` | **i10** | i10 | **+1** / 0 | +1 |
| `3` | **i16** | i16 | **−4** | −4 |
| `4` | **i12** | i12 | **−5** / −6 | −5 |
| `1+2` | **i14** | i14 | **−10** | −10 |
| `3+4` | **i18** | i18 | **−8** / −7 | −8 |
| `4,3` (2.º golpe) | **i24** | — | **−7** | −7 |
| `1` **en golpe** (dummy *Stand*) | **i10** | i10 | **+8** | +8 |

El caso **HIT** se validó con el dummy en `DE PIE` (sin guardia): la ventaja
cambia de `+1` (bloqueo) a `+8` (golpe) y la etiqueta pasa a `HIT`.

El `startup` coincide en **todos** los casos tras medir el impacto en el frame
en que baja la vida. La ventaja conserva el ±1 que aparece **en el juego**:
según qué frame activo conecta y el *hitstop*, la ventaja real cambia un frame.
Una tabla estática no puede mostrar eso; el HUD sí.

### 5.1 Defensa y PUNISH (dummy P2 atacando)

Medido en **entrenamiento defensivo** con la lista de comandos del dummy
(Bruce: sus ids son propios y aún no tienen nombre):

| lista del dummy (ids) | `startup` | ventaja en bloqueo |
|---|---|---|
| `7` | i11 | — |
| `10` | i19 | — |
| `12` | i20 | **−15** |
| `15, 18` | i15 / i16 | **−11** |
| `7, 8, 7, 14` | i11 / i13 / i20 / i20 | **−12** |
| `11, 21` | i14 / i24 | **−6** |

El savestate actual usa la lista simple **`7`, `10`, `12`** (dos golpes sueltos
y un remate de `−15`, el mejor caso para probar castigos).

El **PUNISH** se validó soltando *back* y pulsando `1` en cuanto P1 queda libre:
el HUD muestra `P1 1 i10 +8 HIT PUNISH` sobre el ender de `−15`. El mismo `1`
sobre un string de `−6` sale sin `PUNISH`, porque el dummy ya puede actuar
cuando el jab llega: la etiqueta no es "he acertado", es "he acertado **antes**
de que se recupere".

**Regla exacta:** el `PUNISH` se marca **solo cuando el golpe conecta** (la vida
del dummy baja). Se toman del **último bloqueo real** los dos frames de
liberación (`u_p` = P1 libre, `u_d` = dummy libre) y se exige:

1. el golpe de P1 **empezó** después de que P1 estuviera libre (`start ≥ u_p`);
2. **conectó** mientras el dummy seguía bloqueado (`impact ≤ u_d + 1`, con un
   frame de tolerancia);
3. su `startup` **cabe** en la desventaja del dummy (`startup ≤ |u_p − u_d| + 1`).

Así, un `1` (i10) sobre un `−12` sale `PUNISH`; el mismo `1` sobre un `−6` no,
porque el dummy ya puede actuar cuando el jab llega: la etiqueta no es "he
acertado", es "he acertado **antes** de que se recupere". Un golpe que entra
tarde (fuera de la ventana) tampoco se marca. No hay aviso previo de la
ventana: la ventaja solo se conoce en el frame en que el dummy se libera, así
que cualquier "PUNISH" adelantado aparecía sobre la fila del bloqueo (la del
dummy), que es exactamente lo que no debe pasar.

Cuando el castigo entra, la recuperación del dummy queda cortada, así que su
propia ventaja ya no es el valor del bloqueo: esa fila muestra `—` en vez de un
número contaminado.

---

## 6. Herramientas

| Herramienta | Función |
|---|---|
| `tools/hud.py` | **el tracker en pantalla** (motor de medición + overlay) |
| `tools/live.py` | el mismo motor en consola (sin ventana) |
| `tools/diag.py` | registro en vivo frame a frame + eventos (depuración del castigo) |
| `tools/test_tracker.py` | autotest determinista del motor, sin PPSSPP (`python tools/test_tracker.py`) |
| `tools/notation.json` | nombres de golpe por personaje (opcional) |
| `tools/replay.py` | reproduce una grabación y muestra los eventos del tracker (depuración offline) |
| `tools/psp_mem.py` | localiza la RAM de PSP dentro del proceso y lee memoria |
| `tools/inputrec.py` | grabador frame-accurate de pulsaciones + memoria (`--bin` para replay) |
| `tools/advcheck.py` | calcula la ventaja desde `+0x294` / `+0x295` |
| `tools/validate.py` | batería automática contra la frame data oficial |
| `tools/padfind.py` | localiza la estructura del pad por diferencias de snapshot |
| `tools/fieldscan.py` | escáner de campos de la estructura del jugador |
| `tools/t6scan.py` | snapshots, películas de RAM, búsquedas, series temporales |
| `tools/timeline.py` | volcado frame a frame de una película |
| `tools/t6padd.mjs` | daemon de entrada (WebSocket persistente + bus TCP 8098) |
| `tools/pad.py` | cliente del daemon (`press`, `--combo`, `raw`) |
| `tools/capture-window.ps1` | captura la ventana de PPSSPP |

Ejemplos:

```powershell
python tools/live.py --demo --seconds 10
python tools/replay.py s43a --mids
python tools/inputrec.py --name jab --bin --press square --at 0.7 --seconds 2
```

Las capturas quedan en `.cache/in/`.

---

## 7. Limitaciones conocidas

1. **`startup` en multi-hit del mismo id**: si el golpe siguiente conserva el
   id (p. ej. el `14` de Bruce conecta varias veces), no hay segmento nuevo y
   los golpes 2+ muestran el `startup` acumulado (`i51`, `i52`). El primero es
   correcto y la ventaja del último también.
2. **Bloqueo con estados exóticos**: la detección de bloqueo usa el estado
   `4178` (guardia de pie, el del entrenamiento). Un bloqueo agachado con otro
   valor de estado no se reconocería; con los golpes de la lista actual no pasa.
3. **±1 frame real**: no es ruido del medidor. Depende del frame activo que
   conecta y del hitstop; aparece también en la frame data oficial como rangos.
4. **Strings muy rápidos**: si dos golpes ocurren dentro de la misma animación
   del defensor (sin que su estado cambie), se detecta por el reinicio del
   contador de animación. Funciona en `4,3` y en los strings del dummy; los
   multi-hit del **mismo id** caen en el punto 1.
5. **KD / stagger**: se detectan como `HIT` (la vida baja) pero no se distingue
   knockdown, roll o stagger.
6. **`PUNISH` requiere que el dummy haya bloqueado**: si su golpe te conecta
   (estás en hitstun), no hay ventana que enseñar.
7. **Nombres de golpe**: los ids de movimiento son **por personaje**. Por
   defecto se muestra el id crudo (`#7`, `#21`), que es correcto para
   cualquiera; los nombres son opcionales (§3.1).
8. **Solo Practice**: en versus/arcade las direcciones y el flujo de estados
   pueden cambiar.

### 7.1 Defensa + PUNISH (implementado)

```
P2 (dummy) ataca y P1 bloquea
   ├─ contacto: el defensor sale del idle o reinicia su contador de animación
   ├─ U1 = unlock de P1 (+0x295) → puede actuar
   └─ U2 = unlock de P2 (+0x295) → fin de la recuperación del dummy

ventaja del dummy = U1 − U2   (negativa si es castigable)
```

Implementación:

1. `Reader.sample()` lee también `mid2`, `s1`, `hp1` y `hp2`.
2. `mirror(s)` invierte los papeles y alimenta un **segundo `Tracker`**: el
   mismo motor mide a P2 atacando sin duplicar lógica.
3. Los dos trackers reciben **el mismo frame de juego** en cada muestra
   (`TrackerThread.feed`), así que sus eventos se pueden comparar directamente.
   Antes cada uno contaba con el contador de su jugador y derivaban.
4. En el contacto de P1, si el golpe **conecta** y cumple la ventana del último
   bloqueo (ver §5.1), es **PUNISH**. El aviso se emite con el impacto, nunca
   antes.
5. El `final` del tracker espejo se descarta (`—`) si ese contacto fue
   interrumpido por el golpe de P1: su recuperación quedó cortada y el número
   medido ya no es el bloqueo (antes publicaba basura como `−55`).
6. El impacto de un golpe se mide en el frame de la **bajada de vida** (la
   reacción llega un frame después); sin eso, todo castigo salía `i+1`.

**Filtro clave**: el estado `2114` es a la vez ataque y *hitstun*, así que no
sirve para distinguir. El contacto exige una señal inequívoca: **vida que baja**
(golpe) o **guardia `4178`** (bloqueo). Antes se filtraba con "el defensor tiene
move id `≠ 0` ⇒ está atacando", pero el juego escribe el estado varias muestras
antes que el id: al empezar *tu* movimiento, el tracker espejo veía `estado=2114`
con `id=0` y lo tomaba por una reacción del dummy (impactos absurdos y ventajas
como `+45`/`+82`). Con la vida y la guardia como únicas señales, ese caso
desaparece.

### Próximos pasos

1. Mapear los ids de Bruce para poner nombre a sus golpes (`tools/notation.json`).
2. Corregir el `startup` de los golpes 2+ de un multi-hit con el mismo id
   (usar el reinicio del contador del atacante como inicio de segmento).
3. Emitir un `final` por golpe de string (hoy solo el último lleva valor).
4. Detectar el personaje desde la memoria para cargar su tabla de nombres sola.
5. Confirmar en vivo el `PUNISH` con el reloj compartido (validado en
   simulación: `tools/test_tracker.py`).

---

## 8. Avisos

- Solo lectura de memoria del emulador: no se modifica el juego.
- Herramienta pensada para **Practice offline**. No usar en partidas online ni
  para automatizar el juego de otras personas.
- PPSSPP 1.19.3 se ha cerrado solo varias veces en esta sesión (acceso inválido
  en `0x309fe0`), normalmente al guardar estado. Si pasa, relanzar con
  `--state=...` recupera la partida al instante.
- **No inundar el debugger:** enviar `input.buttons.send` en bucle (p. ej. cada
  40 ms para "mantener" un botón) **congela la emulación**, porque el debugger
  se procesa en el hilo principal. `buttons.send` mantiene el estado hasta que
  se cambia: enviarlo una vez y liberar con un mapa explícito.
- **No usar `PrintWindow`** para capturar la ventana: con el render D3D de
  PPSSPP coincide con cuelgues. Usar `CopyFromScreen` / `ImageGrab` subiendo la
  ventana a *topmost* un instante (`SetWindowPos` `HWND_TOPMOST` → capturar →
  `HWND_NOTOPMOST`).
- Con el **menú de Practice abierto el juego se pausa** (los contadores de
  animación dejan de avanzar). No confundirlo con un cuelgue.
- `backups/ppsspp.ini.original` guarda el `ppsspp.ini` de fábrica (el debugger
  está activado en el perfil actual).

---

## 9. Documentación técnica

- `tasks/01-ppsspp-debugger.md` — protocolo del debugger de PPSSPP.
- `tasks/02-tekken6-memory.md` — primera exploración de memoria (⚠️ contiene un
  error de `+0x400000` en las direcciones; corregido en el 04).
- `tasks/03-lua-overlay.md` — diseño del overlay en LuaJIT (alternativa no usada).
- `tasks/04-live-map.md` — **mapa de memoria y modelo de medición vigentes**.

---

## 10. Repositorio

<https://github.com/Madvillainism/MokuMod-T6BR>

```powershell
git clone https://github.com/Madvillainism/MokuMod-T6BR.git
cd MokuMod-T6BR
python tools/hud.py --debug
```

No hay dependencias de Python fuera de la biblioteca estándar. Para inyectar
botones (solo pruebas) hace falta Node.js:

```powershell
python tools/pad.py --daemon
```

El repo no incluye `.cache/` (grabaciones, savestates y capturas de trabajo),
`captures/` ni los binarios de LuaJIT del intento de overlay en Lua.
