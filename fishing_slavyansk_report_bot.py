import asyncio
import os
import logging
import aiohttp
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo,
    ReplyKeyboardRemove
)
from aiogram.client.default import DefaultBotProperties

# ============ FLASK ДЛЯ RENDER ============
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "🎣 Бот отчётов о рыбалке работает!"

@app.route('/health')
def health():
    return "OK", 200

# ==================== KEEP-ALIVE ====================
async def ping_server():
    """Периодически отправляет запросы к серверу, чтобы он не засыпал"""
    
    SERVICE_URL = "https://fishing-slavyansk-report-bot.onrender.com"
    ping_count = 0
    
    print(f"🔄 Keep-alive запущен. Будет обращаться к: {SERVICE_URL}")
    
    while True:
        try:
            ping_count += 1
            current_time = datetime.now().strftime("%H:%M:%S")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(SERVICE_URL, timeout=30) as response:
                    status = response.status
                    
                    if status == 200:
                        print(f"✅ [{current_time}] Keep-alive #{ping_count}: Сервер отвечает")
                    else:
                        print(f"⚠️ [{current_time}] Keep-alive #{ping_count}: Статус {status}")
        
        except asyncio.TimeoutError:
            current_time = datetime.now().strftime("%H:%M:%S")
            print(f"⏱️ [{current_time}] Keep-alive #{ping_count}: Таймаут")
        
        except aiohttp.ClientConnectorError:
            current_time = datetime.now().strftime("%H:%M:%S")
            print(f"🌐 [{current_time}] Keep-alive #{ping_count}: Не удалось подключиться")
        
        except Exception as e:
            current_time = datetime.now().strftime("%H:%M:%S")
            error_msg = str(e)[:50]
            print(f"❌ [{current_time}] Keep-alive #{ping_count}: Ошибка - {error_msg}")
        
        await asyncio.sleep(840)

# ==================== НАСТРОЙКИ ====================
TOKEN = "8406827750:AAFj6wZlT0a6PKnShyXstrLZiguOddDu-VE"
MAIN_CHAT_ID = -1001790011004  # Основной чат fishing_slavyansk
REPORT_THREAD_ID = 15708       # Тема для отчётов
COMMENTS_THREAD_ID = 1         # Тема №1 для комментариев
# ===================================================

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== КЛАВИАТУРЫ ====================
start_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Подготовить отчёт о рыбалке")]],
    resize_keyboard=True
)

def get_back_kb(additional_buttons=None, include_restart=True):
    """Создает клавиатуру с кнопкой Назад и Начать сначала"""
    keyboard = []
    if additional_buttons:
        if isinstance(additional_buttons[0], list):
            keyboard.extend(additional_buttons)
        else:
            keyboard.append(additional_buttons)
    
    row = [KeyboardButton(text="◀️ Назад")]
    if include_restart:
        row.append(KeyboardButton(text="🔄 Начать сначала"))
    keyboard.append(row)
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

date_kb = get_back_kb([
    [KeyboardButton(text="Сегодня"), KeyboardButton(text="Вчера")],
    [KeyboardButton(text="Ввести вручную")]
])

water_type_kb = get_back_kb([
    KeyboardButton(text="Платник"), 
    KeyboardButton(text="Бесплатник")
])

skip_kb_with_back = get_back_kb([KeyboardButton(text="Пропустить")])
back_kb_only = get_back_kb()

# ==================== СОСТОЯНИЯ ====================
class ReportStates(StatesGroup):
    date = State()
    water_type = State()
    place = State()
    location = State()
    catch = State()
    tackle = State()
    extra = State()
    media = State()
    preview = State()

# ==================== СЛУЖЕБНОЕ ====================
async def save_msg(state: FSMContext, message: types.Message):
    """Сохраняет ID сообщения для текущего шага"""
    data = await state.get_data()
    current_state = await state.get_state()
    
    step_messages = data.get("step_messages", {})
    step_msg_ids = step_messages.get(current_state, [])
    step_msg_ids.append(message.message_id)
    
    step_messages[current_state] = step_msg_ids
    await state.update_data(step_messages=step_messages)

