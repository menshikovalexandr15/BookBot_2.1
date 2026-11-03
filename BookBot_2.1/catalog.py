"""Multi-source catalog for Book Bot.

Sources:
- Project Gutenberg via Gutendex: public-domain ebooks and direct formats.
- Open Library: broad international/Russian metadata and covers.
- Google Books: broad discovery; direct downloads only when Google exposes a downloadLink.
- Internet Archive: discovery and item pages; downloads are not assumed to be rights-cleared.
- Russian Wikisource: Russian-language works and source links.

Search is best-effort: one unavailable provider does not disable the others.
"""
import asyncio
import html
import json
import logging
import re
import zlib
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import quote

import httpx
from query_language import search_variants

log = logging.getLogger(__name__)
HEADERS = {
    "User-Agent": "BookBot/2.1 (personal educational book discovery bot; contact: configure-your-email)"
}
GUTENDEX = "https://gutendex.com/books/"
OPENLIBRARY = "https://openlibrary.org"
GOOGLE_BOOKS = "https://www.googleapis.com/books/v1/volumes"
IA_SEARCH = "https://archive.org/advancedsearch.php"
WIKISOURCE_API = "https://ru.wikisource.org/w/api.php"
CACHE_PATH = Path(__file__).with_name("catalog_cache.json")

@dataclass
class Book:
    id: int
    title: str
    author: str
    language: str
    subjects: list[str]
    cover: str | None
    formats: dict[str, str]
    description: str = ""
    year: str = ""
    source: str = "Unknown"
    source_url: str = ""

_BOOK_CACHE: dict[int, Book] = {}

def _save_cache():
    try:
        payload = {str(k): asdict(v) for k, v in _BOOK_CACHE.items() if not (0 < k < 10_000_000)}
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE_PATH)
    except OSError:
        log.warning("Could not persist catalog cache", exc_info=True)

def _load_cache():
    if _BOOK_CACHE or not CACHE_PATH.exists():
        return
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        for key, value in raw.items():
            try:
                value["subjects"] = list(value.get("subjects") or [])
                value["formats"] = dict(value.get("formats") or {})
                _BOOK_CACHE[int(key)] = Book(**value)
            except (TypeError, ValueError):
                continue
    except (OSError, json.JSONDecodeError):
        log.warning("Could not read catalog cache", exc_info=True)

def _synthetic_id(prefix: int, external_id: str) -> int:
    # Stable namespace IDs, persisted in catalog_cache.json for favorites after restart.
    digest = zlib.crc32(external_id.encode("utf-8")) & 0x1FFFFFFF
    return prefix + digest

def _clone(book: Book) -> Book:
    return replace(book, subjects=list(book.subjects), formats=dict(book.formats))

def pick_formats(formats: dict) -> dict[str, str]:
    result = {}
    for mime, url in (formats or {}).items():
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        m = str(mime).lower()
        if "application/epub+zip" in m and "epub" not in result:
            result["epub"] = url
        elif "application/pdf" in m and "pdf" not in result:
            result["pdf"] = url
        elif ("fictionbook" in m or url.split("?")[0].lower().endswith(".fb2")) and "fb2" not in result:
            result["fb2"] = url
    return result

def parse_gutenberg(item: dict) -> Book:
    authors = ", ".join(a.get("name", "") for a in item.get("authors", []) if a.get("name")) or "Unknown"
    languages = item.get("languages") or ["?"]
    formats = item.get("formats") or {}
    book = Book(
        id=int(item["id"]), title=item.get("title") or "Untitled", author=authors,
        language=languages[0], subjects=item.get("subjects") or [],
        cover=formats.get("image/jpeg"), formats=pick_formats(formats),
        source="Project Gutenberg", source_url=f"https://www.gutenberg.org/ebooks/{item['id']}"
    )
    return book

async def get_json(url, params=None):
    timeout = httpx.Timeout(35, connect=12, read=30, write=15, pool=10)
    last_error = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=HEADERS) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(1)
    if last_error:
        raise last_error
    raise RuntimeError("Request failed")

def _remember(books):
    for book in books:
        _BOOK_CACHE[book.id] = book
    _save_cache()

async def _search_gutenberg(query: str, topic: str, page: int):
    variants = search_variants(query) if query else [""]
    for variant in variants:
        params = {"page": page}
        if variant:
            params["search"] = variant
        if topic:
            params["topic"] = topic
        data = await get_json(GUTENDEX, params)
        results = [parse_gutenberg(x) for x in data.get("results", []) if x.get("id")]
        if results or variant == variants[-1]:
            return results, bool(data.get("next")), int(data.get("count", 0))
    return [], False, 0

