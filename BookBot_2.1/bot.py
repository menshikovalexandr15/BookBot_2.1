"""Book Bot 2.1 — Telegram bot with multilingual menus, cards, SQLite and legal ebook files."""
import asyncio
import hashlib
import html
import logging
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M, Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from catalog import Book, search, get_book, enrich, HEADERS
from storage import Storage

load_dotenv(Path(__file__).with_name('.env'))
TOKEN = os.getenv('BOT_TOKEN')
DB = Storage(os.getenv('DATABASE_PATH', str(Path(__file__).with_name('books.sqlite3'))))
MAX_BYTES = 49 * 1024 * 1024
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')

STRINGS = {
 'ru': {'welcome':'📚 Добро пожаловать в Книжный бот 2.1!\n\nОтправь название книги или автора. Ищем по нескольким открытым каталогам: Gutenberg, Open Library, Google Books, Internet Archive и русской Викитеке.', 'help':'🔎 Напиши название или автора, например Pride and Prejudice или Война и мир. Поиск идёт по нескольким международным и русскоязычным каталогам.\n📖 Открой карточку, добавь в избранное или выбери доступный формат.\n📁 EPUB, PDF, FB2 — только если файл доступен в источнике.\n⚖️ Не все книги свободны для скачивания в каждой стране.', 'menu':'📚 Главное меню\nВыбери действие или просто отправь название книги:', 'search':'🔎 Поиск', 'genres':'📚 Жанры', 'favorites':'❤️ Избранное', 'history':'🕘 История', 'daily':'🎲 Книга дня', 'language':'🌐 Язык', 'help_btn':'❓ Помощь', 'enter':'Напиши название книги или имя автора:', 'bad':'Введи название или автора: 2–150 символов, с буквами.', 'searching':'🔎 Ищу…', 'notfound':'Ничего не найдено. Попробуй название или автора отдельно. Например: Война и мир, Толстой, War and Peace.', 'offline':'Каталог сейчас недоступен. Попробуй позже.', 'results':'📚 Результаты: {count}\nСтраница {page}. Выбери книгу:', 'back':'⬅️ Назад', 'next':'Далее ➡️', 'menu_btn':'🏠 Меню', 'details':'📖 Подробнее', 'download':'📥 Скачать', 'save':'❤️ В избранное', 'remove':'💔 Удалить из избранного', 'saved':'❤️ Сохранено в избранное.', 'unsaved':'💔 Удалено из избранного.', 'emptyfav':'В избранном пока пусто.', 'emptyhist':'История поиска пуста.', 'choose':'Выбери формат:', 'noformats':'Для этой книги нет прямых файлов EPUB, PDF или FB2.', 'loading':'⏳ Загружаю файл…', 'sending':'📤 Отправляю в Telegram…', 'blocked':'Источник не разрешил автоматическую загрузку (HTTP {code}).', 'large':'Файл слишком большой для отправки ботом.', 'failure':'Не удалось скачать файл ({error}).', 'sendfail':'Не удалось отправить файл через Telegram ({error}).', 'source':'Открыть источник', 'stale':'Эта кнопка устарела. Выполни поиск ещё раз.', 'genre_prompt':'Выбери жанр:', 'language_prompt':'Выбери язык интерфейса:', 'language_saved':'Язык интерфейса изменён.', 'about':'Аннотация', 'no_description':'Аннотация отсутствует в открытом каталоге.', 'year':'Год', 'lang':'Язык', 'genre':'Темы', 'no_results':'Доступных книг не найдено.', 'error':'Произошла ошибка. Попробуй позже.'},
 'en': {'welcome':'📚 Welcome to Book Bot 2.1!\n\nSend a title or author. We search several open catalogs: Gutenberg, Open Library, Google Books, Internet Archive and Russian Wikisource.', 'help':'🔎 Send a title or author, e.g. Pride and Prejudice or Война и мир. We search several international and Russian catalogs.\n📖 Open a book card, save it, or choose an available file format.\n📁 EPUB, PDF, FB2 — only if the source provides the file.\n⚖️ Availability and copyright depend on your country.', 'menu':'📚 Main menu\nChoose an action or send a book title:', 'search':'🔎 Search', 'genres':'📚 Genres', 'favorites':'❤️ Favorites', 'history':'🕘 History', 'daily':'🎲 Book of the day', 'language':'🌐 Language', 'help_btn':'❓ Help', 'enter':'Send a book title or author:', 'bad':'Enter a title or author: 2–150 characters containing letters.', 'searching':'🔎 Searching…', 'notfound':'No results. Try another query.', 'offline':'Catalog unavailable. Try again later.', 'results':'📚 Results: {count}\nPage {page}. Select a book:', 'back':'⬅️ Back', 'next':'Next ➡️', 'menu_btn':'🏠 Menu', 'details':'📖 Details', 'download':'📥 Download', 'save':'❤️ Save', 'remove':'💔 Remove favorite', 'saved':'❤️ Added to favorites.', 'unsaved':'💔 Removed from favorites.', 'emptyfav':'No favorites yet.', 'emptyhist':'Search history is empty.', 'choose':'Choose a format:', 'noformats':'No direct EPUB, PDF or FB2 files available for this book.', 'loading':'⏳ Downloading…', 'sending':'📤 Sending to Telegram…', 'blocked':'Source blocked automatic download (HTTP {code}).', 'large':'File is too large for bot upload.', 'failure':'Could not download ({error}).', 'sendfail':'Could not send via Telegram ({error}).', 'source':'Open source', 'stale':'This button expired. Search again.', 'genre_prompt':'Choose a genre:', 'language_prompt':'Choose interface language:', 'language_saved':'Interface language updated.', 'about':'Description', 'no_description':'No description in the open catalog.', 'year':'Year', 'lang':'Language', 'genre':'Subjects', 'no_results':'No available books found.', 'error':'Something went wrong. Try again.'}
}
GENRES = [('🚀 Sci-fi','science fiction'), ('🕵️ Mystery','detective'), ('💘 Romance','romance'), ('🧙 Fantasy','fantasy'), ('🏛️ Classics','classic'), ('🧭 Adventure','adventure'), ('📜 History','history'), ('🧠 Philosophy','philosophy')]


