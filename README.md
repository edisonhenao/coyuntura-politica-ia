# Optimización del Análisis de Coyuntura Política con IA

MVP de herramienta politológica desarrollada en Streamlit para convertir un audio de un discurso  político en una transcripción auditable, un perfil exploratorio de audio, un filtro de relevancia política y un insumo estructurado para el análisis de coyuntura política.

> [!IMPORTANT]
> **Estado actual del proyecto:** este repositorio contiene una **prueba de concepto (PoC) desarrollada como trabajo de grado**, pensada principalmente para **discursos políticos de un solo hablante**. No realiza diarización, no separa automáticamente varias voces, no procesa lotes de audios y no sustituye el juicio profesional del politólogo. Su propósito es validar si una herramienta de IA puede integrarse de forma útil, trazable y metodológicamente prudente a la caja de herramientas cotidiana de la Ciencia Política.

El proyecto sigue el siguiente flujo completo:

```text
Audio
  -> optimización local para transcripción
  -> transcripción temporal con OpenAI
  -> perfil exploratorio de audio
  -> filtro de relevancia política
  -> análisis estructurado de coyuntura
  -> resultados, observabilidad y exportaciones
```

---

## Guía de componentes para analistas y evaluadores

Esta guía permite ubicar los cinco componentes principales del artefacto, comprender su función y distinguir los ajustes de configuración de los cambios que requieren desarrollo. Se mantiene la aplicación monolítica en `app.py`; la guía es documentación estática y no añade controles ni navegación a la interfaz de Streamlit.