async def _search_openlibrary(query: str, page: int):
    params = {
        "page": page, "limit": 10,
        "fields": "key,title,author_name,language,cover_i,first_publish_year,subject,ia,has_fulltext",
    }
    if query:
        params["q"] = query
    else:
        params["q"] = "classic literature"
    data = await get_json(f"{OPENLIBRARY}/search.json", params)
    books = []
    for item in data.get("docs", []):
        match = re.fullmatch(r"(?:/works/)?OL([0-9]+)W", str(item.get("key", "")))
        if not match:
            continue
        olid = match.group(1)
        cover = item.get("cover_i")
        ia_ids = item.get("ia") or []
        source_url = f"https://openlibrary.org/works/OL{olid}W"
        if ia_ids:
            # Discovery link only: availability/rights vary by item and territory.
            source_url = f"https://archive.org/details/{quote(str(ia_ids[0]))}"
        books.append(Book(
            id=-int(olid), title=item.get("title") or "Untitled",
            author=", ".join(item.get("author_name") or []) or "Unknown",
            language=", ".join((item.get("language") or [])[:2]) or "?",
            subjects=(item.get("subject") or [])[:8],
            cover=f"https://covers.openlibrary.org/b/id/{cover}-M.jpg" if isinstance(cover, int) else None,
            formats={}, year=str(item.get("first_publish_year") or ""),
            source="Open Library", source_url=source_url,
        ))
    return books, (page * 10 < int(data.get("numFound", 0))), int(data.get("numFound", 0))

async def _search_google(query: str, page: int):
    if not query:
        query = "classic literature"
    data = await get_json(GOOGLE_BOOKS, {
        "q": query, "startIndex": (page - 1) * 10, "maxResults": 10,
        "printType": "books", "projection": "full",
    })
    books = []
    for item in data.get("items", []):
        volume_id = str(item.get("id") or "")
        if not volume_id:
            continue
        info = item.get("volumeInfo") or {}
        access = item.get("accessInfo") or {}
        image = info.get("imageLinks") or {}
        formats = {}
        # Google exposes downloadLink only for eligible downloadable volumes.
        for ext in ("epub", "pdf"):
            link = (access.get(ext) or {}).get("downloadLink")
            if link and str(link).startswith("https://"):
                formats[ext] = link
        books.append(Book(
            id=_synthetic_id(2_000_000_000, volume_id),
            title=info.get("title") or "Untitled",
            author=", ".join(info.get("authors") or []) or "Unknown",
            language=info.get("language") or "?",
            subjects=(info.get("categories") or [])[:8],
            cover=(image.get("thumbnail") or image.get("smallThumbnail") or "").replace("http://", "https://") or None,
            formats=formats,
            description=re.sub(r"<[^>]+>", " ", info.get("description") or "")[:1200],
            year=(info.get("publishedDate") or "")[:4],
            source="Google Books",
            source_url=info.get("infoLink") or f"https://books.google.com/books?id={quote(volume_id)}",
        ))
    count = int(data.get("totalItems", 0))
    return books, page * 10 < count, count

async def _search_archive(query: str, page: int):
    if query:
        safe_query = str(query).replace("\\\\", " ").replace('"', " ")
        archive_query = (
            f'mediatype:texts AND (title:"{safe_query}" OR '
            f'creator:"{safe_query}" OR subject:"{safe_query}")'
        )
    else:
        archive_query = "mediatype:texts AND collection:opensource"
    data = await get_json(IA_SEARCH, {
        "q": archive_query,
        "fl[]": ["identifier", "title", "creator", "year", "language", "subject"],
        "rows": 10, "page": page, "output": "json",
    })
    response = data.get("response") or {}
    books = []
    for item in response.get("docs", []):
        identifier = str(item.get("identifier") or "")
        if not identifier:
            continue
        creator = item.get("creator") or "Unknown"
        if isinstance(creator, list):
            creator = ", ".join(str(x) for x in creator[:3])
        language = item.get("language") or "?"
        if isinstance(language, list):
            language = ", ".join(language[:2])
        subjects = item.get("subject") or []
        if isinstance(subjects, str):
            subjects = [subjects]
        books.append(Book(
            id=_synthetic_id(3_000_000_000, identifier),
            title=item.get("title") or "Untitled", author=str(creator),
            language=str(language), subjects=subjects[:8], cover=None, formats={},
            year=str(item.get("year") or "")[:4], source="Internet Archive",
            source_url=f"https://archive.org/details/{quote(identifier)}",
        ))
    count = int(response.get("numFound", 0))
    return books, page * 10 < count, count


