# Formato de perfiles de prompt

Cada archivo `.md` de esta carpeta funciona como un perfil seleccionable desde Streamlit.

Estructura minima:

```md
---
id: identificador_unico
name: Nombre visible en la interfaz
description: Descripcion corta
default: true
---

## RELEVANCE_PROMPT
Texto completo del prompt para el filtro de relevancia.

## ANALYSIS_PROMPT
Texto completo del prompt para el analisis estrategico.
```

## Recomendaciones para edicion

- Mantén los nombres de las secciones exactamente como `RELEVANCE_PROMPT` y `ANALYSIS_PROMPT`.
- Puedes cambiar libremente el contenido teorico y metodologico dentro de cada seccion.
- Evita modificar la estructura JSON esperada, a menos que tambien ajustes el codigo del validador y del PDF.
- Si creas un nuevo archivo `.md`, aparecera automaticamente en el selector del frontend.
- El codigo ahora adjunta un perfil exploratorio de audio a cada llamada del LLM. Si quieres aprovecharlo mas, explicitalo dentro del prompt.
