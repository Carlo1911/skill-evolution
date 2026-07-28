# Plan: Post-application history migration para `create_new` proposals

> Created: 2026-07-27
> Status: Shipped

## Problema

`RegressionEvaluator` es efectivamente `improve_existing`-only porque:
- `create_new` proposals tienen `target_skill` vacío → `target_key_for_proposal()` retorna `proposal:<uuid>`
- Cada propuesta obtiene un target key único → 1 entrada en historial → auto-pass sin detectar regresión
- Cuando el skill se crea, el historial del `create_new` queda aislado bajo `proposal:<uuid>`, desconectado del `skill:<name>` que las futuras `improve_existing` usarán

## Solución

Migrar el historial de `proposal:<uuid>` a `skill:<name>` después de que un `create_new` se aplique y cree el skill. **Ejecución síncrona** dentro de `apply_proposal()`, sin flag `--no-migrate`.

### Decisiones de diseño

| Decisión | Elección | Razón |
|----------|----------|-------|
| Timing | **Síncrona** (en `apply_proposal()`) | Consistencia: historial listo inmediatamente. Si falla, falla antes de confirmar el skill como creado. ~100ms extra es insignificante vs. la consistencia. |
| Flag `--no-migrate` | **No** | La migración es idempotente y segura. Rollback no causa daño (entry bajo nombre incorrecto). Debugging se resuelve con `read_history()`. Testing con monkeypatch. |
| Nombre del skill | **De `proposed_changes`** (`field="name"`) | Es la única fuente disponible en el momento de la migración. Si el humano renombra, el historial queda bajo el nombre original (mismo comportamiento que antes). |
| Fallback | **`proposal:<uuid>`** si no se encuentra nombre | Mantener comportamiento actual para propuestas sin nombre claro. |

## Diseño detallado

### 1. Nueva función `migrate_proposal_history()`

Ubicación: `scripts/evaluate.py`, cerca de `append_history()`.

```python
def migrate_proposal_history(proposal_id: str, new_target: str) -> int:
    """Migrate history entries from proposal:<id> to skill:<name> after a create_new is applied.

    Returns the number of entries migrated (0 if none found or already migrated).

    Safety: reads the full file, filters entries whose 'target' matches the old key,
    rewrites with the new key, and appends the original entries to the archive file.
    Same write-order as prune_history(): archive first, then primary.
    """
```

**Lógica:**
1. Leer `eval_history.jsonl` completo
2. Filtrar entries donde `target == f"proposal:{proposal_id}"`
3. Si no hay entries, retornar 0 (no-op)
4. Reescribir cada entry filtrada con `target = new_target`
5. Append originales al archivo de archivado (`eval_history.archive.jsonl`)
6. Reescribir el archivo primario sin las entries migradas + con las entries re-escritas
7. Retornar count de entries migradas

### 2. Integración con `apply_proposal()`

En `scripts/proposal.py`, después de que el gate pasa y antes de construir las instructions:

```python
# After gate passes, before building instructions:
if proposal.type == ProposalType.CREATE_NEW and proposal.proposal_id:
    skill_name = None
    for change in proposal.proposed_changes:
        if change.field == "name" and change.new_value:
            skill_name = change.new_value
            break

    if skill_name:
        new_target = f"skill:{skill_name}"
        migrated = evaluate.migrate_proposal_history(proposal.proposal_id, new_target)
        if migrated:
            print(f"Migrated {migrated} history entries from proposal:{proposal_id} to {new_target}")
```

### 3. Cambio en `target_key_for_proposal()`

Fallback para que `--retroactive` y evaluaciones en vivo acumulen historial bajo `skill:<name>` desde la primera propuesta:

```python
def target_key_for_proposal(proposal: Any) -> str:
    """Return the shared history-store target key for a proposal."""
    target_skill = getattr(proposal, "target_skill", None)
    if target_skill:
        return f"skill:{target_skill}"

    # For create_new proposals, try to extract skill name from changes
    proposal_type = getattr(getattr(proposal, "type", None), "value", "")
    if proposal_type == "create_new":
        for change in getattr(proposal, "proposed_changes", []):
            if getattr(change, "field", None) == "name":
                skill_name = getattr(change, "new_value", None)
                if skill_name:
                    return f"skill:{skill_name}"

    proposal_id = getattr(proposal, "proposal_id", "unknown")
    return f"proposal:{proposal_id}"
```

### 4. Seguridad de la migración

- **Misma postura que `prune_history()`:** archivar ANTES de reescribir el primario
- **Idempotente:** segunda ejecución → 0 entries migradas (ya no están bajo `proposal:<uuid>`)
- **Merge:** si `skill:<name>` ya tiene entries, las migradas se appendan al final

### 5. Impacto en `original_size_for_target()`

Después de la migración, `original_size_for_target("skill:<name>")` leerá la primera entry migrada (el `create_new` original). Correcto: el tamaño original del skill es el que se creó en la primera propuesta.

## Archivos a modificar

| Archivo | Cambio |
|---------|--------|
| `scripts/evaluate.py` | Agregar `migrate_proposal_history()` + modificar `target_key_for_proposal()` |
| `scripts/proposal.py` | Llamar `migrate_proposal_history()` post-aplicación en `apply_proposal()` |
| `tests/test_evaluate_history.py` | Tests de migración (happy path, no-op, idempotencia, archivado) |
| `tests/test_proposal_gate.py` | Test de integración: apply create_new → historial migrado |
| `tests/test_evaluate_skill_text.py` | Actualizar test de `target_key_for_proposal` para create_new |
| `docs/pending-work-2026-07-26.md` | Marcar item como resuelto |
| `CLAUDE.md` | Actualizar nota sobre RegressionEvaluator being improve_existing-only |

## Orden de implementación

1. `migrate_proposal_history()` en `evaluate.py` + tests unitarios
2. `target_key_for_proposal()` fallback para create_new + tests
3. Integración en `proposal.py:apply_proposal()` + test de integración
4. Actualizar docs (CLAUDE.md, pending-work)
5. Suite completa

## Tests

```python
# test_evaluate_history.py
def test_migrate_proposal_history_moves_entries():
    """Entries under proposal:<id> are migrated to skill:<name>."""

def test_migrate_proposal_history_noop_when_no_entries():
    """No entries found → returns 0, no file changes."""

def test_migrate_proposal_history_idempotent():
    """Running twice doesn't duplicate or lose entries."""

def test_migrate_proposal_history_archives_before_rewrite():
    """Original entries are appended to archive before primary is rewritten."""

def test_migrate_proposal_history_merges_with_existing_skill_history():
    """If skill:<name> already has entries, migrated entries are appended."""

# test_proposal_gate.py
def test_apply_create_new_migrates_history():
    """apply_proposal(create_new) migrates history from proposal:<id> to skill:<name>."""

# test_evaluate_skill_text.py
def test_target_key_for_proposal_uses_name_from_changes_for_create_new():
    """create_new proposal with name in changes → skill:<name>."""

def test_target_key_for_proposal_falls_back_when_no_name():
    """create_new without name in changes → proposal:<uuid>."""
```

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|------------|
| Crash mid-migration | Archivar antes de reescribir (misma postura que prune_history) |
| Nombre en changes no coincide con skill creado | Mismo comportamiento que antes; el humano puede renombrar de todos modos |
| Performance en archivos grandes | O(n) donde n crece lentamente (~1 entry/día). La mayoría de runs no migran nada. |
| `--retroactive` re-evalúa con nueva lógica | Comportamiento intencional: las propuestas existentes se benefician |
