# CyberStudy Coach

Aplicación de escritorio (Windows) para estudiar ciberseguridad con notas, resúmenes, diccionario, calendario y quizzes.

## Funcionalidades
- **Notas y resúmenes**: pega tus notas, guarda, y genera un resumen automático.
- **Diccionario**: almacena vocabulario técnico y definiciones.
- **Calendario**: agenda sesiones, tareas y recordatorios.
- **Quizzes**: genera preguntas a partir de tus notas.
- **Investigación web**: consulta rápidamente información en línea (Wikipedia como fuente base).

## Requisitos
- Python 3.11+
- Windows 10/11 (funciona también en macOS/Linux)

## Instalación
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Ejecución
```bash
python -m src.main
```

## Solución de problemas
- **Error: "Import 'requests' could not be resolved" en VS Code**
  - Activa el entorno virtual y selecciona el intérprete de `./.venv` en VS Code (Ctrl+Shift+P → *Python: Select Interpreter*).
  - Asegúrate de ejecutar `pip install -r requirements.txt` dentro del entorno virtual.

## Notas
- Los datos se almacenan en `~/.cyberstudy/data.json`.
- La integración de IA está preparada para conectarse a un proveedor (por ejemplo, OpenAI) mediante una clave de API en el futuro.
