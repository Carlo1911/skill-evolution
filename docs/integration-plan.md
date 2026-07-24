---
title: "Integrar lo mejor de hermes-agent-self-evolution en el pipeline existente"
status: draft
author: Hermes Agent
created: 2026-07-23
tags: [plan, skill-evolution, integration, community-skill]
---

# Plan: Integrar hermes-agent-self-evolution + Skill Comunitario

## Contexto

Actualmente tenemos un pipeline de skill evolution funcional en `ai-memory-stack/`
(scripts/skill-evolution/) con cron diario, auto-apply, y propuestas estructuradas.
El repo `NousResearch/hermes-agent-self-evolution` (4.8k ⭐) ofrece una aproximación
complementaria: DSPy + GEPA para optimización evolutiva de texto de skills.

**No se trata de reemplazar** — el pipeline actual es más práctico y está en producción.
Se trata de **absorber lo mejor** del enfoque DSPy/GEPA donde aporte valor real.

---

## Análisis: Qué tomar del repo externo

### ✅ ALTO VALOR — integrar ahora

| Feature | Por qué | Costo |
|---------|---------|-------|
| **LLMJudge multi-dimensional** (fitness.py) | Reemplazar el keyword-overlap actual con scoring 3D: correctness, procedure_following, conciseness + feedback textual para GEPA | Bajo (~200 líneas) |
| **ConstraintValidator** (constraints.py) | Gates duras en auto-apply: size ≤15KB, growth ≤20%, estructura YAML válida. Hoy no hay validación antes de aplicar | Bajo (~150 líneas) |
| **Secret detection** (external_importers.py) | Ya existe en el repo, mejorarlo y portarlo a `fetch_sessions.py` para nunca minar API keys | Bajo (~50 líneas) |
| **SyntheticDatasetBuilder** (dataset_builder.py) | Generar datasets de eval sintéticos — útil para testear skills nuevos sin sesiones reales | Medio (~250 líneas) |
| **Estructura EvalExample/EvalDataset** | Reusable para trackear calidad de skills a través del tiempo | Bajo |

### 🔄 VALOR MEDIO — considerar después

| Feature | Por qué | Riesgo |
|---------|---------|--------|
| **GEPA optimizer** | Puede mutar texto de skills y evaluar iterativamente — pero hoy simula al agente con `dspy.ChainOfThought`, no ejecuta al agente real. La evaluación es proxy | La optimización podría mejorar el *texto* sin mejorar el *comportamiento real* |
| **SkillModule (DSPy)** | Wrap de SKILL.md como módulo DSPy optimizable. Elegante pero requiere `dspy>=3.0.0` como dependencia | Dependency overhead |
| **External Importers** (Claude Code, Copilot) | Útil para cold-start — nosotros ya minamos Hermes nativamente | No prioritario |

### ❌ BAJO VALOR — skip

| Feature | Razón |
|---------|-------|
| **Darwinian Evolver** | Fase 4 planeada, solo stub. AGPL v3, external CLI |
| **PR builder / benchmark_gate** | No implementados en el repo |
| **Fases 2-5** (tools, prompts, code, monitor) | Solo `__init__.py` vacíos |
| **Benchmark gating (TBLite, YC-Bench)** | Overkill para nuestro uso — no tenemos benchmarks de skills |

---

## Plan de Integración (4 fases)

### Fase 1: Calidad en auto-apply (esta semana)
**Objetivo:** Que auto-apply no aplique cambios rotos

```
├── proposal.py
│   └── apply_proposal() → VALIDAR antes de mutar
│       ├── ConstraintValidator.check_size()
│       ├── ConstraintValidator.check_growth()
│       ├── ConstraintValidator.check_skill_structure()
│       └── Solo si todo pasa → skill_manage
├── session-analyzer-prompt.md
│   └── Nueva sección: "constraints checking" después de auto-apply
└── proposal.py
    └── LLMJudge scoring en vez de keyword-overlap
        ├── correctness (0-1)
        ├── procedure_following (0-1)
        └── conciseness (0-1)
```

**Archivos a crear:**
- `scripts/skill-evolution/constraints.py` — port de ConstraintValidator
- `scripts/skill-evolution/fitness.py` — port de LLMJudge (multi-dimensional)

**Modificar:**
- `proposal.py` — integrar constraints validation en `apply_proposal()`
- `session-analyzer-prompt.md` — mejorar criterios de evaluación

**Verificación:**
- Auto-apply rechaza propuestas que exceden 15KB
- Auto-apply rechaza propuestas sin frontmatter YAML válido
- Auto-apply rechaza crecimiento >20% sobre baseline
- Tests unitarios para cada constraint

---

### Fase 2: Métricas de calidad de skills (próximas 2 semanas)
**Objetivo:** Trackear evolución de calidad de skills a través del tiempo

```
├── fitness.py
│   └── EvalDataset + SyntheticDatasetBuilder
│       ├── Por cada skill, generar dataset de eval sintético
│       ├── Correr evaluación periódica (cron semanal)
│       └── Trackear score histórico por skill
├── skill-quality-tracker.py (nuevo)
│   ├── Lee todos los skills
│   ├── Genera eval datasets con DSPy (si disponible) o con LLM directo
│   ├── Corre LLMJudge contra baseline y skills actuales
│   └── Reporta: qué skills mejoraron, cuáles empeoraron
```

**Archivos a crear:**
- `scripts/skill-evolution/dataset_builder.py` — port de SyntheticDatasetBuilder + EvalDataset
- `scripts/skill-evolution/skill_quality.py` — tracker + reporter

**Verificación:**
- Se puede ejecutar `python skill_quality.py --output report.md`
- Report muestra scores por skill, cambio vs baseline
- Se puede integrar como cron mensual

---

