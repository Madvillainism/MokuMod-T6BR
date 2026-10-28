# `data/` — Base de datos local de frame data

Todo lo que el motor híbrido necesita para enseñar el dato **teórico** en el
mismo frame en que cambia el Move ID, sin depender de internet ni de Node.js.

```
data/
├── raw/                 # salida del scraper:  notación -> datos
│   └── bryan_raw.json
├── mappings/            # Move ID de RAM -> notación (manual o --learn)
│   └── bryan_map.json
├── compiled/            # base final que lee el HUD:  Move ID -> datos
│   └── bryan.json
└── states.json          # estados de guardia / idle / ataque
```

Los nombres de archivo usan un **slug estable**: minúsculas, espacios y puntos
como `_` (`armor king` → `armor_king`, `roger jr.` → `roger_jr`, `jack-6` se
queda igual). Así `--p1-char "armor king"` encuentra `compiled/armor_king.json`
o `raw/armor_king_raw.json` sin depender del sistema de archivos.

> **Importante:** `raw/` está indexado por **notación** (`"1"`, `"3+4"`) y
> `compiled/` por **Move ID de RAM** (`"7"`, `"20"`). El HUD solo predice con
> `compiled/`: si únicamente existe el `raw`, enseña `#id` y mide igual (usar
> `raw` como si fueran IDs mostraría la frame data del jab para el ID 1).

## Flujo completo

```
tekkendocs.com ──scraper.py──> data/raw/<pj>_raw.json
                                        │
HUD --learn ──> data/mappings/<pj>_map.json
                                        │
                                   mapper.py
                                        │
                                        v
                             data/compiled/<pj>.json  ──> HUD (lookup O(1))
```

### 1. Extraer la frame data pública

```powershell
python tools/scraper.py bryan            # un personaje
python tools/scraper.py --roster         # todo el roster de Tekken 6 (~2 min)
```

El scraper lee las tablas **por cabecera** (`Command`, `Startup`, `Block`,
`Hit`), no por posición fija, y si una tabla no trae cabeceras cae al formato
posicional. Guarda `data/raw/<personaje>_raw.json`.

TekkenDocs no usa el nombre del roster en todos los casos, así que hay un mapa
de slugs en `tools/scraper.py` (`SLUG_OVERRIDES`):

| Roster | Página |
|---|---|
| `craig marduk` | `/t6/marduk` |
| `ling` | `/t6/xiaoyu` |
| `marshall law` | `/t6/law` |
| `sergei dragunov` | `/t6/dragunov` |

**Cobertura actual: 38/40.** `mokujin` (mimético) y `panda` (comparte la tabla
de Kuma) no tienen página propia: el scraper los salta con un aviso. Se pueden
mapear a mano con `--learn` usando la tabla de quien imitan.

### 2. Enlazar los Move ID de la RAM

La RAM solo da números (`7`, `10`, `14`…), no nombres. Dos formas:

* **Automática** (`--learn`): juega en Practice; cuando aparece un ID que no
  está en el mapa, el HUD pregunta la notación por consola y la guarda.

  ```powershell
  python tools/hud.py --p1-char bryan --learn
  ```

* **Manual**: edita `data/mappings/<personaje>_map.json`:

  ```json
  { "7": "1", "10": "2", "14": "3", "20": "3+4",
    "crouch:10": "FC.4", "crouch:20": "qcb+3" }
  ```

  La clave `crouch:<id>` es para los movimientos que **solo** existen saliendo de
  la agachada y que comparten Move ID con otro de pie: el `20` es `3+4` de pie y
  `qcb+3` agachado (verificado en vivo), el `10` es `2` de pie y `FC.4` agachado.
  Sin la variante, el HUD no puede saber que el `20` agachado es un bajo y enseña
  `#20` con las dos guardias; con ella mide, etiqueta y elige la guardia correcta.
  `--learn` la guarda sola cuando el movimiento sale agachado.

### 3. Compilar la base final

```powershell
python tools/mapper.py bryan      # o --all
```

Genera `data/compiled/bryan.json` con la forma que lee el HUD:

```json
{
  "character": "bryan",
  "moves": {
    "7": { "notation": "1", "startup": 10, "on_block": 1, "on_hit": 8 }
  }
}
```

