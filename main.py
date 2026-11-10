import speech_recognition as sr
import pyttsx3
import webbrowser
import pyautogui
import subprocess
import requests
import pystray
import sys
import threading
import os
from PIL import Image
from pystray import MenuItem, Menu
import time
import pyscreeze
import keyboard

pyautogui.useImageNotFoundException()

web = webbrowser
api_key = "adaa0fbe6d2419c2101a3fe0062156e9"
currency_api_key = "1d7ff3e91171eb6173910197"  # Ваш API-ключ для конвертации валют
city = "Анапа"
shutdown_chest = True  # True - можно выключать, False - нельзя

# Инициализация
engine = pyttsx3.init()
recognizer = sr.Recognizer()

def respond(text):
    engine.say(text)
    engine.runAndWait()

def listen_for_wake_word():
    with sr.Microphone() as source:
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
    
    try:
        response = requests.get(base_url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            temp = round(data['main']['temp'])
            weather_description = data['weather'][0]['description']
            respond(f"В {city} {temp} градуса, {weather_description}.")
        elif response.status_code == 401:
            respond("Ошибка ключа погоды. Проверьте API-ключ.")
        elif response.status_code == 404:
            respond(f"Город {city} не найден.")
        else:
            respond("Не могу получить погоду, сервер вернул ошибку.")
            
    except requests.exceptions.ConnectTimeout:
        respond("Нет соединения с сервером погоды. Проверьте интернет.")
    except requests.exceptions.ConnectionError:
        respond("Нет интернет-соединения.")
    except requests.exceptions.Timeout:
        respond("Сервер погоды слишком долго отвечает. Попробуйте позже.")
    except Exception as e:
        respond("Что-то пошло не так при запросе погоды.")
        print(f"Ошибка погоды: {e}")

def convert_currency(amount, from_currency, to_currency):
    url = f"https://open.er-api.com/v6/latest/{from_currency}"
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

def on_quit(icon, item):
    icon.stop()
    sys.exit()

# Проверяем наличие файла иконки
icon_path = "micro.png"
if not os.path.exists(icon_path):
    print(f"Файл иконки '{icon_path}' не найден. Пожалуйста, поместите его в папку с скриптом.")
    sys.exit()

# Создаем иконку
icon = pystray.Icon("Glados")
icon.icon = Image.open(icon_path)
icon.title = "Glados"
icon.menu = Menu(MenuItem("Выход", on_quit))

def start_icon():
    icon.run()

# Запускаем иконку в отдельном потоке
threading.Thread(target=start_icon, daemon=True).start()

# Основной цикл
while True:
    command = listen_for_wake_word()
    if command:
        print(f"Распознано: {command}")

    # --- Команда помощи (список всех доступных команд) ---
    if "команды" in command or "помощь" in command or "что ты умеешь" in command:
        respond("Я умею: открывать сайты и приложения, управлять музыкой, регулировать громкость, показывать погоду, конвертировать валюты, управлять микрофоном в дискорде, выключать компьютер и многое другое. Подробный список смотри в консоли.")
        print("\n=== Доступные команды Глаши ===\n")
        print("Общие: привет, погода, конвертируй [сумма] [из] в [в]")
        print("Сайты: youtube, avito, музыка, почта, yandex, twitch")
        print("Приложения: steam, ds/дискорд, telegram/тг, браузер, debian/терминал")
        print("Медиа: пауза/стоп, следующий/вперед, предыдущий/назад, громче/тише, mute, открой плеер")
        print("Дискорд: размут/включи микро, мут/выключи микро")
        print("Хоткеи: пробел/стоп/старт, звук, разверни/сверни, закрой")
        print("Системные: выключи пк, заблокируй/разблокируй выключение, перезагрузка (restart/рестарт)")
        print("Прочее: кликни, голосовой ввод, сотри, сверни всё, СПРАВОЧНЫЕ МАТЕРИАЛЫ, рик и морти")
        print("\nДля активации произнеси 'Гладос' или 'Глаша' перед командой.\n")

    # --- Рядовые фразы ---
    if any(word in command for word in ["гладос", "гладас", "gladas", "glados", "глаша"]):
        respond("Да, я вас слушаю")

    if any(word in command for word in ["привет", "hi", "hello"]):
        respond("Приветствую")

    if "погода" in command:
        get_weather(city, api_key)

    # --- Перезагрузка (поиск изображения) ---
    if any(word in command for word in ["restart", "рестарт"]):
        try:
            det = pyautogui.locateOnScreen("restart.png", grayscale=False, confidence=0.9)
            if det:
                print("НАЙДЕНО!", det)
                first_det = pyautogui.position()
                center = pyautogui.center(det)
                pyautogui.moveTo(center)
                pyautogui.click()
                time.sleep(2)
                pyautogui.moveTo(first_det)
        except pyautogui.ImageNotFoundException:
            print('Изображение restart.png не найдено...')

    # --- Конвертация валют ---
    if "конвертируй" in command:
        try:
            parts = command.split()
            amount = float(parts[1])
            from_currency = parts[2].upper()
            to_currency = parts[4].upper()
            converted_amount = convert_currency(amount, from_currency, to_currency)
            if converted_amount is not None:
                respond(f"{amount} {from_currency} равно {converted_amount:.2f} {to_currency}.")
        except (IndexError, ValueError):
            respond("Пожалуйста, укажите сумму и валюты в формате: конвертируй <сумма> <валюта_из> в <валюта_в>.")

    # --- Сайты ---
    if any(word in command for word in ["youtube", "ютуб"]):
        web.open_new_tab("https://www.youtube.com/")
        respond("Открываю YouTube")

    if any(word in command for word in ["avito", "авито", "авит"]):
        respond("Открываю Авито")
        web.open_new_tab("https://www.avito.ru/")

    if any(word in command for word in ["музыка", "музика", "музыку"]):
        respond("Открываю VK Music")
        web.open_new_tab("https://vk.com/music")

    if "почта" in command:
        web.open_new_tab("https://mail.google.com/")

    if any(word in command for word in ["yandex", "яндекс"]):
        web.open_new_tab("https://www.yandex.com/")

    if any(word in command for word in ["twitch", "твич"]):
        web.open_new_tab("https://www.twitch.tv/")

    if any(word in command for word in ["ozon", "озон"]):
        web.open_new_tab("https://www.ozon.ru/")

    if any(word in command for word in ["wb", "вб"]):
        web.open_new_tab("https://www.wb.ru/")

    # --- Приложения ---
    if any(word in command for word in ["steam", "стим"]):
        respond("Запускаю Steam")
        subprocess.run(r"D:\Program Files\Steam\steam.exe")

    if any(word in command for word in ["ds", "дс", "discord", "дискорд"]):
        respond("Запускаю Discord")
        subprocess.run(r"C:\Users\ShedowKonfas\AppData\Local\Discord\app-1.0.9252\Discord.exe")

    if any(word in command for word in ["запрет", "zapter", "dpi", "дпй"]):
        respond("Запускаю запрет")
        pyautogui.hotkey('ctrl', 'f5')

    if any(word in command for word in ["telegram", "telega", "телеграм", "тг", "телега"]):
        subprocess.run(r"C:\Users\ShedowKonfas\AppData\Roaming\Telegram Desktop\Telegram.exe")

    if "браузер" in command:
        pyautogui.hotkey('win', '1')

    if any(word in command for word in ["debian", "дебиан", "terminal", "терминал", "cmd"]):
        pyautogui.hotkey("win", "6")

    #if "тест клавы" in command:
    #    subprocess.run(r"D:\Python\KeyTest\output\KeyTester.exe")
    
    if "протокол сервер" in command:    
        os.startfile("server.lnk")



    # --- Видео и музыка (расширенное управление) ---
    # Управление плеером (открытие VLC, если установлен)
    if "открой плеер" in command or "vlc" in command:
        try:
            subprocess.Popen("C:/Program Files/VideoLAN/VLC/vlc.exe")
            respond("Запускаю VLC")
        except:
            respond("Не удалось найти VLC. Проверьте путь.")

    # Управление воспроизведением
    if "пауза" in command or "стоп" in command or "stop" in command:
        pyautogui.press('playpause')
        respond("Пауза")

    if "следующий" in command or "next" in command or "вперед" in command:
        pyautogui.press('nexttrack')
        respond("Следующий трек")

    if "предыдущий" in command or "back" in command or "назад" in command and not "перемотай" in command:
        pyautogui.press('prevtrack')
        respond("Предыдущий трек")

    # Громкость (медиа-клавиши)
    if "громче" in command or "прибавь звук" in command:
        pyautogui.press('volumeup')
        respond("Громче")

    if "тише" in command or "убавь звук" in command:
        pyautogui.press('volumedown')
        respond("Тише")

    if "выключи звук" in command or "mute" in command:
        pyautogui.press('volumemute')
        respond("Звук выключен")

    #Запуск вк микса
    if "включи микс" in command:
        try:
            det = pyautogui.locateOnScreen("mix.png", grayscale=False, confidence=0.95)
            #print("НАЙДЕНО!", det)

            center=pyautogui.center(det)
            pyautogui.moveTo(center)
            time.sleep(1)   
            pyautogui.click()
            time.sleep(1) 
        except pyautogui.ImageNotFoundException:
            print('не найдено...')

    # Открытие Рика и Морти
    if any(word in command for word in ["рик и морти"]):
        web.open_new_tab("https://rickandmorty.mult-fan.tv/")
        respond("Включаю Рика и Морти")
        try:
            os.startfile('RaM.lnk')
        except:
            pass

    # --- Хоткеи ---
    if any(word in command for word in ["пробел", "стоп"]):
        pyautogui.hotkey('space')

    if "звук" in command:
        pyautogui.hotkey('m')

    if any(word in command for word in ["разверни", "сверни"]):
        pyautogui.hotkey('f')

    if any(word in command for word in ["назад", "влево", "лево"]):
        pyautogui.hotkey('left')

    if any(word in command for word in ["вперед", "перемотай", "вправо", "право"]):
        pyautogui.hotkey('right')

    if any(word in command for word in ["вверх", "прибавь", "добавь"]):
        pyautogui.hotkey('up')

    if any(word in command for word in ["вниз", "убавь", "уменьши"]):
        pyautogui.hotkey('down')

    if "закрой" in command:
        pyautogui.hotkey('alt', 'f4')

    # --- Выключение ПК (с защитой) ---
    if "выключи пк" in command:
        if shutdown_chest:
            subprocess.run("shutdown /s /t 2")
            respond("Выключаю компьютер")
        else:
            respond("Функция выключения заблокирована")

    if ("разблокировать" in command or "разблокируй" in command) and not shutdown_chest:
        shutdown_chest = True
        respond("Функция выключения разблокирована")

    if "заблокируй выключение" in command:
        shutdown_chest = False
        respond("Функция выключения заблокирована")

    if any(word in command for word in ["запусти бота", "запусти ботинка", "бот", "ботинок"]):
        pyautogui.hotkey('ctrl', 'home')

    if any(word in command for word in ["кликни", "клик", "click"]):
        pyautogui.leftClick()

    if "голосовой ввод" in command:
        pyautogui.hotkey('win', 'h')

    if "сотри" in command:
        pyautogui.hotkey('backspace')

    if "сверни всё" in command:
        pyautogui.hotkey('win', 'm')

    if "сверни" in command:
        pyautogui.hotkey('win', 'down')

    if "Next окно" in command:
        pyautogui.hotkey('ctrl', 'shift', 'esc')

    # --- Файлы ---
    if "СПРАВОЧНЫЕ МАТЕРИАЛЫ" in command:
        webbrowser.open('teor.pdf')

    # --- Discord (микрофон) ---
    if any(word in command for word in ["размут", "unmute", "включи микро", "включи микрофон"]):
        try:
            det_unmute = pyautogui.locateOnScreen("unmute.png", grayscale=False, confidence=0.9)
            if det_unmute:
                print("НАЙДЕНО!", det_unmute)
                first_det_unmute = pyautogui.position()
                center = pyautogui.center(det_unmute)
                pyautogui.moveTo(center)
                pyautogui.click()
                time.sleep(2)
                pyautogui.moveTo(first_det_unmute)
        except pyautogui.ImageNotFoundException:
            print('Изображение unmute.png не найдено...')

    if any(word in command for word in ["мут", "mute", "выключи микро", "выключи микрофон"]):
        try:
            det_mute = pyautogui.locateOnScreen("mute.png", grayscale=False, confidence=0.9)
            if det_mute:
                print("НАЙДЕНО!", det_mute)
                first_det_mute = pyautogui.position()
                center = pyautogui.center(det_mute)
                pyautogui.moveTo(center)
                pyautogui.click()
                time.sleep(2)
                pyautogui.moveTo(first_det_mute)
        except pyautogui.ImageNotFoundException:
            print('Изображение mute.png не найдено...')