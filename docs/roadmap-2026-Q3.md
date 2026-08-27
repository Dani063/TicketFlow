# Roadmap Q3 2026

> Peticiones de la revisión con dirección (10/06/2026) organizadas para el próximo trimestre, ordenadas por valor.

## 1. FAQ para clientes

Base de conocimiento con artículos administrables y vista pública en TicketFlow, multiidioma (ES/EN), sustituyendo la documentación estática actual (`/docs/`). Objetivo: reducir tickets de tipo `question`.

- Modelo `KnowledgeArticle` (slug, marca opcional, idioma, título, cuerpo HTML sanitizado, publicado).
- Admin CRUD en el panel (mismo patrón que plantillas de respuesta, editor Quill).
- Vista pública por marca, con buscador simple.

## 2. Análisis de casos similares + feedback de la IA

"Módulo de análisis que explore casos similares y poder afinar a la IA lo que se quiere resolver."

- `embed()` en `app/services/ai.py` (Azure OpenAI `text-embedding-3-small`, mismo recurso/credenciales que la clasificación).
- Modelo `TicketEmbedding` (vector JSON + hash de contenido) detrás de una interfaz `SimilarTicketIndex` (coseno en memoria al volumen actual; swappable a OpenSearch k-NN si crece).
- Población: task Celery al resolver/cerrar tickets (los resueltos son los ejemplares útiles) + comando de backfill.
- Sugerencia de similares en el detalle del ticket para el agente.
- **Feedback loop**: task nocturna que compara las correcciones de los agentes (`TicketEvent.old_value` vs `TicketAIAnalysis.suggested_*`) → `TicketAIFeedback`; consumidores: (a) ejemplos few-shot en el prompt (de ahí `prompt_version` en el análisis), (b) export JSONL para fine-tuning cuando haya volumen.

La arquitectura del sprint de junio ya lo deja preparado: modelo de análisis separado con sugerencias congeladas, confianza por campo y versionado de prompt/modelo.

## 3. Informes de auditoría estilo Zendesk

"De cara a las auditorías, mirar los informes que hace Zendesk para hacerlos."

- UI de auditoría sobre `TicketEvent` + `InboundEmailLog` + `OutboundEmailLog` con filtros por fecha/marca/agente.
- Export PDF (evaluar `weasyprint` vs `reportlab`).
- Revisión de los informes de Zendesk como referencia funcional antes de diseñar.

## 4. Mejoras de la IA en la interfaz

- Endpoint de re-clasificación manual (`POST /api/tickets/<id>/ai-classify`, agentes).
- Surfacing de sugerencias y `reasoning` de la IA en el detalle del ticket (los datos ya están en `ticket.ai_analyses`).
