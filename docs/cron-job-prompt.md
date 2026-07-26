Analiza las sesiones de Hermes inyectadas abajo (NDJSON) y genera propuestas de mejora de skills.

Sigue las instrucciones de /Users/carloalva/Projects/hermes-skill-evolution/docs/session-analyzer-prompt.md como guía para el análisis, el formato de las propuestas y el reporte final.

Primero corre `python3 /Users/carloalva/Projects/hermes-skill-evolution/scripts/skill_index.py` para obtener el índice de skills instaladas, y analiza cada sesión contra esa librería. Escribe cada propuesta como un archivo markdown en /Users/carloalva/Projects/hermes-skill-evolution/docs/skill-evolution/proposals/ (el directorio se crea solo si no existe).

MODO: SOLO PROPUESTAS. No apliques cambios.

- No llames a `skill_manage` para modificar, crear, fusionar ni borrar skills, aunque `SKILL_EVOLUTION_AUTO_APPLY` esté en `true`. Aplicar una propuesta es un paso separado, con revisión humana.
- Cada propuesta debe permitir decidir sin releer las sesiones: qué está mal hoy, la evidencia citada por `session_id`, el cambio antes/después, qué mejora y cómo verificarlo, y los trade-offs. Ver la sección "Making a proposal reviewable" del archivo de instrucciones.
- Si una propuesta elimina o recorta guía existente, justifícalo explícitamente: borrar contenido es la forma más fácil de subir el score y empeorar el comportamiento.
- Solo genera propuestas con confianza >= 0.5. Una propuesta bien evidenciada vale más que cinco especulativas.
- Salta las sesiones con `source="cron"` y no analices la sesión de este propio run.

El reporte final es la notificación que alguien va a leer: una línea por propuesta (tipo, skill objetivo, confianza y la mejora en pocas palabras), lo que necesita decisión primero, y dónde quedaron los archivos. Si no generaste ninguna propuesta, dilo en una línea y por qué — un run silencioso es un resultado válido.