**Referencia del código:** las funciones y líneas indicadas se verificaron sobre el commit [`9eb90d49234532454369bcd6550e9a5da54fd81e`](https://github.com/edisonhenao/coyuntura-politica-ia/tree/9eb90d49234532454369bcd6550e9a5da54fd81e). Si cambia el archivo, utiliza el nombre de la función para localizarla; las líneas son una referencia de esa versión.

| Componente | Dónde localizarlo | Qué hace | Qué puede ajustar el analista |
|---|---|---|---|
| Configuración | `.streamlit/secrets.toml.example`; `app.py`, líneas 38-138: `_read_secret_or_env`, `_read_float_setting`, `_read_int_setting` y constantes | Lee credenciales, modelos, límites, umbrales acústicos y tarifas desde secretos o variables de entorno. | Crear su archivo privado de configuración y cambiar los valores admitidos; no publicar credenciales. |
| Procesamiento de audio | `app.py`: `inspect_audio_input` (458-496), `prepare_audio_files` (570-687), `transcribe_with_openai_api` (706-735) y `process_audio_pipeline` (1314-1449) | Inspecciona el audio, genera M4A y WAV, solicita la transcripción, normaliza segmentos y construye el perfil acústico. | Cargar un audio, escribir el contexto y configurar bitrate, límite de carga y umbrales acústicos. Cambiar etapas o formatos exige editar código. |
| Prompts y marco politológico | `src/system_prompts/01_dsrm_colombia_general.md`; `app.py`: `_extract_prompt_sections` (331-340) y `load_prompt_profiles` (343-375) | Separa las instrucciones del filtro de relevancia y del análisis de coyuntura en perfiles Markdown seleccionables. | Duplicar un perfil, cambiar sus metadatos, categorías, ejemplos y reglas, conservando los encabezados y el contrato de salida. |
| Modelo de lenguaje | `app.py`: `call_openai_json` (1553-1603), `run_relevance_filter` (1791-1806) y `run_strategic_analysis` (1809-1825) | Envía al modelo el prompt de sistema, la transcripción, el contexto y un resumen del perfil acústico; recibe JSON y registra telemetría. | Configurar `OPENAI_ANALYSIS_MODEL` con un modelo compatible y ajustar las tarifas de referencia. El proveedor implementado es OpenAI. |
| Exportación | `app.py`: `PoliticalReportPDF` (1828-2076), `build_pdf` (2079-2226), `technical_summary_to_text` (2543-2640) y bloque de exportación en `main` (3307-3383) | Prepara TXT, CSV, JSON, PDF y TXT técnico a partir del resultado conservado en la sesión. | Descargar cada salida y conservarla por ejecución. Cambiar los campos o el diseño del informe requiere revisar los generadores y normalizadores. |

### Configuración: qué cambiar y dónde

La plantilla pública enumera los parámetros admitidos. Copia sus valores a `.streamlit/secrets.toml` y sigue los pasos de las secciones 1 y 2. La sección 14 describe las variables. En esta versión, la configuración se consulta primero en `st.secrets` y después en el entorno.

Conviene distinguir tres niveles de intervención:

| Nivel | Ejemplos | ¿Requiere modificar `app.py`? |
|---|---|---|
| Uso de la interfaz | Archivo de audio, contexto inicial y selección del perfil de prompts. | No. |
| Configuración o criterio analítico | Modelos, bitrate, umbrales disponibles, tarifas, definiciones teóricas y ejemplos del perfil. | No, mientras se mantengan las capacidades y el esquema existentes. |
| Cambio de funcionamiento o estructura | Diarización, lotes, otro proveedor, nuevos campos JSON o nuevas salidas. | Sí; requiere desarrollo y verificación. |

### Audio: entradas, transformaciones y límites

`prepare_audio_files` crea ambos archivos desde el audio original: un M4A/AAC para la transcripción remota y un WAV PCM para Librosa. `process_audio_pipeline` coordina la transcripción y la extracción de características, y elimina sus archivos temporales al terminar. Convertir una fuente comprimida a WAV no recupera información que ya se perdió en la grabación o compresión original.

El límite operativo se comprueba sobre el **M4A que se envía**, no como un máximo universal de tamaño del archivo original. La interfaz también estima su tamaño antes de iniciar. La duración aproximada de 51 minutos depende de los valores predeterminados de bitrate y límite; no es un límite fijo independiente de la configuración.

El contexto inicial orienta la transcripción y acompaña el análisis. Para comparar ejecuciones, conserva exactamente ese contexto, el audio y el perfil utilizado.

### Prompts: criterio analítico y contrato de salida

El archivo del perfil contiene metadatos (`id`, `name`, `description`, `default`) y dos encabezados obligatorios: `## RELEVANCE_PROMPT` y `## ANALYSIS_PROMPT`. El primero define la relevancia política; el segundo operacionaliza tiempo, estructura, acontecimientos, actores, relaciones de fuerza, escenarios y disputa de sentidos.

Para adaptar el enfoque sin cambiar el programa:

1. Duplica el perfil como otro archivo `.md` dentro de `src/system_prompts/`.
2. Asigna un `id` único y un nombre que distinga la variante; usa `default: false` si no será el perfil predeterminado.
3. Ajusta definiciones, ejemplos y reglas, manteniendo los encabezados obligatorios y las claves y tipos JSON esperados.
4. Selecciona el perfil en la interfaz y ejecuta el flujo con un caso de prueba.
5. Contrasta la transcripción, el JSON y el PDF antes de utilizar los resultados.

La normalización se encuentra en `normalize_relevance_payload` y `normalize_analysis_payload`. Cambiar la estructura de salida puede exigir ajustes adicionales en esos normalizadores, la interfaz y `build_pdf`. Pedir JSON al modelo y normalizarlo no equivale a validar formalmente un esquema ni la corrección de sus interpretaciones.

### Modelos y parámetros efectivamente implementados

Los valores predeterminados del código revisado son `whisper-1` para transcripción y `gpt-5.6-luna` para las dos llamadas de análisis. Son identificadores configurados en esta versión; su disponibilidad debe comprobarse en la cuenta que ejecute el artefacto. Cambiar el nombre de un modelo no asegura que admita los mismos formatos y parámetros.

La transcripción solicita `verbose_json` y marcas de tiempo por segmento y palabra. El análisis usa `client.chat.completions.create` con `response_format={"type": "json_object"}`. **Las llamadas revisadas no fijan explícitamente `temperature` ni `seed`**; por ello, esta versión no debe describirse como configurada con temperatura 0 ni como garantía de resultados idénticos.

El prompt establece la escala de densidad y espera coherencia entre `density_score` y `relevant`. En la implementación revisada, la continuación depende de `relevant`. `normalize_relevance_payload` usa el umbral de 51 como valor de respaldo cuando la decisión no se interpreta como un booleano válido; no impone una comprobación independiente del umbral cuando recibe un booleano válido. Revisa ambos campos al interpretar el filtro. Una validación estricta conjunta de puntuación y decisión requiere un ajuste del código.

### Exportaciones y evidencia por ejecución

`build_technical_summary` (líneas 2422-2540) reúne tiempos, estado, modelos y solicitudes. El JSON exporta ese resumen junto con la transcripción, segmentos, perfil acústico, relevancia y análisis. El PDF es una vista legible y no contiene toda la telemetría del JSON. Ninguna exportación garantiza por sí sola que todas las afirmaciones tengan evidencia textual: cada resultado debe contrastarse con la fuente.

Descarga el JSON y el TXT técnico antes de iniciar otra ejecución, porque el siguiente proceso sustituye los resultados de la sesión. Para distinguir una ejecución de una nueva descarga del mismo resultado, consulta los campos de auditoría de la sección 13.1.

---

## 1. Inicio rápido

### 1.1. Requisitos

Antes de ejecutar la aplicación necesitas:

- **Python 3.10 o superior**.
- **FFmpeg** instalado y disponible en el `PATH`.
- Una **API key de OpenAI** con acceso a los modelos configurados.
- Facturación o saldo disponible en la plataforma de API de OpenAI.
- Conexión a internet durante las llamadas de transcripción y análisis.

> [!NOTE]
> La facturación de la API de OpenAI es independiente de las suscripciones de ChatGPT. Tener ChatGPT Plus, Pro, Business u otro plan no implica que el uso de la API esté incluido.

### 1.2. Clonar el repositorio

```bash
git clone https://github.com/edisonhenao/coyuntura-politica-ia
cd coyuntura-politica-ia
```

### 1.3. Crear y activar un entorno virtual

#### macOS o Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

#### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 1.4. Instalar dependencias de Python

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Las dependencias principales son:

- Streamlit
- OpenAI SDK
- Pydub
- Librosa
- NumPy
- Pandas
- Plotly
- FPDF

### 1.5. Instalar FFmpeg

FFmpeg se utiliza para leer los formatos de audio admitidos y producir dos archivos temporales: un M4A/AAC optimizado para OpenAI y un WAV PCM para el análisis local con Librosa.

#### macOS con Homebrew

```bash
brew install ffmpeg
```

#### Ubuntu o Debian

```bash
sudo apt update
sudo apt install ffmpeg
```

#### Windows con Winget

```powershell
winget install Gyan.FFmpeg
```

Comprueba la instalación:

```bash
ffmpeg -version
```

---

## 2. Configurar la API key de OpenAI

La aplicación no incluye una clave de API. Cada persona que clone el repositorio debe utilizar su propia credencial.

### 2.1. Crear una API key

1. Ingresa a la plataforma de OpenAI.
2. Crea o selecciona un proyecto.
3. Genera una API key para ese proyecto.
4. Verifica que la cuenta de API tenga facturación o saldo habilitado.
5. Nunca publiques la clave en GitHub, capturas de pantalla, notebooks o archivos compartidos.

Documentación oficial:

- [OpenAI API quickstart](https://platform.openai.com/docs/quickstart)
- [Buenas prácticas para proteger API keys](https://platform.openai.com/docs/guides/production-best-practices)
- [Facturación de ChatGPT y API](https://help.openai.com/en/articles/9039756-managing-billing-settings-on-chatgpt-web-and-platform)

### 2.2. Crear el archivo local de secretos

El repositorio incluye una plantilla pública:

```text
.streamlit/secrets.toml.example
```

Cópiala como archivo local privado:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

En Windows:

```powershell
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
```

Abre `.streamlit/secrets.toml` y reemplaza el valor de ejemplo:

```toml
OPENAI_API_KEY = "sk-proj-replace-with-your-key"
```

por tu clave real:

```toml
OPENAI_API_KEY = "sk-proj-..."
```

Configuración mínima recomendada:

```toml
OPENAI_API_KEY = "sk-proj-..."

OPENAI_TRANSCRIPTION_MODEL = "whisper-1"
OPENAI_ANALYSIS_MODEL = "gpt-5.6-luna"
OPENAI_API_TIMEOUT_S = 180
```

El archivo real `.streamlit/secrets.toml` está ignorado por Git. La plantilla `.streamlit/secrets.toml.example` sí debe permanecer versionada.

Más detalles: [`.streamlit/README_SECRETS.md`](.streamlit/README_SECRETS.md).

### 2.3. Ejecutar la aplicación

```bash
streamlit run app.py
```

Streamlit abrirá la interfaz en el navegador. Por defecto suele utilizar:

```text
http://localhost:8501
```

Si modificas `secrets.toml` mientras la aplicación está abierta, detén el servidor y vuelve a iniciarlo.

---

## 3. Cómo usar la aplicación

### Paso 1. Cargar un audio

Formatos admitidos por la interfaz:

- MP3
- WAV
- M4A
- FLAC
- OGG

La aplicación inspecciona el archivo localmente antes de realizar llamadas facturables y muestra:

- tamaño original;
- duración;
- tamaño estimado del archivo optimizado;
- límite de carga configurado;
- duración máxima teórica aproximada con el bitrate actual.

### Paso 2. Escribir el contexto inicial

El contexto es obligatorio. Debe ayudar a identificar:

- fecha o periodo;
- evento o coyuntura;
- persona que habla;
- país o territorio;
- institución o escenario;
- actores relevantes;
- antecedentes necesarios para interpretar el discurso.

Ejemplo:

```text
Discurso presidencial pronunciado durante la instalación del Congreso.
País: Colombia. Tema central: reformas sociales, relación con la oposición
y agenda legislativa del gobierno.
```

Este contexto cumple dos funciones:

1. sirve como apoyo léxico para la transcripción;
2. se incorpora como marco situacional declarado en el filtro y el análisis.

No debe utilizarse para introducir conclusiones que el audio no contiene.

### Paso 3. Ejecutar el pipeline

Presiona **Ejecutar pipeline**. El flujo realiza las siguientes etapas:

1. Optimización del audio.
2. Transcripción con timestamps.
3. Extracción de métricas de audio.
4. Filtro de relevancia política.
5. Análisis estratégico, solo si el audio supera el filtro.
6. Generación de exportaciones y resumen técnico.

### Paso 4. Interpretar el filtro

El filtro asigna una densidad política entre 0 y 100.

```text
0–25   Sin relevancia política suficiente
26–50  Densidad baja e insuficiente
51–75  Densidad media y analizable
76–100 Densidad alta y claramente analizable
```

El criterio del perfil es considerar analizables las puntuaciones desde 51. En el código revisado, el pipeline se detiene si el resultado normalizado contiene `relevant = false`; no comprueba de forma independiente que una decisión `true` venga acompañada de una puntuación de al menos 51. Si se detiene, la transcripción, el perfil de audio y el resumen técnico continúan disponibles. La guía de componentes explica esta distinción entre el criterio del prompt y la condición programada.

### Paso 5. Revisar resultados

La interfaz se organiza en cinco pestañas:

- **Pipeline:** carga, contexto, chequeos previos y ejecución.
- **Resultados:** filtro, análisis de coyuntura, actores, disputa de sentidos y trazabilidad.
- **Resumen técnico:** tamaños, modelos, tokens, tiempos, request IDs y costos estimados.
- **Exportación:** descarga de archivos auditables.
- **Proyecto:** espacio opcional para documentos teóricos o metodológicos del proyecto.

---

## 4. Qué exporta el MVP

La aplicación puede generar:

| Formato | Contenido |
|---|---|
| TXT | Transcripción completa |
| CSV | Segmentos, timestamps, métricas y alertas técnicas |
| JSON | Resultado integral: metadatos, perfil de audio, relevancia, análisis y resumen técnico |
| PDF | Informe legible de análisis de coyuntura, cuando el audio supera el filtro |
| TXT técnico | Modelos, tamaños, tokens, tiempos, costos estimados y estado de ejecución |

El JSON es la salida más completa y adecuada para auditoría, reproducción y futuras integraciones.

---

## 5. Alcance actual

### Lo que sí hace

- Procesa un audio por ejecución.
- Está optimizado para discursos con un solo hablante principal.
- Convierte el audio a un formato liviano antes de enviarlo a OpenAI.
- Conserva un WAV local separado para el análisis acústico.
- Obtiene transcripción, segmentos y timestamps con `whisper-1`.
- Calcula un perfil exploratorio de pausas, ritmo, energía, pitch y calidad técnica.
- Filtra materiales sin suficiente densidad política.
- Estructura categorías relevantes para el análisis de coyuntura.
- Registra tamaños, tokens, costos y tiempos de cada etapa.
- Permite cambiar el perfil politológico sin editar el código principal.

### Lo que todavía no hace

- No separa ni identifica múltiples hablantes.
- No realiza diarización.
- No procesa entrevistas con turnos de habla de forma diferenciada.
- No procesa varios audios en lote.
- No compara automáticamente un corpus de discursos.
- No persiste proyectos o resultados en una base de datos.
- No incluye autenticación ni gestión de usuarios.
- No dispone de colas de procesamiento o trabajos en segundo plano.
- No permite elegir entre distintos proveedores de IA.
- No incorpora todavía modelos locales.
- No reemplaza la lectura, interpretación ni validación humana.

---

## 6. Cómo se construyó el MVP

### 6.1. Decisión arquitectónica

El proyecto se mantiene deliberadamente como una aplicación monolítica en `app.py`.

Esta decisión favorece:

- iteración rápida;
- depuración directa;
- despliegue sencillo en Streamlit;
- validación temprana de la propuesta de valor;
- cambios metodológicos sin una infraestructura excesiva.

El monolito ya separa responsabilidades mediante funciones y clases, aunque todavía no mediante módulos independientes. La modularización puede incorporarse cuando la aplicación requiera múltiples desarrolladores, procesamiento concurrente, persistencia o varios proveedores de IA.

### 6.2. Arquitectura funcional

```mermaid
flowchart TD
    A[Audio del usuario] --> B[Inspección local]
    B --> C[FFmpeg]
    C --> D[M4A AAC mono 16 kHz 64 kbps]
    C --> E[WAV PCM mono 16 kHz]
    D --> F[OpenAI whisper-1]
    F --> G[Transcripción + segmentos + palabras + timestamps]
    E --> H[Librosa]
    G --> I[Normalización y métricas de Whisper]
    H --> J[RMS + pitch + ZCR]
    I --> K[Perfil exploratorio de audio]
    J --> K
    K --> L[Filtro de relevancia política]
    L -->|No relevante| M[Resultados parciales + resumen técnico]
    L -->|Relevante| N[Análisis de coyuntura con GPT]
    N --> O[PDF + TXT + CSV + JSON + resumen técnico]
```

---

## 7. Preprocesamiento del audio

El sistema genera dos artefactos temporales diferentes.

### Archivo enviado a OpenAI

```text
Contenedor: M4A
Codec: AAC
Bitrate: 64 kbps
Sample rate: 16 kHz
Canales: mono
```

Este archivo reduce de forma importante el tamaño de la carga sin enviar un WAV PCM sin compresión.

### Archivo utilizado localmente

```text
Formato: WAV
Codec: PCM s16le
Sample rate: 16 kHz
Canales: mono
Profundidad: 16 bits
```

El WAV no se envía a OpenAI. Se conserva temporalmente para que Librosa trabaje sobre una señal PCM estable.

### Límite de tamaño

La aplicación utiliza un límite configurable de 25 MB para el archivo enviado a la API. Con el bitrate predeterminado de 64 kbps, la interfaz calcula una duración teórica aproximada cercana a 50 minutos, pero el tamaño real puede variar según el contenedor y la codificación.

Antes de llamar a OpenAI se verifica el tamaño real del M4A generado. Si supera el límite, el proceso se detiene sin realizar la llamada de transcripción.

Los audios más extensos requerirán un futuro sistema de fragmentación y recomposición temporal.

---

## 8. Transcripción

El modelo predeterminado es:

```toml
OPENAI_TRANSCRIPTION_MODEL = "whisper-1"
```

La elección responde a que el MVP necesita:

- `verbose_json`;
- timestamps por segmento;
- timestamps por palabra;
- información temporal para pausas y ritmo;
- metadatos útiles para el perfil técnico.

La transcripción recibe el contexto inicial como apoyo léxico. La aplicación conserva:

- texto completo;
- segmentos;
- palabras temporizadas;
- duración de cada segmento;
- `avg_logprob`;
- `compression_ratio`;
- `no_speech_prob`;
- temperatura reportada por Whisper, cuando está disponible.

Documentación oficial: [Speech to text — OpenAI API](https://platform.openai.com/docs/guides/speech-to-text).

---

## 9. Perfil exploratorio de audio

El MVP combina metadatos de Whisper con descriptores calculados localmente mediante Librosa.

### Variables derivadas de la transcripción

- inicio y final de cada segmento;
- duración;
- número de palabras;
- velocidad aproximada en palabras por minuto;
- pausa antes del segmento;
- palabras de duración atípica;
- palabras de baja probabilidad, cuando el modelo las ofrece;
- alertas de confianza, compresión o silencio.

### Variables acústicas locales

- energía RMS;
- pitch mediano;
- variabilidad del pitch;
- zero crossing rate;
- proporción de tramos con voz, cuando puede estimarse.

### Índice exploratorio de intensidad

La aplicación construye un índice relativo dentro de cada audio y selecciona momentos para revisión reforzada.

> [!WARNING]
> Este índice no es una escala psicológica, no detecta emociones y no permite comparar mecánicamente a distintos oradores. Es una heurística para orientar la lectura humana hacia segmentos potencialmente relevantes.

---

## 10. Filtro de relevancia política

Antes de ejecutar un análisis extenso, el sistema pregunta si la transcripción contiene suficiente densidad política.

El perfil actual rastrea, entre otros elementos:

1. acontecimientos con sentido político;
2. escenarios de lucha política;
3. actores identificables;
4. relaciones de fuerza;
5. disputa de poder;
6. disputa de sentidos;
7. construcción de una frontera nosotros/ellos.

Esta etapa reduce costos y evita forzar categorías politológicas sobre audios protocolarios, personales o carentes de conflicto sustantivo.

---

## 11. Análisis de coyuntura

Cuando el audio supera el filtro, el modelo analítico produce una salida JSON normalizada.

El perfil incluido operacionaliza las siguientes dimensiones:

### Tiempo

- larga duración;
- mediana duración;
- corta duración.

### Estructura y coyuntura

- estructura económica;
- estructura política;
- estructura social.

### Acontecimientos

- acontecimiento desencadenante;
- acontecimientos derivados;
- tendencias de fondo.

### Actores

- individuales;
- agregados;
- colectivos;
- institucionales.

Para cada actor puede identificar:

- postura frente al cambio;
- intereses;
- principios;
- recursos;
- relaciones de fuerza;
- cita clave;
- recomendación estratégica en borrador.

### Relaciones de fuerza

- enfrentamiento;
- cooperación;
- coexistencia;
- subordinación.

### Disputa de sentidos

- resignificación de conceptos;
- construcción del nosotros;
- construcción del ellos;
- frontera amigo/enemigo.

### Escenarios

- públicos institucionales;
- públicos sociales;
- privados;
- internacionales.

El resultado no pretende ser un análisis político terminado. Es un insumo ordenado, trazable y revisable para que el profesional lo valide, lo contraste con otras fuentes y desarrolle su propia interpretación.

---

## 12. Perfil teórico y prompts editables

El corazón politológico no está incrustado por completo en el código. Se encuentra en archivos Markdown dentro de:

```text
src/system_prompts/
```

Cada archivo puede convertirse en un perfil seleccionable desde la interfaz.

Estructura mínima:

```markdown
---
id: identificador_unico
name: Nombre visible
description: Descripción corta
default: false
---

## RELEVANCE_PROMPT

Prompt del filtro de relevancia.

## ANALYSIS_PROMPT

Prompt del análisis estratégico.
```

Para crear un nuevo enfoque:

1. duplica el perfil existente;
2. cambia `id`, `name` y `description`;
3. adapta el marco teórico, categorías y reglas;
4. conserva los encabezados `RELEVANCE_PROMPT` y `ANALYSIS_PROMPT`;
5. ajusta el código si modificas la estructura JSON esperada.

Consulta [`src/system_prompts/README_system_prompt.md`](src/system_prompts/README_system_prompt.md).

### Perfil actual

El perfil `01_dsrm_colombia_general.md` está formulado como un marco general aplicable a distintos contextos políticos. Integra categorías atribuidas en el propio archivo a De Souza, Nieto, Zamitiz, Errejón, Fazio, Licha, García y Schmitt.

---

## 13. Observabilidad y costos

El MVP registra información técnica por ejecución.

### Audio

- tamaño del archivo original;
- tamaño del M4A enviado;
- tamaño del WAV local;
- porcentaje de reducción;
- duración;
- bitrate, codec, sample rate y canales.

### OpenAI

- modelo solicitado;
- modelo devuelto;
- request ID, cuando está disponible;
- caracteres de entrada y salida;
- tokens de entrada;
- tokens cacheados;
- escrituras de caché;
- tokens de salida;
- tokens de razonamiento reportados;
- tokens totales;
- tiempo de cada llamada.

### Costos estimados

Se calculan por separado:

- transcripción;
- filtro de relevancia;
- análisis político;
- total de la ejecución.

Las tarifas de referencia están definidas en `.streamlit/secrets.toml.example` y pueden actualizarse sin modificar el código. El cálculo conserva tarifas configuradas para el modelo de referencia; cambiar `OPENAI_ANALYSIS_MODEL` no actualiza automáticamente esos precios.

> [!CAUTION]
> Los valores mostrados son estimaciones. La fuente definitiva es la facturación y el panel de uso de OpenAI.

### 13.1. Reejecución, caché y verificación de la transcripción

En la versión de código citada en la guía, cada pulsación válida de **Ejecutar pipeline** reinicia `transcript`, `transcription_payload`, los resultados analíticos y la telemetría. Después llama a `process_audio_pipeline`, que invoca `transcribe_with_openai_api` y solicita la transcripción al servicio. No se implementa reutilización automática de transcripciones por coincidencia del hash del audio.

El decorador `st.cache_data` está aplicado a `inspect_audio_input`, que inspecciona duración y tamaño estimado. No está aplicado a la función de transcripción. Por su parte, `st.session_state` conserva el resultado para mostrarlo o exportarlo entre interacciones: consultar pestañas o volver a descargar un informe no acredita una nueva ejecución. Los contadores de tokens cacheados del LLM pertenecen a otra etapa y no demuestran reutilización de la transcripción.

| Evidencia en el JSON | Para qué sirve | Límite de interpretación |
|---|---|---|
| `technical_summary.run.started_at` y `finished_at` | Fechas y horas registradas del proceso. | No confundir con `meta.exported_at`, que corresponde a la exportación. |
| `technical_summary.run.audio_hash` | Identificar el archivo original mediante los primeros 16 caracteres de su SHA-256. | El mismo hash es esperable al repetir el mismo audio; no indica reutilización de resultados. |
| `technical_summary.transcription.request_id` | Relacionar la transcripción con una solicitud, cuando el SDK devuelve ese dato. | Para acreditar dos llamadas, comparar los identificadores de transcripción de ambas rondas. |
| `technical_summary.transcription.elapsed_s` y `model_requested` | Documentar tiempo y modelo solicitados para esa etapa. | Tiempos diferentes apoyan la trazabilidad; no bastan por sí solos para probar dos llamadas. |
| `technical_summary.relevance_call.request_id` y `analysis_call.request_id` | Identificar las solicitudes de las etapas de lenguaje. | Son solicitudes diferentes de la transcripción; no la sustituyen como evidencia. |
| `transcript` y `audio_profile` | Comparar textos y descriptores obtenidos. | Resultados iguales no prueban reutilización ni, con dos casos, estabilidad general del sistema. |

Para una prueba de repetibilidad, conserva por separado el audio original, contexto, perfil de prompts, configuración sin secretos, versión del código y JSON de cada ronda. El JSON actual no incorpora automáticamente el texto completo del contexto, una copia íntegra del prompt ni el commit: archívalos aparte. Registra también las reglas de normalización del texto y emparejamiento de categorías usadas para comparar.

Si faltan los registros de una ronda, formula la conclusión como una comparación de las salidas disponibles. No atribuyas a una caché lo que no se ha demostrado ni presentes la identidad entre dos textos como validación general del modelo. Una prueba nueva debe documentarse como nueva; no reconstruye retroactivamente la telemetría que falte de una prueba anterior.

---

## 14. Configuración disponible

Las variables se leen primero desde `st.secrets` y, como alternativa, desde variables de entorno.

| Variable | Función |
|---|---|
| `OPENAI_API_KEY` | Credencial de OpenAI |
| `OPENAI_TRANSCRIPTION_MODEL` | Modelo de transcripción |
| `OPENAI_ANALYSIS_MODEL` | Modelo para relevancia y análisis |
| `OPENAI_API_TIMEOUT_S` | Timeout de llamadas |
| `OPENAI_AUDIO_FILE_LIMIT_MB` | Límite del archivo enviado |
| `OPENAI_AUDIO_BITRATE_KBPS` | Bitrate AAC del M4A |
| `OPENAI_AUDIO_ESTIMATE_OVERHEAD_FACTOR` | Margen conservador de estimación |
| `PAUSE_DRAMATIC_THRESHOLD_S` | Umbral de pausa prolongada |
| `ELONGATED_WORD_THRESHOLD_S` | Duración atípica de palabra |
| `LOW_CONFIDENCE_LOGPROB_THRESHOLD` | Alerta de confianza baja |
| `HIGH_COMPRESSION_RATIO_THRESHOLD` | Alerta por ratio de compresión |
| `HIGH_NO_SPEECH_PROB_THRESHOLD` | Alerta por silencio o ruido |
| `PRICING_REFERENCE_DATE` | Fecha de tarifas configuradas |
| `WHISPER_PRICE_PER_MINUTE_USD` | Precio estimado por minuto |
| `GPT_SHORT_*` | Tarifas de contexto corto |
| `GPT_LONG_*` | Tarifas de contexto largo |
| `GPT_LONG_CONTEXT_THRESHOLD_TOKENS` | Umbral de cambio de tarifa |

---

## 15. Estructura del repositorio

```text
coyuntura-politica-ia/
├── .streamlit/
│   ├── README_SECRETS.md
│   └── secrets.toml.example
├── src/
│   └── system_prompts/
│       ├── 01_dsrm_colombia_general.md
│       └── README_system_prompt.md
├── .gitignore
├── LICENSE
├── README.md
├── app.py
├── packages.txt
└── requirements.txt
```

### Archivos principales

| Archivo | Función |
|---|---|
| `app.py` | Aplicación monolítica completa |
| `requirements.txt` | Dependencias de Python |
| `packages.txt` | Dependencias del sistema para Streamlit Cloud |
| `.streamlit/secrets.toml.example` | Plantilla pública de configuración |
| `src/system_prompts/*.md` | Perfiles politológicos y contratos JSON |
| `.gitignore` | Protección de secretos, entornos y archivos locales |
| `LICENSE` | Licencia MIT |

La aplicación también está preparada para leer documentos opcionales desde:

```text
src/project_docs/
```

Si esa carpeta no existe, el chequeo la reporta como una condición no crítica. Se espera que se utilice para mostrar documentos teóricos, manuales metodológicos o notas de diseño dentro de la pestaña **Proyecto**.

---

## 16. Despliegue en Streamlit Community Cloud

El repositorio ya contiene:

- `requirements.txt` con dependencias de Python;
- `packages.txt` con FFmpeg;
- plantilla pública de secretos;
- lectura de configuración mediante `st.secrets`.

Flujo de despliegue:

1. Crea una aplicación en Streamlit Community Cloud.
2. Selecciona este repositorio.
3. Usa `app.py` como archivo principal.
4. Abre la configuración avanzada o la sección **Secrets**.
5. Copia allí el contenido de tu `.streamlit/secrets.toml` local.
6. Guarda y despliega.

No subas el archivo real de secretos al repositorio.

Documentación oficial:

- [Desplegar en Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app)
- [Gestionar secretos en Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)

---

## 17. Privacidad y uso responsable

El audio optimizado, la transcripción y los prompts se envían a servicios externos de OpenAI.

Antes de procesar entrevistas, reuniones o grabaciones no públicas, considera:

- consentimiento de las personas participantes;
- base jurídica para el tratamiento de datos;
- anonimización o seudonimización;
- información sensible incluida en el audio;
- políticas institucionales de seguridad;
- condiciones de almacenamiento y tratamiento del proveedor;
- restricciones para investigaciones con poblaciones vulnerables.

No utilices el MVP para vigilancia, perfilamiento automático de personas o decisiones que produzcan efectos adversos sin revisión humana.

---

## 18. Limitaciones metodológicas

1. **No es un detector de emociones.** Las métricas acústicas son pistas exploratorias.
2. **El índice de intensidad es intraaudio.** No debe compararse directamente entre personas o grabaciones.
3. **La transcripción puede contener errores.** Nombres propios, siglas y audio ruidoso requieren revisión.
4. **La evidencia del LLM debe contrastarse.** Una estructura JSON correcta no garantiza una interpretación correcta.
5. **El filtro de relevancia no está calibrado estadísticamente.** Su umbral es una regla metodológica del MVP.
6. **Las recomendaciones son borradores.** No constituyen asesoría política automatizada.
7. **La evaluación es exploratoria.** El trabajo de grado documenta comparaciones con anotación humana en un caso de estudio; estas no equivalen a validación externa, a acuerdo entre varios codificadores ni a generalización estadística.
8. **El perfil teórico es editable.** Cambiar el prompt puede modificar sustancialmente los resultados.

---

## 19. Hoja de ruta: de prueba de concepto hacia una herramienta politológica robusta

El valor del MVP no está solo en su funcionamiento actual, sino en demostrar que es posible construir herramientas especializadas para el trabajo cotidiano de politólogos, investigadores, docentes, consultores y equipos de análisis.

### Fase 1. Robustecer el MVP

- pruebas unitarias y de integración;
- reintentos ante fallos de API;
- pipeline reanudable por etapas;
- validación formal con JSON Schema o modelos tipados;
- verificación automática de citas contra la transcripción;
- mejor registro de errores y request IDs;
- actualización automatizada de precios y modelos;
- documentación bibliográfica completa.

### Fase 2. Procesar audios largos

- fragmentación automática por duración o tamaño;
- cortes cercanos a silencios;
- solapamiento controlado entre fragmentos;
- eliminación de texto duplicado;
- corrección de offsets temporales;
- reintento exclusivo del fragmento fallido;
- recomposición final de segmentos y palabras.

### Fase 3. Entrevistas y múltiples voces

La evolución natural es pasar de discursos unipersonales a entrevistas, debates, grupos focales y reuniones.

Esto requeriría:

- diarización;
- identificación o etiquetado de hablantes;
- turnos de conversación;
- métricas por participante;
- interrupciones y solapamientos;
- preguntas y respuestas;
- comparación de marcos discursivos;
- relaciones de fuerza expresadas entre hablantes;
- trazabilidad de cada afirmación hacia un participante concreto.

Podría integrarse una solución vía API o un pipeline local con modelos de diarización.

### Fase 4. Analizar varios audios y construir corpus

- carga múltiple;
- procesamiento en lote;
- metadatos por audio;
- panel de avance;
- comparación temporal;
- análisis por actor, partido, institución o periodo;
- búsqueda transversal de citas;
- evolución de conceptos y marcos;
- identificación de recurrencias discursivas;
- exportación de matrices para análisis cuantitativo o cualitativo;
- construcción de corpus reproducibles.

### Fase 5. Múltiples proveedores de IA

Una versión más madura debería desacoplar el dominio politológico del proveedor tecnológico.

Posibles rutas:

- transcripción por API o modelos locales;
- diarización por API o modelos locales;
- LLM remoto o ejecutado en infraestructura propia;
- selección del proveedor según costo, privacidad o calidad;
- fallback automático;
- comparación entre modelos;
- ejecución completamente local para materiales sensibles.

Una arquitectura por adaptadores permitiría utilizar OpenAI, otros servicios compatibles o modelos abiertos ejecutados con herramientas como Ollama, MLX o vLLM, sin modificar el núcleo metodológico.

### Fase 6. Producto colaborativo

- autenticación;
- proyectos y usuarios;
- base de datos;
- almacenamiento de audios y resultados;
- historial de versiones;
- prompts versionados;
- colas de trabajo;
- procesamiento asíncrono;
- roles de analista, revisor y administrador;
- anotaciones humanas;
- aprobación y corrección de resultados;
- paneles comparativos;
- API propia para integraciones.

### Fase 7. Validación disciplinar

Para convertirse en un instrumento politológico y no solo en una demostración tecnológica, será necesario:

- construir un corpus anotado por expertos;
- definir protocolos de codificación;
- medir acuerdo entre codificadores;
- comparar modelos y prompts;
- estimar precisión, exhaustividad y consistencia;
- documentar sesgos y casos de fallo;
- establecer categorías que requieren revisión obligatoria;
- publicar metodología, versiones y límites de validez.

---

## 20. Visión del proyecto

Este MVP plantea una hipótesis sencilla pero relevante: la IA puede ser más útil para la Ciencia Política cuando deja de operar como un asistente genérico y se integra a flujos especializados, categorías disciplinares, evidencia trazable y mecanismos de revisión humana.

La aplicación no busca automatizar al politólogo. Busca reducir tareas repetitivas, ordenar información, revelar pasajes que merecen atención y producir insumos que hagan más eficiente el trabajo analítico.

Su potencial de evolución incluye:

- discursos políticos;
- entrevistas semiestructuradas;
- debates legislativos;
- ruedas de prensa;
- grupos focales;
- reuniones institucionales;
- comparecencias públicas;
- series históricas de intervenciones;
- corpus de campañas electorales;
- análisis comparado entre actores y periodos.

La meta de largo plazo es contribuir a una caja de herramientas computacional para politólogos que combine rigor conceptual, métodos reproducibles, IA, procesamiento de lenguaje, análisis de audio y supervisión experta.

---

## 21. Solución de problemas

### `Falta OPENAI_API_KEY`

Comprueba que exista:

```text
.streamlit/secrets.toml
```

Y que contenga:

```toml
OPENAI_API_KEY = "sk-proj-..."
```

Reinicia Streamlit después de editarlo.

### `No se encontró FFmpeg`

Instálalo y comprueba:

```bash
ffmpeg -version
```

### La API rechaza el archivo por tamaño

El M4A real superó el límite. Utiliza un audio más corto o espera la implementación de fragmentación automática.

### El modelo no está disponible

Verifica:

- nombre del modelo en `secrets.toml`;
- permisos del proyecto de OpenAI;
- facturación;
- límites de uso;
- versión del SDK.

### No aparecen perfiles de prompt

Comprueba que exista al menos un `.md` válido dentro de:

```text
src/system_prompts/
```

Y que contenga las secciones:

```markdown
## RELEVANCE_PROMPT
## ANALYSIS_PROMPT
```

### El audio no supera el filtro

No necesariamente es un error. El MVP está diseñado para detener el análisis cuando el material no contiene suficiente conflicto, actores, relaciones de fuerza o disputa de sentidos.

Revisa:

- transcripción;
- contexto inicial;
- respuesta cruda del filtro;
- criterios faltantes;
- calidad del audio.

---

## 22. Contribuciones

Las contribuciones son bienvenidas, especialmente en:

- validación politológica;
- nuevos perfiles teóricos;
- diarización;
- procesamiento por lotes;
- modelos locales;
- pruebas automatizadas;
- accesibilidad;
- privacidad;
- documentación;
- optimización de audio;
- visualizaciones comparativas.

Antes de proponer cambios grandes, conviene abrir un issue que describa:

1. problema;
2. caso de uso politológico;
3. propuesta técnica;
4. impacto metodológico;
5. criterios de validación.

---

## 23. Licencia

Este proyecto se distribuye bajo licencia MIT.

Consulta [LICENSE](LICENSE).

La atribución y los términos de distribución se conservan en el archivo `LICENSE`.

---

## 24. Referencias técnicas útiles

- [OpenAI API documentation](https://platform.openai.com/docs)
- [OpenAI Speech to Text](https://platform.openai.com/docs/guides/speech-to-text)
- [OpenAI API billing](https://help.openai.com/en/collections/3675945-understanding-openai-api-billing-and-usage)
- [Streamlit documentation](https://docs.streamlit.io/)
- [Streamlit secrets management](https://docs.streamlit.io/develop/concepts/connections/secrets-management)
- [Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud)
- [FFmpeg documentation](https://ffmpeg.org/documentation.html)
- [Librosa documentation](https://librosa.org/doc/latest/index.html)

---

## Autor

**Edison Ferney Henao Hernández**  
Estudiante de Ciencia Política. 