`--learn` recompila solo al añadir un ID, así que normalmente no hace falta
ejecutarlo a mano.

## `states.json`

```json
{
  "block_states": [4178],
  "crouch_block_states": [12329],
  "idle_states": [6466, 6482, 14633],
  "crouch_idle_states": [14633],
  "attack_states": [2114],
  "crouch_attack_states": [10273]
}
```

* `block_states`: estados del defensor que cuentan como **bloqueo de pie**.
  `4178` está verificado en vivo.
* `crouch_block_states`: bloqueo de **golpes bajos**. `12329` está verificado en
  vivo (el único estado que aparece al bloquear un low).
* `idle_states`: neutro (incluye el idle agachado `14633`).
* `crouch_idle_states`: idle agachado. Se usa para saber que un movimiento sale
  de la agachada (en el frame del Move ID el estado ya es `2114`, genérico).
* `attack_states`: ataque **y** hitstun (`2114`), por eso nunca se usan solos.
* `crouch_attack_states`: ataque agachado (`10273`). Sirve para desambiguar los
  Move ID que se repiten entre posturas y para elegir la guardia esperada.

**Cómo se elige la guardia:** el HUD mira el campo `type` de la tabla del
movimiento (`l` = low, `m`, `h`, `sm`). Un `l` solo se bloquea agachado, así que
la guardia esperada es `crouch_block_states`; un `h`/`m`, la de pie; un `sm`,
las dos. Si el atacante está agachado y no hay variante `crouch:<id>` se aceptan
las dos, porque los Move ID se repiten entre posturas (el `10` es `2` de pie y
`FC.4` agachado). Sin tabla, se aceptan las dos y decide el *stun*.

**Confirmación:** un bloqueo solo se publica si el defensor queda **trabado**
(`free2 = 0`) dentro del estado de guardia. El dummy en *Guard All* levanta la
guardia al ver arrancar el ataque; si el golpe falla, esa guardia dura un par de
muestras y se va. Sin esta comprobación se publicaban bloqueos fantasma.

Si un personaje usa variantes distintas, el HUD las aprende con `--learn` cuando
ve una reacción de guardia desconocida (el contador del defensor reinicia dentro
de tu movimiento y su estado no es idle/ataque/guardia conocida). También se
pueden añadir a mano:

```powershell
python tools/game_states.py --add-block 4182          # guardia de pie nueva
python tools/game_states.py --add-crouch-block 12345  # guardia de bajos nueva
```

## Consultar la base

```powershell
python tools/lookup_engine.py bryan        # lista el catálogo compilado
python tools/lookup_engine.py bryan 7      # detalle de un Move ID
python tools/mapper.py --list              # personajes con mapa
```

## Notación y botones

`raw/` está indexado por **notación Tekken** (`df+3`, `qcb+3`, `FC.4`, `1,2`…),
que es el idioma común para escribir entradas y propiedades. `tools/tekken_notation.py`
la parsea, la traduce a los botones de la PSP y la cruza con el catálogo:

| Botón PSP | Notación |
|---|---|
| Cuadrado | `1` (LP) |
| Triángulo | `2` (RP) |
| Cruz | `3` (LK) |
| Círculo | `4` (RK) |

```powershell
python tools/tekken_notation.py bryan df+3          # botones + startup + b/h
python tools/tekken_notation.py --check-raw         # cobertura sobre todos los raw
python tools/tekken_notation.py bryan --stances     # posturas del personaje
python tools/tekken_notation.py bryan --match 17 -10  # qué movimiento encaja
```

`on_block` / `on_hit` pueden ser un número (`-14`) o una etiqueta cualitativa
(`KND`, `CH`, `CS`). Si es etiqueta no hay frames que calcular: el HUD la
publica tal cual y no convierte el tiempo de levantarse en una ventaja.

`--check-raw` recorre los 38 `_raw.json` (5782 entradas) y comprueba que todas
se parsean: 100 % con estructura válida. Los tokens que no están en el
vocabulario son abreviaturas de postura propias de cada personaje y se aceptan
tal cual.

## Personajes

El motor funciona con **cualquier personaje** aunque no tenga tabla: en ese
caso el HUD enseña el ID crudo (`#37`) y mide igual (la telemetría no depende
de la tabla). La tabla solo añade el nombre y el valor teórico instantáneo.
