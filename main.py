import speech_recognition as sr
import pyttsx3
import webbrowser
import pyautogui
import subprocess
import requests

web = webbrowser
api_key = "adaa0fbe6d2419c2101a3fe0062156e9"
currency_api_key = "1d7ff3e91171eb6173910197"  # Ваш API-ключ для конвертации валют
city = "Анапа"
shutdown_chest = True

# Инициализация
engine = pyttsx3.init()
recognizer = sr.Recognizer()

def respond(text):
    engine.say(text)
    engine.runAndWait()

def listen_for_wake_word():
    with sr.Microphone() as source:
        # Автоматическая настройка уровня шума
        recognizer.adjust_for_ambient_noise(source, duration=1)
        print("Ожидаю команду 'Гладос'...")
        try:
            audio = recognizer.listen(source, timeout=3, phrase_time_limit=5)
            text = recognizer.recognize_google(audio, language='ru-RU').lower()
            return text
        except sr.WaitTimeoutError:
            return ""
        except sr.UnknownValueError:
            return ""
        except sr.RequestError:
            return ""

def get_weather(city, api_key):
    base_url = "http://api.openweathermap.org/data/2.5/weather"
    params = {
        'q': city,
        'appid': api_key,
        'units': 'metric',
        'lang': 'ru'
    }
    response = requests.get(base_url, params=params)
    data = response.json()

    if response.status_code == 200:
        temperature = data['main']['temp']
        weather_description = data['weather'][0]['description']
        respond(f"Температура в Анапе: {temperature} градусов Цельсия. Состояние: {weather_description}.")
    else:
        respond("Не удалось получить данные о погоде.")

def convert_currency(amount, from_currency, to_currency):
    url = f"https://open.er-api.com/v6/latest/{from_currency}"  # Используем новый API для конвертации валют
    response = requests.get(url)
    data = response.json()

    if response.status_code == 200 and 'rates' in data:
        rates = data['rates']
        if to_currency in rates:
            converted_amount = amount * rates[to_currency]
            return converted_amount
        else:
            respond("Неизвестная валюта для конвертации.")
            return None
    else:
        respond("Не удалось получить данные о курсах валют.")
        return None

# Основной цикл
while True:
    command = listen_for_wake_word()

    ### Рядовые фразы
    if "гладос" in command or "гладас" in command or "gladas" in command or "glados" in command or "глаша" in command: 
        respond("Да, я вас слушаю")

    if "привет" in command or "hi" in command or "hello" in command:
        respond("Hello")

    if "погода" in command:
        get_weather(city, api_key)

    ### Конвертация валют
    if "конвертируй" in command:
        try:
            parts = command.split()
            amount = float(parts[1])  # Сумма для конвертации
            from_currency = parts[2].upper()  # Исходная валюта
            to_currency = parts[4].upper()  # Целевая валюта
            converted_amount = convert_currency(amount, from_currency, to_currency)
            if converted_amount is not None:
                respond(f"{amount} {from_currency} равно {converted_amount:.2f} {to_currency}.")
        except (IndexError, ValueError):
            respond("Пожалуйста, укажите сумму и валюты в формате: конвертируй <сумма> <валюта_из> в <валюта_в>.")

    ### Сайты
    if "youtube" in command or "ютуб" in command:
        web.open_new_tab("https://www.youtube.com/") 
        respond("Открываю YouTube")

    if "avito" in command or "авито" in command or "авит" in command:
        respond("Открываю Авито")
        web.open_new_tab("https://www.avito.ru/")  

    if "музыка" in command or "музика" in command or "музыку" in command:
        respond("Открываю VK Music")
        web.open_new_tab("https://vk.com/music")

    if "почта" in command:
        web.open_new_tab("https://mail.google.com/")
    
    ### Приложения             
    if "steam" in command or "стим" in command:
        respond("Запускаю Steam")
        subprocess.run(r"D:\Program Files\Steam\steam.exe")

    if "ds" in command or "дс" in command or "discord" in command or "дискорд" in command:
        respond("Запускаю Discord")
        pyautogui.hotkey('ctrl', 'f10')

    if "запрет" in command or "zapter" in command or "dpi" in command or "дпй" in command:
        respond("Запускаю запрет")
        pyautogui.hotkey('ctrl', 'f5')

    if "telegram" in command or "telega" in command or "телеграм" in command or "тг" in command or "телега" in command:
        subprocess.run(r"C:\Program Files\WindowsApps\TelegramMessengerLLP.TelegramDesktop_5.16.5.0_x64__t4vj0pshhgkwm\Telegram.exe")

    if "браузер" in command:
        pyautogui.hotkey('win', '1')

    ### Хоткеи
    if "пробел" in command or "стоп" in command or "stop" in command or "старт" in command or "start" in command or "включи" in command:
        pyautogui.hotkey('space')

    if "звук" in command:
        pyautogui.hotkey('m')

    if "разверни" in command or "сверни" in command:
        pyautogui.hotkey('f')

    if "назад" in command or "влево" in command or "лево" in command:
        pyautogui.hotkey('left')

    if "вперед" in command or "перемотай" in command or "вправо" in command or "право" in command:
        pyautogui.hotkey('right')

    if "вверх" in command or "прибавь" in command or "добавь" in command:
        pyautogui.hotkey('up')

    if "вниз" in command or "убавь" in command or "уменьши" in command:
        pyautogui.hotkey('down')
    
    if "закрой" in command:
        pyautogui.hotkey('alt', 'f4')

    if "выключи пк" in command and shutdown_chest == True:
        subprocess.run("shutdown /s /t 2")
        respond("Функция заблокирована")

    if ("разблокировать" in command or "разблокируй" in command) and shutdown_chest == False:
        shutdown_chest = True
        respond("Функция разблокирована")

    if "запусти бота" in command or "запусти ботинка" in command or "бот" in command or "ботинок" in command:
        pyautogui.hotkey('ctrl', 'home')

    if "кликни" in command or "клик" in command or "click" in command:
        pyautogui.leftClick()

    if "голосовой ввод" in command:
        pyautogui.hotkey('win', 'h')

    if "сотри" in command:
        pyautogui.hotkey('backspace')
            
    if "сверни всё" in command:
        pyautogui.hotkey('win', 'm')