async def delete_step_messages(user_id: int, state: FSMContext, step_state: str = None):
    """Удаляет сообщения определенного шага или всех шагов"""
    data = await state.get_data()
    step_messages = data.get("step_messages", {})
    media_message_ids = data.get("media_message_ids", [])
    status_msg_id = data.get("status_msg_id")
    
    deleted_count = 0
    
    if status_msg_id:
        try:
            await bot.delete_message(user_id, status_msg_id)
            deleted_count += 1
        except:
            pass
    
    if step_state:
        if step_state in step_messages:
            for mid in step_messages[step_state]:
                try:
                    await bot.delete_message(user_id, mid)
                    deleted_count += 1
                except:
                    pass
            step_messages[step_state] = []
    else:
        for step_state_key, msg_ids in step_messages.items():
            for mid in msg_ids:
                try:
                    await bot.delete_message(user_id, mid)
                    deleted_count += 1
                except:
                    pass
        
        for mid in media_message_ids:
            try:
                await bot.delete_message(user_id, mid)
                deleted_count += 1
            except:
                pass
        
        step_messages = {}
        media_message_ids = []
        status_msg_id = None
    
    await state.update_data(
        step_messages=step_messages, 
        media_message_ids=media_message_ids,
        status_msg_id=status_msg_id
    )
    return deleted_count

async def show_start(user_id: int, state: FSMContext = None):
    """Показывает стартовое сообщение"""
    try:
        msg = await bot.send_message(
            chat_id=user_id,
            text="🎣 **Бот отчётов о рыбалке - Славянский район**\n\n"
                 "📋 **Новая система:**\n"
                 "• Отчёты публикуются в теме 15708\n"
                 "• Автоматически пересылаются в тему для комментариев\n"
                 "• Комментировать можно сразу в теме №1\n\n"
                 "⚠️ **Что нужно сделать:**\n\n"
                 "1️⃣ Нажмите «Подготовить отчёт о рыбалке»\n"
                 "2️⃣ Укажите данные о рыбалке\n"
                 "3️⃣ Добавьте фото/видео\n"
                 "4️⃣ Отправьте отчёт\n\n"
                 "📍 **Комментарии автоматически:**\n"
                 "Отчёт сразу появится в теме для обсуждения!",
            reply_markup=start_kb
        )
        if state:
            await save_msg(state, msg)
        return msg
    except:
        return None

async def update_buttons_message(user_id: int, chat_id: int, state: FSMContext, media_count: int):
    """Обновляет сообщение с кнопками"""
    data = await state.get_data()
    status_msg_id = data.get("status_msg_id")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁️ Предпросмотр отчёта", callback_data="preview_report")],
        [InlineKeyboardButton(text="📤 Отправить отчёт", callback_data="send_report")]
    ])
    
    if status_msg_id:
        try:
            await bot.delete_message(chat_id, status_msg_id)
        except:
            pass
    
    msg = await bot.send_message(
        chat_id=chat_id,
        text=f"✅ **Загружено {media_count} медиа. Добавляйте ещё или нажмите «👁️ Предпросмотр отчёта».**",
        reply_markup=kb
    )
    
    await save_msg(state, msg)
    await state.update_data(status_msg_id=msg.message_id)
    return msg

