# 🧪 Guía de pruebas — FRAMEMOD (Tekken 6 PSP)

Guía para probar el HUD **tú mismo**, con PPSSPP lanzado desde tu escritorio y
`tools/hud.py` ejecutado desde el terminal de VS Code. No hace falta el daemon
de teclado ni inyectar botones: todo se mide con lo que tú juegas.

---

## 1. Lo que necesitas

| Cosa | Detalle |
|---|---|
| PPSSPP | 1.19.3 (Windows x64), con el perfil que ya tienes |
| Juego | Tekken 6 (Europe) `ULES01376` |
| Savestate | uno **dentro de Practice** (el actual: `ULES01376_1.00_0.ppst`) |
| Python | 3.11 o superior, con `tkinter` (el instalador oficial lo trae) |
| Proyecto | `C:\Users\Carlos\Desktop\FRAMEMOD` |

No hay dependencias que instalar: solo biblioteca estándar.

---

## 2. Arranque (2 pasos)

### Paso 1 — PPSSPP desde tu escritorio

Doble clic en PPSSPP y carga el savestate (`Estado → Cargar estado →` slot 1),
o arrastra el `.ppst` sobre la ventana. Debes quedar **dentro de Practice**, con
los dos personajes en pantalla.

> Si el juego está en el menú de Practice, **se pausa** (los contadores dejan de
> avanzar). Cierra el menú con START antes de medir.

### Paso 2 — HUD desde VS Code

1. Abre la carpeta del proyecto en VS Code: `Archivo → Abrir carpeta… →
   C:\Users\Carlos\Desktop\FRAMEMOD`.
2. Abre el terminal integrado: **Ctrl + ñ** (o `Terminal → Nuevo terminal`).
3. Comprueba que estás en la raíz del proyecto (el *prompt* debe acabar en
   `FRAMEMOD>`) y lanza:

```powershell
python tools/hud.py --debug
```

Variantes útiles:

```powershell
python tools/hud.py                                  # sin consola
python tools/hud.py --p1-char bryan --p2-char bruce  # nombres de golpe
python tools/hud.py --no-predict                     # sin el valor adelantado (~)
python tools/hud.py --x 40 --y 40                    # posición fija (si tapa algo)
```

### Qué debe pasar

- Aparece un panel **sin bordes ni fondo** sobre PPSSPP, centrado y justo debajo
  de las barras de vida, con dos filas:

```
▌ P1   1        i10      +1     BLOCK
▌ P2   #12      i20     −15     BLOCK
```

- Se puede jugar con normalidad: el panel **no roba clics** (click-through).
- Al cerrar PPSSPP, el panel **desaparece solo** en ~1 segundo.
- Si lo lanzas dos veces, la segunda copia escribe `overlay already running` y
  no dibuja nada (así no se apilan dos paneles).

---

## 3. Preparar el dummy (menú de Practice)

START → menú de práctica (el juego se congela mientras está abierto):

| Opción | Valor para probar |
|---|---|
| `ELEGIR MODO` | **ENTRENAMIENTO DEFENSIVO** (la CPU ataca con la lista) |
| `LISTA DE COMANDOS` | al menos **2 movimientos** (elige los que quieras) |
| `MUÑECO 1` | `EN GUARDIA DE PIE` para bloquear / `DE PIE` para golpes limpios |
| `RESTAB. POSICIÓN` | `SÍ` (reinicia tras cada intercambio) |

En entrenamiento defensivo aparece **`SELECT + R EMPEZAR`**. Con el mando:
SELECT + R. (Desde fuera: `python tools/pad.py --combo select+rtrigger`, pero
no lo necesitas si juegas tú.)

---

## 4. Batería de pruebas

Marca cada casilla. Los valores oficiales de Bryan están entre paréntesis.

### 4.1 Startup en bloqueo

1. `MUÑECO 1` = `EN GUARDIA DE PIE`.
2. Con P1 (Bryan), haz un jab `1`.

| Esperado | Hecho |
|---|---|
| `P1 1 i10` y ventaja `+1` (`BLOCK`) | ☐ |

Prueba también `3` (`i16`, `−4`), `4` (`i12`, `−5`), `1+2` (`i14`, `−10`),
`3+4` (`i18`, `−8`). El `startup` debe coincidir **exacto**; la ventaja puede
bailar ±1 (es real: depende del frame activo y del *hitstop*).

### 4.2 Golpe limpio (HIT)

1. `MUÑECO 1` = `DE PIE` (sin guardia).
2. Jab `1`.

| Esperado | Hecho |
|---|---|
| `P1 1 i10 +8 HIT` (azul) | ☐ |

### 4.3 Ventaja del dummy (fila P2)

1. `MUÑECO 1` = `EN GUARDIA DE PIE` y **mantén atrás** para bloquear.
2. Deja que el dummy te pegue y bloquea.

| Esperado | Hecho |
|---|---|
| `P2 #12 i20 −15 BLOCK` y tu fila `P1 +15` | ☐ |
| El valor aparece ~0,5 s después del impacto (es cuando el dummy se libera) | ☐ |
| Mientras tanto se ve `~−15` (última medición del mismo golpe) | ☐ |

### 4.4 PUNISH (la prueba importante)

Requisito: un movimiento del dummy que lo deje **−12 o peor** (el `12` de la
lista actual es `−15`).