def tr(user_id, key, **kwargs):
    return STRINGS[DB.language(user_id)][key].format(**kwargs)


def menu(uid):
    return M([[B(tr(uid,'search'),callback_data='menu:search'), B(tr(uid,'genres'),callback_data='menu:genres')],
              [B(tr(uid,'favorites'),callback_data='menu:favorites'), B(tr(uid,'history'),callback_data='menu:history')],
              [B(tr(uid,'daily'),callback_data='menu:daily'), B(tr(uid,'language'),callback_data='menu:language')],
              [B(tr(uid,'help_btn'),callback_data='menu:help')]])


def home_button(uid):
    return [B(tr(uid,'menu_btn'),callback_data='menu:home')]


def valid_query(query):
    return 2 <= len(query.strip()) <= 150 and any(c.isalpha() for c in query)


def short(s, n=55):
    return s if len(s) <= n else s[:n-1] + '…'


async def say(update, text, reply_markup=None, photo=None):
    # CallbackQuery.message may be a photo: send a fresh message rather than editing incompatible media.
    msg = update.effective_message
    if photo and photo.startswith('https://'):
        try:
            return await msg.reply_photo(
                photo=photo,
                caption=text[:1024],
                parse_mode='HTML',
                reply_markup=reply_markup,
                connect_timeout=30,
                read_timeout=60,
                write_timeout=60,
                pool_timeout=30,
            )
        except TelegramError:
            log.warning('Could not show cover; falling back to text', exc_info=True)
    return await msg.reply_text(
        text[:4096],
        parse_mode='HTML',
        reply_markup=reply_markup,
        disable_web_page_preview=True,
        connect_timeout=30,
        read_timeout=60,
        write_timeout=60,
        pool_timeout=30,
    )


async def start(update, context):
    uid = update.effective_user.id
    await say(update, html.escape(tr(uid,'welcome')), menu(uid))


async def help_cmd(update, context):
    uid = update.effective_user.id
    await say(update, html.escape(tr(uid,'help')), M([home_button(uid)]))


async def show_menu(update, context):
    uid = update.effective_user.id
    await say(update, html.escape(tr(uid,'menu')), menu(uid))