# ==================== ОБРАБОТКА КНОПКИ "НАЗАД" ====================
@dp.message(lambda m: m.text == "◀️ Назад")
async def go_back(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    current_state = await state.get_state()
    
    await save_msg(state, message)
    
    state_transitions = {
        ReportStates.date.state: None,
        ReportStates.water_type.state: ReportStates.date.state,
        ReportStates.place.state: ReportStates.water_type.state,
        ReportStates.location.state: ReportStates.place.state,
        ReportStates.catch.state: ReportStates.location.state,
        ReportStates.tackle.state: ReportStates.catch.state,
        ReportStates.extra.state: ReportStates.tackle.state,
        ReportStates.media.state: ReportStates.extra.state,
        ReportStates.preview.state: ReportStates.media.state,
    }
    
    previous_state = state_transitions.get(current_state)
    
    if current_state == ReportStates.date.state:
        await delete_step_messages(user_id, state)
        await state.clear()
        await show_start(user_id, state)
        
    elif previous_state:
        await delete_step_messages(user_id, state, current_state)
        await delete_step_messages(user_id, state, previous_state)
        await state.set_state(previous_state)
        
        if previous_state == ReportStates.date.state:
            msg = await message.answer(
                "📅 **Дата рыбалки:**\n\nВыберите или введите дату",
                reply_markup=date_kb
            )
            await save_msg(state, msg)
            
        elif previous_state == ReportStates.water_type.state:
            msg = await message.answer(
                "💰 **Тип водоёма:**\n\n• Платник\n• Бесплатник",
                reply_markup=water_type_kb
            )
            await save_msg(state, msg)
            
        elif previous_state == ReportStates.place.state:
            msg = await message.answer(
                "📍 **Укажите водоём ловли:**",
                reply_markup=back_kb_only
            )
            await save_msg(state, msg)
            
        elif previous_state == ReportStates.location.state:
            msg = await message.answer(
                "📍 **Геопозиция (можно пропустить):**",
                reply_markup=skip_kb_with_back
            )
            await save_msg(state, msg)
            
        elif previous_state == ReportStates.catch.state:
            msg = await message.answer(
                "🎣 **Что поймали?:**",
                reply_markup=back_kb_only
            )
            await save_msg(state, msg)
            
        elif previous_state == ReportStates.tackle.state:
            msg = await message.answer(
                "🪝 **Снасти и наживка (можно пропустить):**",
                reply_markup=skip_kb_with_back
            )
            await save_msg(state, msg)
            
        elif previous_state == ReportStates.extra.state:
            msg = await message.answer(
                "📝 **Доп. информация (можно пропустить):**",
                reply_markup=skip_kb_with_back
            )
            await save_msg(state, msg)
            
        elif previous_state == ReportStates.media.state:
            await state.update_data(
                media=[],
                media_message_ids=[],
                status_msg_id=None
            )
            msg = await message.answer(
                "📸 **Добавьте фото или видео:**",
                reply_markup=back_kb_only
            )
            await save_msg(state, msg)
    
    else:
        await delete_step_messages(user_id, state)
        await state.clear()
        await show_start(user_id, state)

# ==================== ОБРАБОТКА КНОПКИ "НАЧАТЬ СНАЧАЛА" ====================
@dp.message(lambda m: m.text == "🔄 Начать сначала")
async def restart_report(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    await save_msg(state, message)
    deleted = await delete_step_messages(user_id, state)
    print(f"🔄 Начать сначала: удалено {deleted} сообщений")
    
    await state.clear()
    await show_start(user_id, state)

# ==================== START ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await delete_step_messages(message.from_user.id, state)
    await state.clear()
    await show_start(message.from_user.id, state)

@dp.message(lambda m: m.text == "Подготовить отчёт о рыбалке")
async def start_report(message: types.Message, state: FSMContext):
    await delete_step_messages(message.from_user.id, state)
    await state.clear()
    
    await save_msg(state, message)

    msg = await message.answer(
        "📅 **Дата рыбалки (обязательно):**\n\n"
        "Можете выбрать:\n"
        "• «Сегодня» – автоматически поставит текущую дату\n"
        "• «Вчера» – автоматически поставит вчерашнюю дату\n"
        "• «Ввести вручную» – напишите дату в формате ДД.ММ.ГГГГ (например, 26.01.2026)",
        reply_markup=date_kb
    )
    await save_msg(state, msg)
    await state.set_state(ReportStates.date)

# ==================== ШАГИ ====================
@dp.message(ReportStates.date)
async def step_date(message: types.Message, state: FSMContext):
    if message.text in ["◀️ Назад", "🔄 Начать сначала"]:
        return
    
    await save_msg(state, message)
    
    if message.text == "Сегодня":
        date_str = datetime.now().strftime("%d.%m.%Y")
        await state.update_data(date=date_str)
        await process_date_step(message, state)
        
    elif message.text == "Вчера":
        yesterday = datetime.now() - timedelta(days=1)
        date_str = yesterday.strftime("%d.%m.%Y")
        await state.update_data(date=date_str)
        await process_date_step(message, state)
        
    elif message.text == "Ввести вручную":
        msg = await message.answer(
            "✏️ Введите дату в формате ДД.ММ.ГГГГ (например: 26.01.2026):",
            reply_markup=get_back_kb([], include_restart=True)
        )
        await save_msg(state, msg)
        return
        
    elif not message.text.strip():
        msg = await message.answer("❌ Дата обязательна", reply_markup=date_kb)
        await save_msg(state, msg)
        return
        
    else:
        date_text = message.text.strip()
        try:
            datetime.strptime(date_text, "%d.%m.%Y")
            await state.update_data(date=date_text)
            await process_date_step(message, state)
        except ValueError:
            msg = await message.answer(
                "❌ Неверный формат даты!\n"
                "Используйте формат ДД.ММ.ГГГГ (например: 26.01.2026)",
                reply_markup=date_kb
            )
            await save_msg(state, msg)

async def process_date_step(message: types.Message, state: FSMContext):
    data = await state.get_data()
    msg = await message.answer(f"✅ Дата сохранена: {data['date']}")
    await save_msg(state, msg)
    
    msg = await message.answer(
        "💰 **Тип водоёма (обязательно):**\n\n"
        "• Платник – платная рыбалка\n"
        "• Бесплатник – бесплатная рыбалка",
        reply_markup=water_type_kb
    )
    await save_msg(state, msg)
    await state.set_state(ReportStates.water_type)

@dp.message(ReportStates.water_type)
async def step_water_type(message: types.Message, state: FSMContext):
    if message.text in ["◀️ Назад", "🔄 Начать сначала"]:
        return
    
    await save_msg(state, message)
    
    if message.text not in ["Платник", "Бесплатник"]:
        msg = await message.answer(
            "❌ Пожалуйста, выберите один из вариантов:",
            reply_markup=water_type_kb
        )
        await save_msg(state, msg)
        return
    
    await state.update_data(water_type=message.text)
    
    msg = await message.answer("✅ Тип водоёма сохранен", reply_markup=ReplyKeyboardRemove())
    await save_msg(state, msg)
    
    msg = await message.answer(
        "📍 **Укажите водоём ловли (обязательно):**\n\n"
        "Примеры:\n"
        "• Река Кубань\n"
        "• 28 канал\n"
        "• Лиман Фуртовый\n"
        "• Пруд 'Золотая рыбка'",
        reply_markup=back_kb_only
    )
    await save_msg(state, msg)
    await state.set_state(ReportStates.place)

@dp.message(ReportStates.place)
async def step_place(message: types.Message, state: FSMContext):
    if message.text in ["◀️ Назад", "🔄 Начать сначала"]:
        return
    
    await save_msg(state, message)
    if not message.text.strip():
        msg = await message.answer("❌ Водоём обязателен", reply_markup=back_kb_only)
        await save_msg(state, msg)
        return
    await state.update_data(place=message.text.strip())

    msg = await message.answer(
        "📍 **Укажите геопозицию рыболовной точки (можно пропустить):**\n\n"
        "Можете указать:\n"
        "• Координаты точки\n"
        "• Точку на карте\n"
        "• Ориентировочное место ловли\n\n"
        "Примеры:\n"
        "• Координаты: 45.123456, 38.123456\n"
        "• Отправьте точку на карте\n"
        "• Район хутора Верхний\n\n"
        "Или нажмите «Пропустить»",
        reply_markup=skip_kb_with_back
    )
    await save_msg(state, msg)
    await state.set_state(ReportStates.location)

@dp.message(ReportStates.location)
async def step_location(message: types.Message, state: FSMContext):
    if message.text in ["◀️ Назад", "🔄 Начать сначала"]:
        return
    
    await save_msg(state, message)
    
    if message.text == "Пропустить":
        await state.update_data(location=None)
    else:
        await state.update_data(location=message.text.strip())
    
    msg = await message.answer(
        "🎣 **Что поймали? (обязательно):**\n\n"
        "Можно указать количество и вес по желанию.\n\n"
        "Примеры:\n"
        "• Судак – 3 шт\n"
        "• Карп – 2 шт\n"
        "• Окунь, плотва, карась\n"
        "• Щука + окунь 5 шт",
        reply_markup=back_kb_only
    )
    await save_msg(state, msg)
    await state.set_state(ReportStates.catch)

@dp.message(ReportStates.catch)
async def step_catch(message: types.Message, state: FSMContext):
    if message.text in ["◀️ Назад", "🔄 Начать сначала"]:
        return
    
    await save_msg(state, message)
    if not message.text.strip():
        msg = await message.answer("❌ Улов обязателен", reply_markup=back_kb_only)
        await save_msg(state, msg)
        return
    await state.update_data(catch=message.text.strip())

    msg = await message.answer(
        "🪝 **Снасти и наживка (можно пропустить):**\n\n"
        "Примеры:\n"
        "• Спиннинг, плетёнка 0.14\n"
        "• Фидер, леска 0.25\n"
        "• Наживка: червь, кукуруза\n"
        "• Прикормка самодельная",
        reply_markup=skip_kb_with_back
    )
    await save_msg(state, msg)
    await state.set_state(ReportStates.tackle)

@dp.message(ReportStates.tackle)
async def step_tackle(message: types.Message, state: FSMContext):
    if message.text in ["◀️ Назад", "🔄 Начать сначала"]:
        return
    
    await save_msg(state, message)
    await state.update_data(tackle=None if message.text == "Пропустить" else message.text)

    msg = await message.answer(
        "📝 **Дополнительная информация (можно пропустить):**\n\n"
        "Можно указать:\n"
        "✓ Погодные условия\n"
        "✓ Время ловли\n"
        "✓ Особенности клёва\n"
        "✓ Интересные моменты\n"
        "✓ Советы другим рыбакам\n"
        "✓ **Для платников:** цены, режим работы, удобства",
        reply_markup=skip_kb_with_back
    )
    await save_msg(state, msg)
    await state.set_state(ReportStates.extra)

@dp.message(ReportStates.extra)
async def step_extra(message: types.Message, state: FSMContext):
    if message.text in ["◀️ Назад", "🔄 Начать сначала"]:
        return
    
    await save_msg(state, message)
    await state.update_data(extra=None if message.text == "Пропустить" else message.text)

    msg = await message.answer(
        "📸 **Добавьте фото или видео (обязательно):**\n\n"
        "Можно отправить:\n"
        "✅ Фото улова\n"
        "✅ Видео процесса ловли\n"
        "✅ Фото места рыбалки\n"
        "✅ Видео с поклёвкой\n\n"
        "📌 Нужно хотя бы одно фото или видео.\n"
        "📌 Можно отправить несколько файлов.",
        reply_markup=back_kb_only
    )
    await save_msg(state, msg)

    await state.update_data(
        media=[],
        status_msg_id=None,
        media_message_ids=[]
    )
    await state.set_state(ReportStates.media)

# ==================== МЕДИА ====================
media_group_cache = {}

@dp.message(ReportStates.media)
async def step_media(message: types.Message, state: FSMContext):
    if message.text in ["◀️ Назад", "🔄 Начать сначала"]:
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    data = await state.get_data()
    media = data.get("media", [])
    media_message_ids = data.get("media_message_ids", [])
    
    if message.photo or message.video:
        if message.photo:
            media.append(InputMediaPhoto(media=message.photo[-1].file_id))
        elif message.video:
            media.append(InputMediaVideo(media=message.video.file_id))
        
        media_message_ids.append(message.message_id)
        
        await state.update_data(
            media=media,
            media_message_ids=media_message_ids
        )
        
        if message.media_group_id:
            if user_id not in media_group_cache:
                media_group_cache[user_id] = {}
            
            if message.media_group_id not in media_group_cache[user_id]:
                media_group_cache[user_id][message.media_group_id] = {
                    'count': 0,
                    'timer': None
                }
            
            media_group_cache[user_id][message.media_group_id]['count'] += 1
            
            if media_group_cache[user_id][message.media_group_id]['timer']:
                media_group_cache[user_id][message.media_group_id]['timer'].cancel()
            
            timer = asyncio.create_task(
                update_buttons_after_delay(user_id, chat_id, state, len(media), message.media_group_id)
            )
            media_group_cache[user_id][message.media_group_id]['timer'] = timer
        else:
            await update_buttons_message(user_id, chat_id, state, len(media))
    
    else:
        msg = await message.answer("❌ Здесь можно отправлять только фото или видео", reply_markup=back_kb_only)
        await save_msg(state, msg)

async def update_buttons_after_delay(user_id: int, chat_id: int, state: FSMContext, media_count: int, media_group_id: str):
    """Обновляет кнопки после задержки для медиа-групп"""
    await asyncio.sleep(0.5)
    
    if user_id in media_group_cache and media_group_id in media_group_cache[user_id]:
        await update_buttons_message(user_id, chat_id, state, media_count)
        
        del media_group_cache[user_id][media_group_id]
        if not media_group_cache[user_id]:
            del media_group_cache[user_id]

# ==================== ПРЕДПРОСМОТР ОТЧЁТА ====================
@dp.callback_query(lambda c: c.data == "preview_report")
async def preview_report(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    
    data = await state.get_data()
    user = cb.from_user
    media = data.get("media", [])

    if not media:
        await cb.answer("❌ Нет медиа для предпросмотра!", show_alert=True)
        return

    link = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"

    text = (
        "📋 **ПРЕДПРОСМОТР ОТЧЁТА**\n\n"
        f"👤 **Автор:** {user.full_name}\n"
        f"📅 **Дата рыбалки:** {data['date']}\n"
        f"💰 **Тип водоёма:** {data.get('water_type', 'Не указано')}\n"
        f"📍 **Водоём:** {data['place']}\n"
    )
    
    if data.get("location"):
        text += f"📍 **Геопозиция:** {data['location']}\n"
    
    text += f"🎣 **Улов:** {data['catch']}\n"
    
    if data.get("tackle"):
        text += f"🪝 **Снасти/наживка:** {data['tackle']}\n"
    if data.get("extra"):
        text += f"📝 **Доп. информация:** {data['extra']}\n"
    
    text += f"\n📸 **Медиафайлов:** {len(media)}\n\n"
    
    preview_text_msg = await cb.message.answer(text)
    await save_msg(state, preview_text_msg)
    
    if media:
        first_group = []
        for m in media[:1]:
            if isinstance(m, InputMediaPhoto):
                first_group.append(InputMediaPhoto(media=m.media))
            elif isinstance(m, InputMediaVideo):
                first_group.append(InputMediaVideo(media=m.media))
        
        try:
            sent_messages = await bot.send_media_group(
                chat_id=cb.message.chat.id,
                media=first_group
            )
            for msg in sent_messages:
                await save_msg(state, msg)
        except:
            pass
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить отправку", callback_data="send_report")],
        [InlineKeyboardButton(text="✏️ Редактировать отчёт", callback_data="edit_report")]
    ])
    
    buttons_msg = await cb.message.answer("**Выберите действие:**", reply_markup=kb)
    await save_msg(state, buttons_msg)
    
    await state.set_state(ReportStates.preview)

@dp.callback_query(lambda c: c.data == "edit_report")
async def edit_report(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    
    await state.set_state(ReportStates.media)
    
    data = await state.get_data()
    media = data.get("media", [])
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁️ Предпросмотр отчёта", callback_data="preview_report")],
        [InlineKeyboardButton(text="📤 Отправить отчёт", callback_data="send_report")]
    ])
    
    try:
        await cb.message.delete()
    except:
        pass
    
    msg = await cb.message.answer(
        f"✅ **Загружено {len(media)} медиа. Добавляйте ещё или нажмите «👁️ Предпросмотр отчёта».**",
        reply_markup=kb
    )
    await save_msg(state, msg)
    await state.update_data(status_msg_id=msg.message_id)

