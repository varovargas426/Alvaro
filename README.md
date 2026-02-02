# CyberStudy Coach

Desktop application (Windows) for studying cybersecurity with notes, summaries, a dictionary, a calendar, and quizzes.

## Features
- **Notes and summaries**: paste your notes, save them, and generate an automatic summary.
- **Dictionary**: store technical vocabulary and definitions.
- **Calendar**: schedule study sessions, tasks, and reminders.
- **Quizzes**: generate questions from your notes.
- **Web research**: quickly look up information online (Wikipedia as the base source).

## Requirements
- Python 3.11+
- Windows 10/11 (also works on macOS/Linux)

## Installation
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Run
```bash
python -m src.main
```

## ChatGPT setup
To enable ChatGPT features (summaries, glossary, quizzes, haiku generation), set your OpenAI API key:
```bash
setx OPENAI_API_KEY "your-api-key"
```
The app uses the `gpt-5-nano` model via the OpenAI Responses API.
Restart your terminal or VS Code after setting environment variables.

## Troubleshooting
- **Error: "Import 'requests' could not be resolved" in VS Code**
  - Activate the virtual environment and select the `./.venv` interpreter in VS Code (Ctrl+Shift+P → *Python: Select Interpreter*).
  - Make sure to run `pip install -r requirements.txt` inside the virtual environment.
 - **Web search does not return results**
  - Some networks block Wikipedia. Try a different network or check your firewall/proxy settings.
  - Make sure you can open https://en.wikipedia.org in a browser.
 - **ChatGPT buttons show errors**
  - Confirm `OPENAI_API_KEY` is set and that your key has API access.
  - Verify you are online and the OpenAI status page is healthy.

## Notes
- Data is stored in `~/.cyberstudy/data.json`.
- The AI integration is prepared to connect to a provider (for example, OpenAI) via an API key in the future.
