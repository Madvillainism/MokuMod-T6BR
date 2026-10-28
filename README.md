# 🥋 FRAMEMOD — Tracker de frame data en vivo para Tekken 6 (PSP)

Herramienta de telemetría para el **modo Practice de Tekken 6 (PSP)** emulado en
**PPSSPP**. Lee la memoria del juego y muestra **en pantalla, sobre el propio
juego**, el arranque (`startup`) del movimiento que acabas de hacer y la
**ventaja o desventaja** que produjo al conectar (bloqueo o golpe).

No es un overlay de datos copiados de una web: **mide en vivo** con los
contadores internos del juego mientras juegas. Opcionalmente puede usar una
**base de datos local** (`data/`) para enseñar el dato teórico del movimiento en
el mismo frame en que empieza, antes de que exista ningún impacto. La medición de
memoria siempre tiene la última palabra: el teórico se marca con `~` y desaparece
en cuanto el golpe conecta.

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

## Índice

1. [Qué mide](#1-qué-mide)
2. [Requisitos](#2-requisitos)
3. [Instalación](#3-instalación)
4. [Primeros pasos](#4-primeros-pasos)
5. [Preparar el modo Practice](#5-preparar-el-modo-practice)
6. [Uso del HUD](#6-uso-del-hud)
7. [Cómo leer el panel](#7-cómo-leer-el-panel)
8. [Golpes bajos y agachado](#8-golpes-bajos-y-agachado)
9. [Tabla de frame data opcional](#9-tabla-de-frame-data-opcional)
10. [Cómo funciona por dentro](#10-cómo-funciona-por-dentro)
11. [Herramientas](#11-herramientas)
12. [Problemas frecuentes](#12-problemas-frecuentes)
13. [Limitaciones](#13-limitaciones)
14. [Avisos](#14-avisos)
15. [Empaquetado (sin Python instalado)](#15-empaquetado-sin-python-instalado)

---

## 1. Qué mide

| Dato | Qué significa |
|---|---|
| **startup** (`i10`, `i16`, `i12`…) | Frames desde que empieza el movimiento hasta que conecta. Se mide con el contador de animación del atacante y se ancla en el frame exacto del impacto. |
| **ventaja** (`+1`, `−12`…) | Frames que ganas o pierdes cuando termina el contacto. Positivo = puedes actuar antes que el rival. |
| **HIT** | El golpe bajó la vida del rival. |
| **BLOCK** | El rival lo bloqueó (incluye los golpes bajos, que se bloquean agachado). |
| **PUNISH** | Tu golpe conectó **dentro** de la ventana de castigo que dejó el golpe bloqueado del rival. |
| **`~`** | Valor adelantado: la última vez que mediste ese mismo golpe. Se sustituye por el número exacto en cuanto se libera el rival. |
| **Strings** (`4,3`, `1,2`…) | Un resultado por golpe, medido desde el inicio del string. |

Se mide **siempre en las dos direcciones**: cuando tú atacas y el dummy bloquea o
recibe, y cuando el dummy ataca y tú bloqueas (el tracker espejo). Por eso el
panel tiene dos filas.

---

## 2. Requisitos

- **Windows 10/11 de 64 bits.**
- **PPSSPP** (probado con 1.19.x, versión **64 bits**).
- **Tekken 6 (PSP)** en un **ISO/CSO que hayas obtenido legalmente** (tu propia
  copia del UMD). Esta herramienta no incluye ni distribuye el juego.
- **Python 3.11 o superior** — solo biblioteca estándar: no hay que instalar
  paquetes (`pip` no se usa para nada).
- Una partida en **modo Practice**. El HUD está pensado para práctica offline.

> El HUD **no** necesita el *debugger* de PPSSPP: lee la memoria del proceso con
> la API de Windows. El debugger solo hace falta si quieres usar el bus de
> entrada (`tools/pad.py`), que es opcional y sirve para automatizar pulsaciones
> en pruebas.

La lectura de memoria localiza la base de la RAM de PSP dentro del proceso del
emulador, así que no depende de una ruta ni de una versión concreta del ISO. Se
ha validado con las ediciones europea y americana; con otras regiones debería
funcionar igual siempre que la estructura de los jugadores sea la misma.

---

## 3. Instalación

```powershell
git clone https://github.com/Madvillainism/MokuMod-T6BR.git
cd MokuMod-T6BR
python tools/hud.py
```

Si no usas git: descarga el ZIP del repositorio y descomprímelo en cualquier
carpeta.

Comprobación rápida sin PPSSPP (reproduce una grabación de ejemplo sobre el
overlay real):

```powershell
python tools/hud.py --replay val_1_0
```

---

## 4. Primeros pasos

1. Arranca **PPSSPP** y carga **Tekken 6**.
2. Entra en **Practice** (menú principal → *Practice*).
3. Deja a PPSSPP en primer plano y ejecuta:

   ```powershell
   python tools/hud.py --debug
   ```

   `--debug` imprime además cada evento en la consola; sirve para comprobar que
   todo va bien la primera vez.
4. Haz un golpe. En el frame en que empieza verás `P1 <golpe>` y, al conectar, el
   `startup` y la ventaja.
5. El panel se coloca solo sobre PPSSPP y **se cierra solo** cuando cierras el
   emulador.

Solo puede haber **una copia** del HUD: la segunda sale con
`overlay already running`.

---

## 5. Preparar el modo Practice

El menú de práctica se abre con **START**. Mientras está abierto el juego se
congela (los contadores dejan de avanzar): no es un cuelgue.

| Opción | Para qué sirve |
|---|---|
| `MUÑECO 1` | Comportamiento del dummy. Se cambia con ←/→. |
| `MUÑECO 1 = GUARDIA TOTAL` | Bloquea todo: ideal para medir **ventaja en bloqueo** y castigos. |
| `MUÑECO 1 = DE PIE` | Quieto y **sin guardia**: ideal para medir **golpes limpios (HIT)**. |
| `MUÑECO 1 = EN GUARDIA DE PIE` | De pie, bloqueando solo lo que se bloquea de pie. |
| `MUÑECO 2` | Qué hace el dummy al caer (`LEVANTARSE`). |
| `RESTAB. POSICIÓN` | Reinicia la posición tras cada intercambio. Déjalo en `NO` si quieres medir a distintas distancias. |
| `CONTRAATAQUES` | Es el *counter hit* de la práctica; **no** hace que el dummy ataque. |
| `ELEGIR MODO` | `ENTRENAMIENTO DEFENSIVO` hace que la CPU ataque usando la `LISTA DE COMANDOS` (para probar tu defensa y el aviso PUNISH). |

**Regla de oro:** para medir un golpe en concreto pon el dummy en `DE PIE` (para
ver el HIT) y en `GUARDIA TOTAL` (para ver el BLOCK). Si lo dejas siempre en
guardia, todo lo que hagas saldrá como bloqueo y nunca verás el daño.

---

## 6. Uso del HUD

```powershell
# HUD en pantalla
python tools/hud.py

# variantes
python tools/hud.py --debug                            # además imprime en consola
python tools/hud.py --x 60 --y 90                      # posición fija
python tools/hud.py --no-click-through                 # permite clics en la ventana
python tools/hud.py --p1-char bryan --p2-char bruce    # tabla + nombres de golpe
python tools/hud.py --no-predict                       # sin el valor adelantado (~)
python tools/hud.py --settings                         # panel de ajustes (también con F8)
python tools/hud.py --no-table                         # solo memoria (sin proyección teórica)
python tools/hud.py --p1-char bryan --learn            # aprender IDs nuevos por consola

# demo del overlay sin PPSSPP (reproduce una grabación de .cache/in/)
python tools/hud.py --replay val_1_0        # velocidad real, se cierra solo
python tools/hud.py --replay s43a --speed 2 # doble de velocidad

# tracker en consola (mismo motor, sin ventana)
python tools/live.py
```

- La ventana se coloca sola sobre PPSSPP y **sigue al emulador** si lo mueves.
- Es **transparente** (solo se ve el texto) y **click-through**: no roba clics.
- Cada número lleva una **sombra negra** para que se lea sobre cualquier fondo, y
  cada fila tiene una **barra de color** (P1 verde azulado, P2 naranja).
- **Panel de ajustes (F8)**: mueve el HUD, cambia opacidad y tamaño de fuente.
  Todo se guarda en `config/hud.json`.
- `--p1-char` / `--p2-char` se **recuerdan** entre ejecuciones: basta indicarlos
  una vez. `--p1-char none` los borra.

---

## 7. Cómo leer el panel

```
P1   3+4   i18   −8   BLOCK     ← golpe de P1, bloqueado
P2   1     i10   +8   PUNISH    ← el dummy te castigó
```

- **Colores**: verde = ventaja, blanco = neutro, naranja = `−1`…`−9`,
  rojo = `−10` o peor, azul = golpe limpio en ventaja.
- **`PUNISH`** aparece solo si tu golpe **conecta** dentro de la ventana que dejó
  el bloqueo previo del rival. Si llegas tarde, el número se publica igual pero
  sin la etiqueta.
- **`KND` / etiquetas cualitativas**: si el golpe derriba, no se publica un
  número (el tiempo de levantarse no es una ventaja real): verás `i18 — KND`.
- El panel **borra** el resultado a los 5 segundos sin actividad.

---

## 8. Golpes bajos y agachado

Los golpes bajos son un caso aparte y el HUD los trata como tales:

- **El bloqueo de un bajo no es el mismo estado que el de pie.** El juego usa un
  estado interno distinto (`12329`) cuando un golpe bajo queda bloqueado, frente
  al `4178` del bloqueo normal. Ambos están en `data/states.json`.
- **El `type` de la tabla decide qué guardia se espera.** Cada movimiento del
  catálogo trae su nivel de golpe (`l` = low, `m` = medio, `h` = alto,
  `sm` = medio especial). Un `l` solo se puede bloquear agachado, así que el HUD
  espera la guardia de bajos; un alto o un medio, la de pie. Los `sm` valen en
  las dos.
- **Un bloqueo exige que el rival quede trabado.** El dummy en *Guard All*
  levanta la guardia en cuanto ve arrancar el ataque; si el golpe pasa de largo,
  la guardia dura un par de muestras y se va. Eso **no** es un bloqueo: el HUD
  espera a que el defensor quede bloqueado de verdad (flag `free2 = 0` durante el
  *blockstun*) y, si nunca ocurre, descarta el evento. Sin esto se publicaban
  bloqueos fantasma, ventajas inventadas y castigos falsos.
- **Los IDs de movimiento se repiten entre posturas.** Verificado en vivo: el
  mismo ID puede ser un `2` de pie y un `FC.4` agachado. Por eso, si el atacante
  está agachado y la tabla dice que ese ID es un alto, el HUD **no** proyecta la
  tabla y muestra el ID crudo (`#10`): es preferible no enseñar nada a enseñar el
  nombre y la frame data de otro movimiento.
- **Cómo comprobarlo tú mismo**: pon el dummy en `GUARDIA TOTAL`, haz un bajo y
  deberías ver `BLOCK` con su ventaja (por ejemplo `i12 −15`); cámbialo a
  `DE PIE`, repite y verás `HIT` (por ejemplo `i12 −4`). Los dos números son
  medidas reales del juego.

---

## 9. Tabla de frame data opcional

La medición **no depende del personaje**: el `startup` y la ventaja salen de los
contadores internos del juego. Lo único que cambia entre personajes es el
**nombre** del golpe y el dato **teórico** que se puede proyectar al instante,
porque el ID de movimiento es propio de cada personaje.

- Sin tabla, el HUD muestra el ID crudo (`#7`, `#21`, `#12`). Sirve igual para
  cualquier personaje y **nunca etiqueta mal** un golpe.
- Con tabla (`data/compiled/<personaje>.json`) enseña el nombre (`1`, `3+4`,
  `4,3`) y proyecta `i10~` / `+1~` en el frame 0; al conectar, la medición real
  sustituye al teórico.
- **Bryan** ya viene compilado como ejemplo.
- El `raw` de la mayoría del roster ya está descargado en `data/raw/`
  (`python tools/scraper.py --roster`). Falta asociar los IDs de los personajes
  que juegas.

```powershell
python tools/scraper.py bruce                 # frame data pública -> data/raw/
python tools/hud.py --p2-char bruce --learn   # asocia los IDs mientras juegas
```

`--learn` pregunta la notación por consola cuando aparece un ID desconocido, la
guarda en `data/mappings/<personaje>_map.json`, recompila y recarga el HUD **sin
reiniciar PPSSPP**. El flujo completo está en [`data/README.md`](data/README.md).

La tabla **nunca** puede falsear la medición: si no hay datos, o el personaje no
tiene tabla, el HUD sigue midiendo igual. Si la tabla y la memoria discrepan,
gana la memoria.

---

## 10. Cómo funciona por dentro

### 10.1 Lectura de memoria

El HUD abre el proceso de PPSSPP y lee **directamente** con `ReadProcessMemory`
una ventana de ~4,7 KB que cubre las dos estructuras de jugador. Una lectura
cuesta ~0,04 ms, así que se muestrea a ~1 kHz (unas 16 muestras por frame de
juego). Nunca usa el `memory.read` del debugger: eso pausa la CPU del emulador.

De cada jugador se leen: vida, contador de animación, estado, Move ID y los dos
flags de accionabilidad (`free` / `tail`).

### 10.2 Contacto: golpe o bloqueo

Dos señales inequívocas, nada de "el estado no es idle" (el estado `2114` es a la
vez ataque y *hitstun*):

| Señal | Cuándo |
|---|---|
| **HIT** | La vida del defensor baja en ese mismo frame. |
| **BLOCK** | El defensor entra en un estado de guardia conocido (`4178` de pie, `12329` de bajos) y queda trabado. |

### 10.3 Startup

El contador de animación del atacante **reinicia a 1** en cada movimiento, así que
su valor es el número de frame del movimiento. El `startup` es el valor del
contador en el frame del impacto. Esto no depende de que el lector pierda
muestras ni de que el Move ID aparezca uno o dos frames antes del reinicio del
contador (los dos casos desplazaban el número ±1).

### 10.4 Ventaja

```
ventaja = frame de liberación del defensor − frame de liberación del atacante
```

Los flags `+0x294` / `+0x295` marcan el frame exacto en que cada jugador puede
volver a actuar. El "fin de la animación" **no** sirve: la animación sigue ~12
frames después de que el personaje ya puede actuar.

### 10.5 Reloj compartido

Los dos jugadores se miden con **un solo reloj de frame de juego**. Antes cada
tracker contaba con el contador de su propio jugador y los relojes derivaban
(~17 frames en unos minutos), con lo que el aviso PUNISH —que compara un frame de
P1 con uno de P2— dejaba de tener sentido.

### 10.6 Motor híbrido (tabla + memoria)

```
Move ID cambia            contacto (vida baja / guardia)         unlock
     │                              │                              │
     ▼                              ▼                              ▼
[1] PROYECCIÓN                [2] STARTUP REAL              [3] VENTAJA REAL
tabla local, O(1)             medido en memoria             medida en memoria
"i10~ +1~" en gris            "i10" blanco                  "+1" con color
```

Los estados de guardia también son **datos, no código**: `data/states.json`
guarda qué valores cuentan como bloqueo y `--learn` añade los que descubra, sin
reiniciar el HUD.

---

## 11. Herramientas

| Herramienta | Función |
|---|---|
| `tools/hud.py` | **el tracker en pantalla** (motor de medición + overlay) |
| `tools/live.py` | el mismo motor en consola (sin ventana) |
| `tools/replay.py` | reproduce una grabación y muestra los eventos (depuración offline) |
| `tools/test_tracker.py` | autotest determinista del motor, sin PPSSPP |
| `tools/diag.py` | registro en vivo frame a frame + eventos |
| `tools/scraper.py` | descarga frame data pública → `data/raw/<pj>_raw.json` |
| `tools/mapper.py` | compila `raw/` + `mappings/` → `data/compiled/<pj>.json` |
| `tools/lookup_engine.py` | búsqueda O(1) Move ID → frame data |
| `tools/tracker_hybrid.py` | proyección de tabla + confirmación por memoria |
| `tools/learn.py` | modo `--learn`: aprende IDs y estados de guardia |
| `tools/game_states.py` | gestión de `data/states.json` |
| `tools/inputrec.py` | grabador de pulsaciones + memoria (`--bin` para replay) |
| `tools/pad.py` | inyecta botones por el bus WebSocket (opcional) |
| `tools/psp_mem.py` | localiza la RAM de PSP, lee memoria y detecta el disc ID |
| `tools/capture-window.ps1` | captura la ventana de PPSSPP |

Comprobación del motor sin emulador:

```powershell
python tools/test_tracker.py
```

Debe terminar con `TODO OK`.

---

## 12. Problemas frecuentes

| Síntoma | Causa / solución |
|---|---|
| `overlay already running` | Ya hay un HUD abierto. Ciérralo (o mata el proceso `python` que lo ejecuta) antes de abrir otro. |
| No aparece ningún dato | Comprueba que PPSSPP está **abierto y en primer plano**, que es la versión de **64 bits** y que estás en **Practice**. Si el HUD se lanzó como administrador y PPSSPP no, o al revés, Windows puede bloquear la lectura. |
| El panel aparece desplazado | Mueve PPSSPP y espera un segundo (se recoloca solo) o fija la posición con `--x` / `--y`. |
| Todo sale como `BLOCK` | El dummy está en `GUARDIA TOTAL`. Cámbialo a `DE PIE` para medir HIT. |
| Un golpe bajo no muestra nada | Comprueba que el golpe **conecta** (si el dummy lo bloquea debe salir BLOCK). Si sigue sin salir, ejecuta con `--learn`: el estado de guardia de tu personaje puede ser distinto del conocido y el HUD te lo ofrecerá para añadirlo. |
| Números `±1` distintos a una web | Es normal: depende del frame activo que conecta y del *hitstop*. La medición es la del juego. |
| El juego se congela al usar `tools/pad.py` | No envíes `buttons.send` en bucle: el debugger de PPSSPP se procesa en el hilo principal. Envía el botón una vez y libéralo con un mapa explícito. |
| El HUD no se cierra | Se cierra solo al cerrar PPSSPP; si PPSSPP no aparece en 20 s, también se cierra. |

Si nada de lo anterior encaja, arranca con `--debug` y mira la consola: cada
evento se imprime con su frame, y los mensajes de `--learn` explican qué ID o
estado ha visto.

---

## 13. Limitaciones

1. **Solo Practice**: en versus/arcade las direcciones y el flujo de estados
   pueden cambiar.
2. **±1 frame real**: no es ruido del medidor; aparece también en la frame data
   oficial como rangos.
3. **Knockdown / stagger**: se detectan como `HIT` (la vida baja) pero no se
   distingue knockdown, roll o stagger.
4. **`PUNISH` requiere que el rival haya bloqueado**: si su golpe te conecta (estás
   en hitstun), no hay ventana que enseñar.
5. **Nombres de golpe**: los IDs son por personaje y pueden repetirse entre
   posturas (ver §8). Sin tabla se muestra el ID crudo, que siempre es correcto.
6. **El bloqueo agachado depende del personaje**: los estados conocidos son
   `4178` (de pie) y `12329` (bajos). Si tu personaje usa otra variante, `--learn`
   la añade a `data/states.json` sin tocar código.

---

## 14. Avisos

- Solo **lectura** de memoria del emulador: no se modifica el juego.
- Herramienta pensada para **práctica offline** con tu propia copia del juego. No
  la uses en partidas online ni para automatizar el juego de otras personas.
- No distribuyas el ISO con el proyecto: no está incluido.

---

## 15. Empaquetado (sin Python instalado)

```powershell
.\build.ps1              # -> dist\FRAMEMOD\FRAMEMOD.exe
.\build.ps1 -OneFile     # -> dist\FRAMEMOD.exe (un solo archivo)
```

El `.exe` incluye el intérprete y las bibliotecas: sirve para un PC sin Python.

---

## Repositorio

<https://github.com/Madvillainism/MokuMod-T6BR>

No hay dependencias de Python fuera de la biblioteca estándar. El repo no incluye
`.cache/` (grabaciones, savestates y capturas de trabajo) ni `captures/`.

Documentación adicional:

- [`GUIA-USO.txt`](GUIA-USO.txt) — guía de uso en texto plano.
- [`GUIA-PRUEBAS.md`](GUIA-PRUEBAS.md) — cómo probarlo paso a paso y qué significa
  cada mensaje.
- [`data/README.md`](data/README.md) — estructura de la base de datos local.
