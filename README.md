# GLaDOS — голосовой ассистент для Windows

Слушает кодовое слово («Гладос» / «Глаша»), выполняет команды и **отвечает голосом**.
Работает в трее, команды описаны в `config.json` и `commands.py`.

## Быстрый старт

```bat
python -m venv venv
venv\Scripts\pip install -r requirements.txt
start.cmd
```

- `start.cmd` — обычный запуск (без консоли);
- `start_debug.cmd` — запуск с консолью и подробным логом;
- `build.bat` — собрать `Glados.exe` в `dist\`.

Проверка без микрофона:

```bat
venv\Scripts\python main.py --test-tts
venv\Scripts\python main.py --cmd "погода"
venv\Scripts\python main.py --list-mics
venv\Scripts\python main.py --list-voices
venv\Scripts\python main.py --list-commands
venv\Scripts\python -m unittest discover -s tests -v
```

## Настройки

- `config.json` — общие настройки: кодовые слова, голос, город, пути к приложениям, хоткеи.
- `config.local.json` — личные данные (API-ключи). В git не попадает.
- `logs/glados.log` — журнал: что услышано, какая команда сработала, каким движком озвучено.

Ключевые параметры:

| Параметр | Значение |
|---|---|
| `assistant.wake_words` | варианты кодового слова |
| `assistant.followup_seconds` | сколько секунд после ответа команды можно говорить без кодового слова |
| `assistant.mic_name` | часть имени микрофона (`""` — устройство по умолчанию) |
| `voice.engine` | `auto` (edge → gTTS → pyttsx3), `edge`, `gtts` или `pyttsx3` |
| `voice.edge_voice` | голос edge-tts, по умолчанию `ru-RU-SvetlanaNeural` |
| `api.openweather_key` | ключ OpenWeather (погода) |

## Как это работает

1. Микрофон открыт постоянно, калибровка шума — один раз при старте.
2. Кодовое слово ищется нечётко (`wake.py`), потому что распознавание редко слышит «Гладос» идеально.
3. После активации ассистент отвечает и держит диалоговое окно `followup_seconds` секунд:
   команды можно говорить без кодового слова. «Хватит» / «отбой» — выйти из диалога.
4. Озвучка идёт в отдельном потоке (`tts.py`), микрофон на время речи ставится на паузу,
   чтобы ассистент не слышал сам себя.
5. Команды — таблица с приоритетами (`commands.py`): срабатывает только первое совпадение.

## Если молчит

1. Откройте `start_debug.cmd` и посмотрите `logs/glados.log`.
2. Проверьте озвучку: `venv\Scripts\python main.py --test-tts`.
   - в логе `Озвучено движком gtts` — звук должен быть;
   - `Ни один движок озвучки не сработал` — нет доступа к edge-tts/gTTS. Помогает запущенный
     обход DPI или установка русского голоса SAPI (Параметры → Время и язык → Речь).
3. Проверьте микрофон: `--list-mics`, при необходимости впишите часть имени в
   `assistant.mic_name` (например `G435`).
4. Если не слышит кодовое слово — добавьте свой вариант в `assistant.wake_words`.
