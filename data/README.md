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
  { "7": "1", "10": "2", "14": "3" }
  ```

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
  "idle_states": [6466, 6482, 14633],
  "attack_states": [2114]
}
```

* `block_states`: estados del defensor que cuentan como **bloqueo**. `4178`
  (blockstun de pie) está verificado en vivo.
* `idle_states`: neutro.
* `attack_states`: ataque **y** hitstun (`2114`), por eso nunca se usan solos.

La **guardia agachada** usa otros valores: con `--learn`, cuando el HUD ve una
reacción de guardia desconocida (el contador del defensor reinicia dentro de tu
movimiento y su estado no es idle/ataque/guardia conocida), pregunta si
añadirla. Al aceptar se escribe aquí y el HUD recarga los estados sin
reiniciarse. También se puede añadir a mano:

```powershell
python tools/game_states.py --add-block 4182
```

## Consultar la base

```powershell
python tools/lookup_engine.py bryan        # lista el catálogo compilado
python tools/lookup_engine.py bryan 7      # detalle de un Move ID
python tools/mapper.py --list              # personajes con mapa
```

## Personajes

El motor funciona con **cualquier personaje** aunque no tenga tabla: en ese
caso el HUD enseña el ID crudo (`#37`) y mide igual (la telemetría no depende
de la tabla). La tabla solo añade el nombre y el valor teórico instantáneo.
