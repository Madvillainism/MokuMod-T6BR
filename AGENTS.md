# 🥋 Tekken 6 Frame Data Assistant (PPSSPP)

## Arquitectura de Software e Ingeniería Inversa de Estados Globales

Este módulo implementa un motor de telemetría y un overlay gráfico
embebido para el modo entrenamiento de _Tekken 6_ (PSP), emulado en
PPSSPP. El desarrollo se desacopla mediante programación reactiva
basada en eventos de animación para evitar el parpadeo de datos
(_memory jittering_).
Esta es la ruta de mi juego de Tekken 6 dentro del PPSSPP: C:\Users\Carlos\Documents\PPSSPP\PSP\GAME
---

### 🏛️ 1. Arquitectura de Datos y Debounce de Memoria

Inspirado en _Tekken Bot Prime_, el script no evalúa la memoria en
crudo en cada ciclo. Implementa un sistema de **Detección de Cambios de
Estado (Debounce)** guardando el fotograma anterior para mitigar el
ruido de actualización del motor gráfico de Namco (60Hz).
Usa el código con precaución.
[ RAM de PPSSPP ] ──> (Lectura a 60Hz) ──> [ Filtro de Cambio de Estado ]
│
┌──────────────────────────────────────┴─────────────
─────────────────────────┐
▼ (Si el Estado o MoveID Cambió) ▼ (Si se mantiene igual)
[ Resetear Contadores Lógicos ] [ Incrementar Frames Activos / Stun ]

---

### ⏱️ 2. Mapeo del Ciclo de Vida del Combate (Seccionado)

El desarrollo se ejecuta de forma incremental dividiendo el motor en 4
capas lógicas de complejidad ascendente:
+---------------------------------------------------------------------------------+
| FASE 1: Motor de Tiempo (Impact Frames) |
| -> Leer Flags de Acción Globales de P1. Contar frames hasta Hitbox = Activa. |
+---------------------------------------------------------------------------------+
│
▼
+---------------------------------------------------------------------------------+
| FASE 2: Captura e Ingesta Dinámica |
| -> Aislar el ID del Movimiento actual. Almacenar métricas en caché local. |
+---------------------------------------------------------------------------------+
│
▼
+---------------------------------------------------------------------------------+
| FASE 3: Motor Matemático de Ventajas (On Block / On Hit) |
| -> Ecuación: P2_GuardStun - P1_Recovery. Colorear HUD (Verde: Plus / Rojo:
Neg) |
+---------------------------------------------------------------------------------+
│
▼
+---------------------------------------------------------------------------------+
| FASE 4: Sistema de Validación de Castigos (PUNISH Alert) |
| -> Ventana temporal: P1_Startup <= Abs(P2_Last_Advantage) && P1_Hit == true |
+---------------------------------------------------------------------------------+

---

### 🔍 3. Diccionario de Pointers Estáticos y Flags Globales

En lugar de mapear miles de strings de movimientos, el script rastrea
los **Flags de Estado Globales** de las estructuras de los jugadores
(`P1_Base` y `P2_Base`):
| Variable Clave | Tipo | Propósito en el Código |
| :--- | :--- | :--- |
| `ADDR_GAME_MODE` | Byte | Candado de seguridad. Solo ejecuta el HUD
si su valor es equivalente a _Practice Mode_. |
| `P1_MOVE_ID` | Integer | ID único del movimiento. Dispara el reset
del contador de frames cuando cambia. |
| `P1_ANIM_FRAME` | Short | Reloj de la animación actual. Determina el
ciclo de vida del movimiento. |
| `P2_STUN_STATE` | Byte | Flag binario. `0` = Libre para actuar, `1` =
En congelamiento (HitStun o GuardStun). |
| `P2_STUN_TIMER` | Short | Contador regresivo de frames de
congelamiento. Vital para calcular la ventaja matemática. |

---

### 🎨 4. Renderizado Gráfico Condicional (HUD Primitivo)

La API gráfica pinta directamente sobre el búfer del emulador usando
colores HEX estructurados en formato `0xRGBA`:

```lua
-- Colores de la Escena Competitiva
local COLOR_PLUS = 0x00FF00FF -- Verde: Ventaja segura
local COLOR_MINUS = 0xFFFFFFFF -- Blanco: Desventaja segura (-1f a
-9f)
local COLOR_PUNISH = 0xFF0000FF -- Rojo: Movimiento castigable (>=
-10f)
-- Renderizado de la Alerta Punish
if show_punish_label then
 PPSSPP.DrawText(240, 120, "¡¡ PUNISH !!", COLOR_PUNISH)
end
```