async def do_search(update, context, query='', topic='', page=1, remember=False):
    uid = update.effective_user.id
    if remember and query:
        DB.add_history(uid, query)
    context.user_data['last_search'] = (query, topic)
    status = await say(update, tr(uid,'searching'))
    try:
        books, has_next, count = await search(query=query, topic=topic, page=page)
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        log.exception('Catalog search failed')
        await status.edit_text(tr(uid,'offline'))
        return
    if not books:
        await status.edit_text(tr(uid,'notfound'))
        return
    rows = [[B(short(b.title, 45),callback_data=f'view:{b.id}')] for b in books]
    nav = []
    if page > 1:
        nav.append(B(tr(uid,'back'),callback_data=f'page:{page-1}'))
    if has_next and page < 100:
        nav.append(B(tr(uid,'next'),callback_data=f'page:{page+1}'))
    if nav:
        rows.append(nav)
    rows.append(home_button(uid))
    await status.edit_text(tr(uid,'results',count=count,page=page),reply_markup=M(rows))


async def handle_text(update, context):
    uid = update.effective_user.id
    query = (update.effective_message.text or '').strip()
    if not valid_query(query):
        await say(update,tr(uid,'bad'))
        return
    await do_search(update,context,query=query,remember=True)


async def show_book(update, context, book_id):
    uid = update.effective_user.id
    try:
        book = await get_book(book_id)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        log.exception('Book lookup failed')
        await say(update,tr(uid,'offline'))
        return
    book = await enrich(book)
    desc = book.description.strip() or tr(uid,'no_description')
    # Avoid overflowing photo caption; longer descriptions can be viewed via the details button.
    text = f'📚 <b>{html.escape(short(book.title,130))}</b>\n✍️ {html.escape(short(book.author,160))}\n🌐 {html.escape(book.language)}'
    if book.year:
        text += f'\n📅 {html.escape(book.year)}'
    text += f'\n🏛️ {html.escape(book.source)}'
    text += f'\n\n<b>{tr(uid,"about")}:</b> {html.escape(short(desc,360))}'
    text += f'\n\n📁 {", ".join(ext.upper() for ext in book.formats) or "—"}'
    rows = [[B(tr(uid,'download'),callback_data=f'formats:{book.id}')] if book.formats else [],
            [B(tr(uid,'source'),url=book.source_url)] if book.source_url else [],
            [B(tr(uid,'save'),callback_data=f'fav:{book.id}')],
            [B(tr(uid,'details'),callback_data=f'details:{book.id}')],home_button(uid)]
    await say(update,text,M([r for r in rows if r]),photo=book.cover)


async def show_details(update, context, book_id):
    uid = update.effective_user.id
    try:
        book = await enrich(await get_book(book_id))
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        await say(update,tr(uid,'offline'))
        return
    desc = html.escape(short(book.description or tr(uid,'no_description'),2200))
    subjects = ', '.join(book.subjects[:4]) or '—'
    text = f'📖 <b>{html.escape(book.title)}</b>\n✍️ {html.escape(book.author)}\n🌐 {html.escape(book.language)}\n📅 {html.escape(book.year or "—")}\n🏛️ {html.escape(book.source)}\n🏷️ {html.escape(short(subjects,450))}\n\n<b>{tr(uid,"about")}:</b>\n{desc}'
    buttons = ([[B(tr(uid,'download'),callback_data=f'formats:{book.id}')]] if book.formats else [])
    if book.source_url:
        buttons.append([B(tr(uid,'source'),url=book.source_url)])
    buttons += [[B(tr(uid,'back'),callback_data=f'view:{book.id}')],home_button(uid)]
    await say(update,text,M(buttons))


async def show_formats(update, context, book_id):
    uid = update.effective_user.id
    try:
        book = await get_book(book_id)
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        await say(update,tr(uid,'offline'))
        return
    if not book.formats:
        await say(update,tr(uid,'noformats'),M([[B(tr(uid,'back'),callback_data=f'view:{book.id}')]]))
        return
    rows = [[B(ext.upper(),callback_data=f'file:{book.id}:{ext}')] for ext in book.formats]
    rows += [[B(tr(uid,'back'),callback_data=f'view:{book.id}')],home_button(uid)]
    await say(update,html.escape(tr(uid,'choose'))+f'\n<b>{html.escape(short(book.title,100))}</b>',M(rows))


class TooLarge(Exception):
    pass