# ==================== ОТПРАВКА ОТЧЁТА ====================
@dp.callback_query(lambda c: c.data == "send_report")
async def send_report(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    
    data = await state.get_data()
    user = cb.from_user
    media = data.get("media", [])

    if not media:
        await cb.answer("❌ Сначала отправьте хотя бы одно фото или видео!", show_alert=True)
        return

    link = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"

    # Формируем финальный текст отчета
    text = (
        f"👤 <b>Автор:</b> <a href='{link}'>{user.full_name}</a>\n"
        f"📅 <b>Дата рыбалки:</b> {data['date']}\n"
        f"💰 <b>Тип водоёма:</b> {data.get('water_type', 'Не указано')}\n"
        f"📍 <b>Водоём:</b> {data['place']}\n"
    )
    
    if data.get("location"):
        text += f"📍 <b>Геопозиция:</b> {data['location']}\n"
    
    text += f"🎣 <b>Улов:</b> {data['catch']}\n"
    
    if data.get("tackle"):
        text += f"🪝 <b>Снасти/наживка:</b> {data['tackle']}\n"
    if data.get("extra"):
        text += f"📝 <b>Доп. информация:</b> {data['extra']}\n"

    print(f"✅ Отправка отчёта в тему {REPORT_THREAD_ID}...")
    
    try:
        # 1. Отправляем медиагруппу в тему для отчётов (15708)
        media[0].caption = text
        
        sent_reports = await bot.send_media_group(
            chat_id=MAIN_CHAT_ID,
            media=media,
            message_thread_id=REPORT_THREAD_ID
        )
        
        report_message_id = sent_reports[0].message_id
        
        print(f"✅ Отчёт отправлен в тему {REPORT_THREAD_ID}, ID сообщения: {report_message_id}")
        
        # 2. ДЕЛАЕМ FORWARD в тему для комментариев (1)
        print(f"🔄 Делаем forward в тему {COMMENTS_THREAD_ID}...")
        
        forwarded_msg = await bot.forward_message(
            chat_id=MAIN_CHAT_ID,
            message_thread_id=COMMENTS_THREAD_ID,
            from_chat_id=MAIN_CHAT_ID,
            message_id=report_message_id
        )
        
        print(f"✅ Forward сделан в тему {COMMENTS_THREAD_ID}, ID: {forwarded_msg.message_id}")
        
        # 3. Редактируем forward, добавляем подпись "📝 Новый отчёт:"
        try:
            if forwarded_msg.caption:
                new_caption = f"📝 <b>Новый отчёт:</b>\n\n{forwarded_msg.caption}"
            else:
                new_caption = "📝 <b>Новый отчёт</b>"
            
            await bot.edit_message_caption(
                chat_id=MAIN_CHAT_ID,
                message_thread_id=COMMENTS_THREAD_ID,
                message_id=forwarded_msg.message_id,
                caption=new_caption,
                parse_mode="HTML"
            )
            print(f"✅ Добавлена подпись '📝 Новый отчёт:'")
        except Exception as e:
            print(f"⚠️ Не удалось добавить подпись: {e}")
        
        # 4. Получаем username бота
        bot_info = await bot.get_me()
        bot_username = bot_info.username
        
        # 5. Создаём кнопку "Отправить отчёт" (ТОЛЬКО ОДНУ!)
        chat_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Отправить отчёт",
                    url=f"https://t.me/{bot_username}?start=from_chat"
                )
            ]
        ])
        
        # 6. Отправляем кнопку под отчётом в теме 15708
        await bot.send_message(
            chat_id=MAIN_CHAT_ID,
            text="────────────────",
            reply_markup=chat_kb,
            message_thread_id=REPORT_THREAD_ID
        )
        
        print(f"✅ Кнопка '📤 Отправить отчёт' добавлена в тему {REPORT_THREAD_ID}")
        print(f"✅ Отчёт автоматически переслан в тему {COMMENTS_THREAD_ID} для комментариев")
        
        # 7. Отправляем сообщение об успехе пользователю
        success_msg = await bot.send_message(
            chat_id=user.id,
            text="✅ Отчёт успешно опубликован!\n\n"
                 f"📍 **Опубликовано в:** тема {REPORT_THREAD_ID}\n"
                 f"💬 **Для комментариев:** тема {COMMENTS_THREAD_ID}\n\n"
                 f"📝 Отчёт автоматически переслан в тему для обсуждения.\n"
                 f"Теперь все могут комментировать ваш улов!"
        )
        await save_msg(state, success_msg)
        
    except Exception as e:
        print(f"❌ Ошибка отправки отчёта: {e}")
        await cb.answer("❌ Ошибка при отправке отчёта", show_alert=True)
        return

    # 8. Очищаем состояние
    await delete_step_messages(user.id, state)
    await state.clear()
    
    # 9. Показываем стартовое сообщение
    await show_start(user.id, state)