### Fase 3: GEPA como optimizador opcional (próximo mes)
**Objetivo:** Ocasionalmente, dejar que GEPA optimice un skill específico

```
├── optimize_skill.py (nuevo)
│   ├── Toma un skill + número de iteraciones
│   ├── Usa SyntheticDatasetBuilder para generar eval data
│   ├── Corre GEPA (dspy.GEPA) con el skill como módulo
│   ├── Valida constraints post-optimización
│   └── Genera propuesta de mejora (reusa proposal.py)
├── pyproject.toml
│   └── Optional dependency: dspy>=3.0.0 (no obligatorio)
```

**Flujo:**
```
User corre: python optimize_skill.py --skill debugging-and-error-recovery --iterations 10
  → Genera dataset sintético
  → Corre GEPA (10 iteraciones, ~$2-5)
  → Valida constraints
  → Crea propuesta en docs/skill-evolution/proposals/
  → Report: "GEPA mejoró score de 0.72 a 0.81 (+12.5%)"
```

**Condiciones:**
- `dspy` es dependencia opcional — si no está, el comando falla con mensaje claro
- No se toca el pipeline actual (sigue funcionando sin DSPy)
- GEPA solo optimiza skills, nunca tools/prompts/code (Fase 1 del repo)
- La salida es una propuesta, no un cambio directo — pasa por auto-apply gate

---

### Fase 4: Community Skill (junto con Fase 1)
**Objetivo:** Publicar SKILL.md en GitHub que otros puedan usar

```
├── SKILL.md → El skill comunitario (ver sección abajo)
├── scripts/skill-evolution/
│   └── Todo el pipeline, reusable por cualquiera
└── README.md → Instrucciones de setup
```

---

## Community Skill: Diseño

El skill que publicaremos en GitHub debe ser:

### 🔑 Principios

1. **Zero config assumption** — No asume `ai-memory-stack`, ni paths específicos
2. **Self-bootstrapping** — El propio agente crea los scripts si no existen
3. **Safe defaults** — Auto-apply OFF por defecto, solo "review mode"
4. **Works with vanilla Hermes** — Solo usa tools que todo Hermes tiene (file, terminal, cronjob, skill_manage)
5. **Clear boundaries** — No toca skills instalados por el hub, solo agent-created

### 📐 Estructura del Skill

```markdown
---
name: hermes-skill-evolution
description: "Autonomous skill improvement — analyze sessions, generate proposals, apply high-confidence changes"
author: Nous Research Community
---

# Hermes Skill Evolution

A self-improvement feedback loop for Hermes Agent skills.
Reads past sessions, analyzes them against loaded skills, and proposes improvements.

## Quick Start

1. Install the skill
2. Run `/skill hermes-skill-evolution`
3. Ask: "run skill evolution analysis"

The agent will:
- Fetch recent sessions
- Analyze them against existing skills
- Generate structured proposals
- Deliver a summary

## Auto-Apply (Opt-in)

By default, proposals are review-only. Enable auto-apply:

```
hermes config set skills.evolution.auto_apply true
hermes config set skills.evolution.min_confidence 0.85
```

## How It Works

[Descripción del pipeline completo]
```

### 📦 Lo que incluye el skill

| Componente | Qué hace |
|-----------|----------|
| `scripts/fetch_sessions.py` | Lee state.db, output NDJSON |
| `scripts/analyze.py` | Formatea sesiones para el agente |
| `scripts/skill_index.py` | Escanea skills instalados |
| `scripts/proposal.py` | Schema + I/O de propuestas |
| `session-analyzer-prompt.md` | Prompt template para el cron |

### 🚫 Lo que NO incluye (para mantenerlo genérico)

- GEPA / DSPy integration (requiere `dspy>=3.0.0` — optional install)
- `ai-memory-stack` específico
- Money-admin integration
- Wiki harvesting
- Nada de la configuración personal de Carlo

---

## Resumen de archivos

### Crear nuevos

| Archivo | Fase | Propósito |
|---------|------|-----------|
| `scripts/skill-evolution/constraints.py` | 1 | ConstraintValidator port |
| `scripts/skill-evolution/fitness.py` | 1 | LLMJudge multi-dimensional |
| `scripts/skill-evolution/dataset_builder.py` | 2 | EvalDataset + SyntheticDatasetBuilder |
| `scripts/skill-evolution/skill_quality.py` | 2 | Tracker histórico de calidad |
| `scripts/skill-evolution/optimize_skill.py` | 3 | GEPA wrapper opcional |
| `skills/hermes-skill-evolution/SKILL.md` | 4 | Skill comunitario |

### Modificar

| Archivo | Fase | Cambio |
|---------|------|--------|
| `proposal.py` | 1 | Integrar constraints en `apply_proposal()` |
| `session-analyzer-prompt.md` | 1 | Mejorar criterios de evaluación |
| `scripts/skill-evolution/fetch_sessions.py` | 1 | Mejorar secret detection |

---

## Definition of Done

### Fase 1 ✅
- [x] Constraints portados y probados
- [x] `apply_proposal()` valida antes de mutar
- [x] Secret detection mejorado en fetch_sessions
- [x] LLMJudge multi-dimensional implementado
- [x] Tests unitarios para constraints + fitness

### Fase 2
- [ ] Dataset builder sintético funcional
- [ ] skill_quality.py produce reportes
- [ ] Cron semanal de calidad funcionando

### Fase 3
- [ ] optimize_skill.py corre con DSPy instalado
- [ ] GEPA genera propuestas válidas
- [ ] Sin break en pipeline actual sin DSPy

### Fase 4
- [ ] SKILL.md publicado en GitHub
- [ ] README con instrucciones claras
- [ ] Scripts auto-contenidos (sin depender de ai-memory-stack)