def _plain_wikisource_snippet(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return html.unescape(re.sub(r"\s+", " ", value)).strip()

async def _search_wikisource(query: str, page: int):
    if not query or not re.search(r"[а-яё]", query.casefold()):
        return [], False, 0
    data = await get_json(WIKISOURCE_API, {
        "action": "query", "list": "search", "srsearch": query,
        "srnamespace": 0, "srlimit": 10, "sroffset": (page - 1) * 10,
        "format": "json", "utf8": 1,
    })
    results = (data.get("query") or {}).get("search") or []
    total = int(((data.get("query") or {}).get("searchinfo") or {}).get("totalhits", 0))
    books = []
    for item in results:
        title = str(item.get("title") or "")
        if not title:
            continue
        books.append(Book(
            id=_synthetic_id(4_000_000_000, title),
            title=title, author="См. страницу произведения",
            language="ru", subjects=["Русская литература"],
            cover=None, formats={},
            description=_plain_wikisource_snippet(item.get("snippet", ""))[:900],
            source="Русская Викитека",
            source_url="https://ru.wikisource.org/wiki/" + quote(title.replace(" ", "_"), safe=""),
        ))
    return books, (page * 10 < total), total

async def _search_variants(provider, variants, page, *extra):
    collected, has_next, total = [], False, 0
    # Empty query means "browse": the providers have safe default catalog queries.
    terms = variants if any(variants) else [""]
    for variant in terms:
        try:
            books, more, count = await provider(variant, page, *extra)
            collected.extend(books)
            has_next = has_next or more
            total += count
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            log.warning("Catalog variant failed: %s", type(exc).__name__)
    return collected, has_next, total

async def search(query: str = "", topic: str = "", page: int = 1):
    if page < 1 or page > 100:
        raise ValueError("Invalid page")
    discovery_query = query or topic
    variants = search_variants(discovery_query) if discovery_query else [""]
    # Search both Cyrillic and translated variants to find Russian editions
    # alongside international editions of the same work. Genre requests also
    # flow into metadata catalogs instead of returning unrelated default titles.
    original_terms = list(dict.fromkeys([discovery_query, *variants])) if discovery_query else [""]
    translated_terms = list(dict.fromkeys([*variants, discovery_query])) if discovery_query else [""]
    jobs = [
        ("gutenberg", _search_gutenberg(query, topic, page)),
        ("openlibrary", _search_variants(_search_openlibrary, original_terms, page)),
        ("google", _search_variants(_search_google, translated_terms, page)),
        ("archive", _search_variants(_search_archive, original_terms, page)),
    ]
    if query and re.search(r"[а-яё]", query.casefold()):
        jobs.append(("wikisource", _search_wikisource(query, page)))
    results = await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)
    merged, seen = [], set()
    counts, next_flags = [], []
    for (name, _), result in zip(jobs, results):
        if isinstance(result, Exception):
            log.warning("%s catalog search failed: %s", name, type(result).__name__)
            continue
        books, has_next, count = result
        counts.append(count)
        next_flags.append(has_next)
        for book in books:
            key = (re.sub(r"\\W+", "", book.title.casefold()), re.sub(r"\\W+", "", book.author.casefold()))
            if key in seen:
                continue
            seen.add(key)
            merged.append(book)
    _remember(merged)
    # Counts overlap between catalogs; this is a rough discovery count, not unique total.
    return merged, any(next_flags), sum(counts)


async def get_book(book_id: int) -> Book:
    _load_cache()
    if 0 < book_id < 10_000_000:
        cached = _BOOK_CACHE.get(book_id)
        if cached:
            return _clone(cached)
        return parse_gutenberg(await get_json(f"{GUTENDEX}{book_id}/"))
    cached = _BOOK_CACHE.get(book_id)
    if cached:
        return _clone(cached)
    if -1_000_000_000 < book_id < 0:
        olid = -book_id
        work = await get_json(f"{OPENLIBRARY}/works/OL{olid}W.json")
        description = work.get("description") or ""
        if isinstance(description, dict):
            description = description.get("value", "")
        covers = work.get("covers") or []
        return Book(
            id=book_id, title=work.get("title") or "Untitled", author="Open Library",
            language="?", subjects=(work.get("subjects") or [])[:8],
            cover=f"https://covers.openlibrary.org/b/id/{covers[0]}-M.jpg" if covers else None,
            formats={}, description=str(description)[:1500], source="Open Library",
            source_url=f"{OPENLIBRARY}/works/OL{olid}W",
        )
    raise ValueError("Book record is not in the local catalog cache. Search for it again.")

async def enrich(book: Book) -> Book:
    """Fill missing year/cover/description from Open Library when possible."""
    if book.description and book.year and book.cover:
        return book
    try:
        data = await get_json(f"{OPENLIBRARY}/search.json", {
            "title": book.title, "author": book.author.split(",")[0], "limit": 5,
            "fields": "title,author_name,first_publish_year,key,cover_i",
        })
        docs = data.get("docs") or []
        normalized = book.title.casefold().strip()
        match = next((d for d in docs if d.get("title", "").casefold().strip() == normalized), None)
        if match:
            if not book.year and match.get("first_publish_year"):
                book.year = str(match["first_publish_year"])
            if not book.cover and match.get("cover_i"):
                book.cover = f"https://covers.openlibrary.org/b/id/{int(match['cover_i'])}-M.jpg"
            key = match.get("key", "")
            if not book.description and key.startswith("/works/"):
                work = await get_json(f"{OPENLIBRARY}{key}.json")
                desc = work.get("description", "")
                if isinstance(desc, dict):
                    desc = desc.get("value", "")
                if isinstance(desc, str):
                    book.description = desc[:1200]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        log.debug("Open Library enrichment unavailable", exc_info=True)
    _BOOK_CACHE[book.id] = book
    _save_cache()
    return book