async def fetch_file(url, path):
    if urlparse(url).scheme != 'https':
        raise ValueError('Only HTTPS is allowed')
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90,connect=20,read=45), follow_redirects=True, headers=HEADERS) as client:
                async with client.stream('GET',url) as resp:
                    resp.raise_for_status()
                    size = resp.headers.get('content-length')
                    if size and int(size)>MAX_BYTES:
                        raise TooLarge()
                    total=0
                    with path.open('wb') as out:
                        async for chunk in resp.aiter_bytes():
                            total+=len(chunk)
                            if total>MAX_BYTES:
                                raise TooLarge()
                            out.write(chunk)
                    if not total:
                        raise ValueError('Empty file')
                    with path.open('rb') as inp:
                        head=inp.read(512).lstrip().lower()
                    if head.startswith((b'<html',b'<!doctype html')):
                        raise ValueError('HTML instead of ebook')
                    return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (429,500,502,503,504) or attempt:
                raise
        except (httpx.TimeoutException,httpx.NetworkError):
            if attempt:
                raise
        await asyncio.sleep(2)


def source_markup(uid,url):
    return M([[B(tr(uid,'source'),url=url)],home_button(uid)])


async def send_file(update, context, book_id, ext):
    uid=update.effective_user.id
    try:
        book=await get_book(book_id)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        await say(update,tr(uid,'offline'))
        return
    url=book.formats.get(ext)
    if not url or urlparse(url).scheme!='https':
        await say(update,tr(uid,'stale'))
        return
    status=await say(update,tr(uid,'loading'))
    filename=(re.sub(r'[^\w .()-]','_',book.title)[:75].strip() or 'book')+'.'+ext
    with tempfile.TemporaryDirectory(prefix='bookbot_') as tmp:
        path=Path(tmp)/filename
        try:
            await fetch_file(url,path)
        except TooLarge:
            await status.edit_text(tr(uid,'large'),reply_markup=source_markup(uid,url))
            return
        except httpx.HTTPStatusError as exc:
            log.warning('Download HTTP %s for book %s',exc.response.status_code,book.id)
            await status.edit_text(tr(uid,'blocked',code=exc.response.status_code),reply_markup=source_markup(uid,url))
            return
        except (httpx.HTTPError,ValueError,OSError) as exc:
            log.exception('Download failed for book %s',book.id)
            await status.edit_text(tr(uid,'failure',error=type(exc).__name__),reply_markup=source_markup(uid,url))
            return
        try:
            await status.edit_text(tr(uid,'sending'))
            with path.open('rb') as doc:
                await update.effective_message.reply_document(document=doc,filename=filename,caption=f'📚 {book.title}\n✍️ {book.author}'[:1024],read_timeout=120,write_timeout=180,connect_timeout=30)
            await status.delete()
        except (TelegramError,OSError) as exc:
            log.exception('Telegram upload failed')
            await status.edit_text(tr(uid,'sendfail',error=type(exc).__name__),reply_markup=source_markup(uid,url))


async def show_favorites(update, context):
    uid=update.effective_user.id
    rows=DB.favorites(uid)
    if not rows:
        await say(update,tr(uid,'emptyfav'),M([home_button(uid)]))
        return
    buttons=[[B(short(title,48),callback_data=f'view:{bid}')] for bid,title,author in rows]
    buttons.append(home_button(uid))
    await say(update,tr(uid,'favorites'),M(buttons))


async def show_history(update, context):
    uid=update.effective_user.id
    rows=DB.history(uid)
    if not rows:
        await say(update,tr(uid,'emptyhist'),M([home_button(uid)]))
        return
    # Store short list in user_data: callback indices are never untrusted arbitrary text.
    context.user_data['history_queries']=[r[0] for r in rows]
    buttons=[[B(short(q,50),callback_data=f'hist:{i}')] for i,(q,_) in enumerate(rows)]
    buttons.append(home_button(uid))
    await say(update,tr(uid,'history'),M(buttons))


async def show_genres(update,context):
    uid=update.effective_user.id
    rows=[[B(label,callback_data=f'genre:{i}')] for i,(label,_) in enumerate(GENRES)]
    rows.append(home_button(uid))
    await say(update,tr(uid,'genre_prompt'),M(rows))


async def show_daily(update,context):
    # Same book for all users on a UTC day; based on catalog's popularity list.
    from datetime import datetime,timezone
    uid=update.effective_user.id
    try:
        books,_,_=await search(page=1)
        if not books:
            await say(update,tr(uid,'no_results'))
            return
        date=datetime.now(timezone.utc).date().isoformat()
        index=int(hashlib.sha256(date.encode()).hexdigest(),16)%len(books)
        await show_book(update,context,books[index].id)
    except (httpx.HTTPError,ValueError,KeyError,TypeError):
        log.exception('Daily book failed')
        await say(update,tr(uid,'offline'))