1. Mantén **atrás** para bloquear ese movimiento.
2. En cuanto termine el bloqueo, **suelta atrás y pulsa `1`** de inmediato
   (es un jab `i10`; solo cabe si el dummy queda a −10 o peor).

| Esperado | Hecho |
|---|---|
| Tu fila: `P1 1 i10 +8 HIT` **PUNISH** (rojo) | ☐ |
| La fila del dummy **nunca** muestra `PUNISH` | ☐ |
| La fila del dummy queda en `—` o `~−15` (su recuperación quedó cortada) | ☐ |

### 4.5 No es PUNISH

1. Bloquea el mismo movimiento.
2. Espera **medio segundo** y entonces pulsa `1`.

| Esperado | Hecho |
|---|---|
| `P1 1 i10 +8 HIT` **sin** `PUNISH` | ☐ |
| En consola: `(tarde (…>…+1))` | ☐ |

### 4.6 Cualquier personaje

1. Sal de Practice y elige **otro personaje** (Kazuya, Lili, quien sea).
2. Repite 4.1 y 4.2.

| Esperado | Hecho |
|---|---|
| El nombre del golpe sale como `#7`, `#21`… (id crudo) | ☐ |
| El `startup` y la ventaja se siguen midiendo igual | ☐ |
| Con `--p1-char bryan` vuelven los nombres de Bryan | ☐ |

> El id de movimiento es **de cada personaje**, por eso el HUD no inventa
> nombres: mide igual para todos y solo etiqueta lo que tú le digas en
> `tools/notation.json`.

### 4.7 Overlay

| Prueba | Esperado | Hecho |
|---|---|---|
| Cierra PPSSPP | el panel desaparece solo | ☐ |
| Lanza `hud.py` dos veces | la segunda dice `overlay already running` | ☐ |
| Arrastra/mueve PPSSPP | el panel lo sigue | ☐ |
| Abre el menú de Practice | los números se congelan (el juego está pausado) | ☐ |

### 4.8 Sin el juego (opcional)

```powershell
python tools/test_tracker.py
```

Debe imprimir 6 líneas `ok` y `TODO OK`. Es el mismo motor, con partidas
sintéticas: sirve para saber si un fallo es del motor o del entorno.

---

## 5. Cómo leer la consola con `--debug`

Cada evento sale con el **frame del reloj compartido**, el arranque medido, la
ventaja, el tipo y el motivo de (no) PUNISH:

```
f  6132 P2 final  #12   i20  −15 BLOCK  ~−15  [6089->6101]
f  6968 P1 impact 1     i10    - HIT           PUNISH  [6960->6968]
f  5440 P1 impact 1     i10    - HIT    (bloqueo viejo (+71f))  [5431->5440]
```

| Texto | Significado |
|---|---|
| `PUNISH` | el golpe conectó dentro de la ventana del último bloqueo |
| `(tarde (X>Y+1))` | llegaste tarde: el dummy ya se había liberado |
| `(i10 no cabe en −6)` | el dummy solo quedó a −6: ese golpe no es castigable con un jab |
| `(sin bloqueo previo)` | no hay bloqueo registrado antes de ese golpe |
| `(no conecto (BLOCK))` | el golpe fue bloqueado, no es castigo |
| `(bloqueo viejo (+Nf))` | el último bloqueo fue hace demasiados frames |
| `(empezo en stun (A<B))` | empezaste el golpe antes de estar libre |

Si algo no sale, **cópiane 5-6 líneas alrededor del evento** (con el `--debug`
activo) y dime qué hiciste: el motivo entre paréntesis dice exactamente qué
comprobación falló.

---

## 6. Problemas y soluciones

| Síntoma | Causa / solución |
|---|---|
| `overlay already running` y no ves nada | hay un HUD viejo abierto: ciérralo (Administrador de tareas → `python.exe`) o reinicia el terminal |
| El panel no aparece | comprueba que PPSSPP está abierto **antes**; si no lo está, el HUD se cierra solo a los 20 s |
| El panel está en otro sitio | `--x 40 --y 40`, o mueve PPSSPP (el panel lo sigue cada 0,7 s) |
| Todo en `—` y no se mueve nada | el menú de Practice está abierto (juego pausado) |
| `python` no se reconoce | en VS Code: `Ctrl+Shift+P → Python: Select Interpreter`, o usa `py tools/hud.py` |
| `ModuleNotFoundError: tkinter` | reinstala Python marcando *tcl/tk* |
| PPSSPP se cierra solo | relanza con el savestate; el HUD se cierra contigo |
| Números absurdos (`−55`) | no debería pasar ya: mándame la traza `--debug` de ese intercambio |

---

## 7. Chuleta

```powershell
# HUD
python tools/hud.py --debug
python tools/hud.py --p1-char bryan --p2-char bruce
python tools/hud.py --x 40 --y 40

# sin ventana (mismo motor)
python tools/live.py

# autotest sin PPSSPP
python tools/test_tracker.py

# registro frame a frame (para depurar)
python tools/diag.py 60          # deja .cache/diag.log

# drill del dummy desde fuera (opcional)
python tools/pad.py --combo select+rtrigger
```

---

## 8. Qué contarme si algo falla

1. Qué personaje usabas (P1 y dummy).
2. Qué hiciste exactamente (bloquear qué, cuándo pulsaste).
3. Las líneas de consola de esos segundos (`--debug`).
4. Si el número salió mal: el valor que esperabas y el que salió.

Con eso el motivo entre paréntesis suele bastar para localizarlo.
