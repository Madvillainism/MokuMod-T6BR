# 05 — Roadmap híbrido (scraper + base local + auto-aprendizaje)

Estado de la implementación de los dos documentos del roadmap
(`t6-roadmap-1.docx` y `t6-roadmap-2.docx`). El objetivo era dejar de depender
de una tabla estática y de nombres escritos a mano: **el motor mide siempre** y
la tabla local solo adelanta el dato teórico en el frame 0.

```
Move ID (+0x144) ──► data/compiled/<pj>.json ──► proyección "i10~ +1~" (gris)
        │
        └──────────► memoria (vida / guardia 4178 / +0x295) ──► "i10 +1" (color)
```

## 1. Extracción y base de datos local — ✅

| Tarea | Estado | Dónde |
|---|---|---|
| Estructura `data/raw`, `data/mappings`, `data/compiled` | ✅ | `data/` |
| Scraper de frame data pública (40 personajes) | ✅ | `tools/scraper.py` |
| Parser por cabeceras + fallback posicional | ✅ | `tools/scraper.py` |
| Compilador `raw` + `mappings` → `compiled` | ✅ | `tools/mapper.py` |
| Lookup O(1) por Move ID | ✅ | `tools/lookup_engine.py` |
| Modo `--learn` (pregunta la notación y recompila) | ✅ | `tools/learn.py`, `tools/hud.py` |
| Recarga en caliente sin reiniciar PPSSPP | ✅ | `Learner.on_change` → `lookup.reload` |
| Bryan descargado (159 movimientos, 7 mapeados) | ✅ | `data/raw/bryan_raw.json` |
| Roster descargado | ✅ 38/40 | `data/raw/` (Mokujin y Panda sin tabla propia) |
| Slugs distintos en la fuente (`ling`→`xiaoyu`, …) | ✅ | `SLUG_OVERRIDES` en `tools/scraper.py` |
| Nombres de archivo estables (`armor king`→`armor_king`) | ✅ | `lookup_engine.slugify` |
| `raw/` (notación) no se usa como si fueran Move IDs | ✅ | `FrameTableLookup.is_id_indexed` |

## 2. Motor híbrido — ✅

| Tarea | Estado | Dónde |
|---|---|---|
| Proyección de tabla al cambiar el Move ID | ✅ | `tracker_hybrid.HybridTracker.project` |
| Confirmación por memoria (startup y ventaja) | ✅ | `HybridTracker.confirm` |
| Marca visual del dato teórico (`~`, gris, `TABLA`) | ✅ | `tools/hud.py` (overlay) |
| La memoria manda si discrepa | ✅ | `hud.TrackerThread._confirm` |
| Fallback a `#id` sin tabla (cualquier personaje) | ✅ | `HybridTracker.project` |

## 3. Multi-hit del mismo id — ✅

El contador de animación (`+0x7C`) **reinicia a 1** en cada golpe del string.
Ese reinicio abre un segmento nuevo, así que el segundo golpe se mide desde su
propio frame 1 (antes salía `i51`/`i52`). También arregló el bloqueo del jab
(`i11` → `i10`): el startup sale de `c1 − c0 + 1` y no de contar muestras.

## 4. Estados de guardia (incluida la agachada) — ✅ configurable

| Tarea | Estado | Dónde |
|---|---|---|
| Estados en datos, no en código | ✅ | `data/states.json` |
| Guardia de pie verificada (`4178`) | ✅ | por defecto |
| Descubrir estados nuevos en vivo | ✅ | `--learn` (pregunta y guarda) |
| Recarga en caliente | ✅ | `TrackerThread.reload_states` |
| Confirmar valores de guardia agachada | ⏳ pendiente de prueba en vivo |

## 5. Portabilidad y empaquetado — ✅

| Tarea | Estado | Dónde |
|---|---|---|
| Quitar Node.js (bus en Python nativo) | ✅ | `tools/ppsspp_ws.py` (`--serve`, puerto 8098) |
| Cliente de entrada sin dependencias | ✅ | `tools/pad.py` (misma API) |
| Detección automática del PID | ✅ | `psp_mem.find_pid` (ya existía) |
| Detección de la región / disc ID | ✅ | `psp_mem.detect_game_id` (`--game-id`) |
| `.exe` autónomo (PyInstaller) | ✅ | `framemod.spec`, `build.ps1` |
| Panel visual (posición, opacidad, fuente, topmost) | ✅ | `--settings` / **F8**, `config/hud.json` |

## 6. Pendiente (requiere PPSSPP abierto)

1. Confirmar en vivo el arreglo del bloqueo (`i10`) y los multi-hit.
2. Verificar el cambio de fase `start` (proyección) en el overlay.
3. Probar `--learn` con un id desconocido y una guardia agachada.
4. Probar F8 y que `config/hud.json` persista.
5. Compilar el `.exe` (`build.ps1`) y probarlo en un PC sin Python.
6. Mapear con `--learn` los personajes que juegas (el `raw` de los 38 ya está
   descargado: solo faltan los Move IDs).

## 7. Cómo probarlo (resumen)

```powershell
python tools/test_tracker.py          # motor, sin PPSSPP
python tools/hud.py --debug           # overlay + traza
python tools/hud.py --p1-char bryan --learn
python tools/scraper.py --roster
python tools/mapper.py --all
.\build.ps1
```

La batería manual está en [`../GUIA-PRUEBAS.md`](../GUIA-PRUEBAS.md) §4.8–4.11.