# ==================== ЗАПУСК FLASK ====================
def run_flask_server():
    """Запускает Flask сервер"""
    import warnings
    warnings.filterwarnings("ignore", message=".*development server.*")
    
    port = int(os.environ.get("PORT", 10000))
    print(f"🌐 Flask запускается на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ==================== ОСНОВНОЙ ЗАПУСК ====================
async def main():
    """Основная функция запуска"""
    print("🚀 Запуск системы...")
    print(f"📊 Настройки:")
    print(f"   Чат ID: {MAIN_CHAT_ID}")
    print(f"   Тема отчётов: {REPORT_THREAD_ID}")
    print(f"   Тема комментариев: {COMMENTS_THREAD_ID}")
    print(f"   Канал: ОТКЛЮЧЕН")
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("✅ Старые обновления очищены")
    except:
        pass
    
    await asyncio.sleep(3)
    
    import threading
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    print("🌐 Flask запущен в отдельном потоке")
    
    keep_alive_task = asyncio.create_task(ping_server())
    print("🔄 Keep-alive запущен (запросы каждые 14 минут)")
    
    print("🤖 Запуск Telegram бота...")
    
    try:
        await dp.start_polling(
            bot,
            skip_updates=True,
            allowed_updates=dp.resolve_used_update_types()
        )
    finally:
        keep_alive_task.cancel()
        print("🛑 Keep-alive остановлен")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())