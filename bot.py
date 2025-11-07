import logging
import asyncio
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import yt_dlp
import requests
import sqlite3
import os
from typing import Optional, List, Dict

# Configuration
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_BOT_TOKEN')
LASTFM_API_KEY = os.environ.get('LASTFM_API_KEY', 'YOUR_LASTFM_API_KEY')
LASTFM_API_URL = 'http://ws.audioscrobbler.com/2.0/'
DB_PATH = 'users.db'

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Advertisement messages
ADS = [
    "Реклама: Попробуй наш партнёрский бот @CoolMusicBot!",
    "Реклама: Открой для себя новую музыку в @MusicDiscoveryBot!",
    "Реклама: Слушай радио онлайн через @RadioStreamBot!",
    "Реклама: Создавай плейлисты с @PlaylistMasterBot!"
]

# Database initialization
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            mode TEXT DEFAULT 'basic',
            interaction_count INTEGER DEFAULT 0,
            preferences TEXT DEFAULT ''
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            track_name TEXT,
            artist TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    ''')
    conn.commit()
    conn.close()

def get_user(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, mode, interaction_count, preferences FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {'user_id': row[0], 'mode': row[1], 'interaction_count': row[2], 'preferences': row[3]}
    return None

def create_or_update_user(user_id: int, mode: str = None, increment_count: bool = False):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    user = get_user(user_id)

    if user is None:
        cursor.execute('INSERT INTO users (user_id, mode, interaction_count) VALUES (?, ?, ?)',
                      (user_id, mode or 'basic', 0))
    else:
        if mode:
            cursor.execute('UPDATE users SET mode = ? WHERE user_id = ?', (mode, user_id))
        if increment_count:
            cursor.execute('UPDATE users SET interaction_count = interaction_count + 1 WHERE user_id = ?', (user_id,))

    conn.commit()
    conn.close()

def add_to_history(user_id: int, track_name: str, artist: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO history (user_id, track_name, artist) VALUES (?, ?, ?)',
                  (user_id, track_name, artist))
    conn.commit()
    conn.close()

def should_show_ad(user_id: int) -> bool:
    user = get_user(user_id)
    if user and user['interaction_count'] > 0 and user['interaction_count'] % 10 == 0:
        return True
    return False

# Last.fm API functions
def search_track_lastfm(query: str, limit: int = 5) -> List[Dict]:
    try:
        params = {
            'method': 'track.search',
            'track': query,
            'api_key': LASTFM_API_KEY,
            'format': 'json',
            'limit': limit
        }
        response = requests.get(LASTFM_API_URL, params=params, timeout=10)
        data = response.json()

        tracks = []
        if 'results' in data and 'trackmatches' in data['results']:
            for track in data['results']['trackmatches'].get('track', []):
                tracks.append({
                    'name': track.get('name', ''),
                    'artist': track.get('artist', ''),
                    'url': track.get('url', '')
                })
        return tracks
    except Exception as e:
        logger.error(f"Last.fm search error: {e}")
        return []

def get_similar_tracks(artist: str, track: str, limit: int = 10) -> List[Dict]:
    try:
        params = {
            'method': 'track.getSimilar',
            'artist': artist,
            'track': track,
            'api_key': LASTFM_API_KEY,
            'format': 'json',
            'limit': limit
        }
        response = requests.get(LASTFM_API_URL, params=params, timeout=10)
        data = response.json()

        tracks = []
        if 'similartracks' in data and 'track' in data['similartracks']:
            for track in data['similartracks']['track']:
                tracks.append({
                    'name': track.get('name', ''),
                    'artist': track['artist'].get('name', '') if isinstance(track.get('artist'), dict) else track.get('artist', ''),
                })
        return tracks
    except Exception as e:
        logger.error(f"Last.fm similar tracks error: {e}")
        return []

def get_top_tracks_by_tag(tag: str, limit: int = 10) -> List[Dict]:
    try:
        params = {
            'method': 'tag.getTopTracks',
            'tag': tag,
            'api_key': LASTFM_API_KEY,
            'format': 'json',
            'limit': limit
        }
        response = requests.get(LASTFM_API_URL, params=params, timeout=10)
        data = response.json()

        tracks = []
        if 'tracks' in data and 'track' in data['tracks']:
            for track in data['tracks']['track']:
                tracks.append({
                    'name': track.get('name', ''),
                    'artist': track['artist'].get('name', '') if isinstance(track.get('artist'), dict) else track.get('artist', ''),
                })
        return tracks
    except Exception as e:
        logger.error(f"Last.fm top tracks error: {e}")
        return []

# YouTube download function
def download_audio(query: str) -> Optional[str]:
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'downloads/%(title)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'no_warnings': True,
            'default_search': 'ytsearch1',
        }

        os.makedirs('downloads', exist_ok=True)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=True)
            if info:
                filename = ydl.prepare_filename(info)
                audio_file = filename.rsplit('.', 1)[0] + '.mp3'
                return audio_file
    except Exception as e:
        logger.error(f"Download error: {e}")
    return None

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    create_or_update_user(user_id)

    keyboard = [
        [InlineKeyboardButton("🎵 Базовый режим", callback_data='mode_basic')],
        [InlineKeyboardButton("🎸 Расширенный режим", callback_data='mode_advanced')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_text = (
        "🎶 Добро пожаловать в MelodyForge!\n\n"
        "Я помогу тебе найти и скачать любимую музыку.\n\n"
        "📌 Выбери режим работы:\n"
        "• Базовый — простой поиск музыки\n"
        "• Расширенный — рекомендации, плейлисты, миксы"
    )

    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    data = query.data

    if data == 'mode_basic':
        create_or_update_user(user_id, mode='basic')
        await query.edit_message_text(
            "✅ Базовый режим активирован!\n\n"
            "Просто отправь мне название песни или исполнителя, и я найду музыку для тебя."
        )

    elif data == 'mode_advanced':
        create_or_update_user(user_id, mode='advanced')
        keyboard = [
            [InlineKeyboardButton("🔍 Поиск музыки", callback_data='search')],
            [InlineKeyboardButton("💡 Рекомендации", callback_data='recommendations')],
            [InlineKeyboardButton("🎼 Микс по жанру", callback_data='genre_mix')],
            [InlineKeyboardButton("📜 История", callback_data='history')],
            [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_start')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "✅ Расширенный режим активирован!\n\n"
            "Выбери действие:",
            reply_markup=reply_markup
        )

    elif data == 'search':
        await query.edit_message_text("🔍 Отправь название песни или исполнителя для поиска.")

    elif data == 'recommendations':
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT track_name, artist FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1', (user_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            track_name, artist = row
            await query.edit_message_text(f"🔄 Ищу похожие треки на '{track_name}' - {artist}...")

            similar = get_similar_tracks(artist, track_name, limit=5)
            if similar:
                text = "💡 Рекомендации на основе твоей истории:\n\n"
                for i, track in enumerate(similar, 1):
                    text += f"{i}. {track['artist']} - {track['name']}\n"
                text += "\nОтправь номер трека для скачивания или название новой песни."
                context.user_data['recommendations'] = similar
                await query.edit_message_text(text)
            else:
                await query.edit_message_text("😔 Не удалось найти рекомендации. Попробуй позже.")
        else:
            await query.edit_message_text("📭 У тебя пока нет истории прослушиваний. Скачай несколько треков сначала!")

    elif data == 'genre_mix':
        keyboard = [
            [InlineKeyboardButton("🎸 Rock", callback_data='genre_rock')],
            [InlineKeyboardButton("🎹 Pop", callback_data='genre_pop')],
            [InlineKeyboardButton("🎺 Jazz", callback_data='genre_jazz')],
            [InlineKeyboardButton("🎤 Hip-Hop", callback_data='genre_hip hop')],
            [InlineKeyboardButton("🎼 Electronic", callback_data='genre_electronic')],
            [InlineKeyboardButton("⬅️ Назад", callback_data='mode_advanced')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("🎼 Выбери жанр для микса:", reply_markup=reply_markup)

    elif data.startswith('genre_'):
        genre = data.replace('genre_', '')
        await query.edit_message_text(f"🔄 Создаю микс в жанре {genre.title()}...")

        tracks = get_top_tracks_by_tag(genre, limit=10)
        if tracks:
            text = f"🎼 Топ-10 треков в жанре {genre.title()}:\n\n"
            for i, track in enumerate(tracks, 1):
                text += f"{i}. {track['artist']} - {track['name']}\n"
            text += "\nОтправь номер трека для скачивания."
            context.user_data['genre_mix'] = tracks
            await query.edit_message_text(text)
        else:
            await query.edit_message_text("😔 Не удалось создать микс. Попробуй другой жанр.")

    elif data == 'history':
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT track_name, artist, timestamp FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 10', (user_id,))
        rows = cursor.fetchall()
        conn.close()

        if rows:
            text = "📜 Твоя история (последние 10 треков):\n\n"
            for i, (track, artist, _) in enumerate(rows, 1):
                text += f"{i}. {artist} - {track}\n"
            await query.edit_message_text(text)
        else:
            await query.edit_message_text("📭 История пуста.")

    elif data == 'back_to_start':
        keyboard = [
            [InlineKeyboardButton("🎵 Базовый режим", callback_data='mode_basic')],
            [InlineKeyboardButton("🎸 Расширенный режим", callback_data='mode_advanced')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "🎶 MelodyForge\n\nВыбери режим работы:",
            reply_markup=reply_markup
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    create_or_update_user(user_id, increment_count=True)

    # Check for advertisement
    if should_show_ad(user_id):
        ad = random.choice(ADS)
        await update.message.reply_text(ad)

    user = get_user(user_id)
    mode = user['mode'] if user else 'basic'

    # Check if user is selecting from recommendations or genre mix
    if text.isdigit():
        track_num = int(text) - 1

        if 'recommendations' in context.user_data and track_num < len(context.user_data['recommendations']):
            track = context.user_data['recommendations'][track_num]
            query = f"{track['artist']} {track['name']}"
            await process_download(update, user_id, query, track['name'], track['artist'])
            return

        if 'genre_mix' in context.user_data and track_num < len(context.user_data['genre_mix']):
            track = context.user_data['genre_mix'][track_num]
            query = f"{track['artist']} {track['name']}"
            await process_download(update, user_id, query, track['name'], track['artist'])
            return

    # Regular search
    if mode == 'basic':
        # Basic mode: direct search and download
        await update.message.reply_text("🔍 Ищу музыку...")
        tracks = search_track_lastfm(text, limit=1)

        if tracks:
            track = tracks[0]
            query = f"{track['artist']} {track['name']}"
            await process_download(update, user_id, query, track['name'], track['artist'])
        else:
            # Fallback to direct YouTube search
            await process_download(update, user_id, text, text, 'Unknown')

    else:
        # Advanced mode: show search results
        await update.message.reply_text("🔍 Ищу музыку...")
        tracks = search_track_lastfm(text, limit=5)

        if tracks:
            text_response = "🎵 Найденные треки:\n\n"
            for i, track in enumerate(tracks, 1):
                text_response += f"{i}. {track['artist']} - {track['name']}\n"
            text_response += "\nОтправь номер трека для скачивания."

            context.user_data['search_results'] = tracks
            await update.message.reply_text(text_response)
        else:
            await update.message.reply_text("😔 Ничего не найдено. Попробуй другой запрос.")

async def process_download(update: Update, user_id: int, query: str, track_name: str, artist: str):
    try:
        status_msg = await update.message.reply_text("⬇️ Скачиваю...")

        audio_file = download_audio(query)

        if audio_file and os.path.exists(audio_file):
            await status_msg.edit_text("📤 Отправляю...")

            with open(audio_file, 'rb') as audio:
                await update.message.reply_audio(
                    audio=audio,
                    title=track_name,
                    performer=artist
                )

            add_to_history(user_id, track_name, artist)
            await status_msg.delete()

            # Clean up
            try:
                os.remove(audio_file)
            except:
                pass
        else:
            await status_msg.edit_text("❌ Не удалось скачать трек. Попробуй другой запрос.")

    except Exception as e:
        logger.error(f"Download process error: {e}")
        await update.message.reply_text("❌ Произошла ошибка при скачивании. Попробуй позже.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception while handling an update: {context.error}")

def main():
    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("Bot started!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
