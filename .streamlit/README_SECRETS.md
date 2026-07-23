# Configuracion de secretos

## Desarrollo local

1. Copia el archivo de ejemplo:

   ```bash
   cp .streamlit/secrets.toml.example .streamlit/secrets.toml
   ```

2. Reemplaza el valor de `OPENAI_API_KEY` por tu clave real.
3. Reinicia Streamlit después de modificar los secretos.

El archivo `.streamlit/secrets.toml` está ignorado por Git. El archivo
`.streamlit/secrets.toml.example` sí debe subirse al repositorio.

## Streamlit Community Cloud

Copia el contenido de tu archivo local `.streamlit/secrets.toml` en la
sección **Secrets** de la configuración de la aplicación desplegada. No
subas el archivo real al repositorio.

## Si el secreto ya fue agregado alguna vez a Git

```bash
git rm --cached .streamlit/secrets.toml
git commit -m "Stop tracking local Streamlit secrets"
```

Después, rota la clave expuesta desde el panel de OpenAI.