async def callback(update,context):
    cb=update.callback_query
    try:
        await cb.answer()
    except TelegramError:
        # A delayed callback acknowledgement should not prevent the requested action.
        log.warning('Callback acknowledgement timed out or failed', exc_info=True)
    uid=update.effective_user.id
    parts=(cb.data or '').split(':')
    action=parts[0]
    try:
        if action=='menu':
            choice=parts[1]
            if choice=='home': await show_menu(update,context)
            elif choice=='search': await say(update,tr(uid,'enter'))
            elif choice=='genres': await show_genres(update,context)
            elif choice=='favorites': await show_favorites(update,context)
            elif choice=='history': await show_history(update,context)
            elif choice=='daily': await show_daily(update,context)
            elif choice=='language':
                await say(update,tr(uid,'language_prompt'),M([[B('🇷🇺 Русский',callback_data='lang:ru'),B('🇬🇧 English',callback_data='lang:en')],home_button(uid)]))
            elif choice=='help': await help_cmd(update,context)
        elif action=='lang' and parts[1] in ('ru','en'):
            DB.set_language(uid,parts[1]); await say(update,tr(uid,'language_saved'),menu(uid))
        elif action=='genre':
            index=int(parts[1]); topic=GENRES[index][1]
            await do_search(update,context,topic=topic)
        elif action=='hist':
            q=context.user_data.get('history_queries',[])[int(parts[1])]
            await do_search(update,context,query=q,remember=True)
        elif action=='page':
            q,topic=context.user_data.get('last_search',('',''))
            if not q and not topic:
                await say(update,tr(uid,'stale'))
            else:
                await do_search(update,context,query=q,topic=topic,page=int(parts[1]))
        elif action in ('view','details','formats','fav'):
            book_id=int(parts[1])
            if action=='view': await show_book(update,context,book_id)
            elif action=='details': await show_details(update,context,book_id)
            elif action=='formats': await show_formats(update,context,book_id)
            else:
                book=await get_book(book_id)
                saved=DB.toggle_favorite(uid,book)
                await say(update,tr(uid,'saved' if saved else 'unsaved'),M([[B(tr(uid,'details'),callback_data=f'view:{book_id}')],home_button(uid)]))
        elif action=='file' and parts[2] in ('epub','pdf','fb2'):
            await send_file(update,context,int(parts[1]),parts[2])
        else:
            await say(update,tr(uid,'stale'))
    except (ValueError,IndexError,KeyError,TypeError):
        log.warning('Invalid or stale callback: %r',cb.data,exc_info=True)
        await say(update,tr(uid,'stale'))
    except httpx.HTTPError:
        log.exception('Callback network failure')
        try:
            await say(update,tr(uid,'offline'))
        except TelegramError:
            log.exception('Could not notify user about catalog failure')
    except TelegramError:
        log.exception('Telegram API timeout/error while handling callback')


async def error_handler(update,context):
    error = context.error
    log.error(
        'Unhandled error',
        exc_info=(type(error), error, error.__traceback__) if error else None,
    )
    # If Telegram itself timed out, another Telegram message is unlikely to succeed.
    if isinstance(error, TelegramError):
        return
    if isinstance(update,Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                tr(update.effective_user.id,'error'),
                connect_timeout=30,
                read_timeout=60,
                write_timeout=60,
                pool_timeout=30,
            )
        except TelegramError:
            log.exception('Could not send generic error message')


def main():
    if not TOKEN:
        raise SystemExit('BOT_TOKEN is missing. Create .env next to bot.py')
    app = (
        Application.builder()
        .token(TOKEN)
        .connection_pool_size(16)
        .connect_timeout(30)
        .read_timeout(90)
        .write_timeout(90)
        .pool_timeout(30)
        .get_updates_connection_pool_size(4)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(60)
        .get_updates_write_timeout(60)
        .get_updates_pool_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler('start',start))
    app.add_handler(CommandHandler('help',help_cmd))
    app.add_handler(CommandHandler('menu',show_menu))
    app.add_handler(CommandHandler('favorites',show_favorites))
    app.add_handler(CommandHandler('history',show_history))
    app.add_handler(CommandHandler('genres',show_genres))
    app.add_handler(CommandHandler('daily',show_daily))
    app.add_handler(CallbackQueryHandler(callback,pattern=r'^(menu|lang|genre|hist|page|view|details|formats|fav|file):'))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_text))
    app.add_error_handler(error_handler)
    log.info('Book Bot 2.1 started')
    app.run_polling()


if __name__=='__main__':
    main()
