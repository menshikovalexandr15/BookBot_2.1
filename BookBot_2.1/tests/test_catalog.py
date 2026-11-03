import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import catalog
from query_language import search_variants
from storage import Storage


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_archive_blank_query_uses_collection_query(self):
        response = {"response": {"docs": [], "numFound": 0}}
        with patch.object(catalog, "get_json", new=AsyncMock(return_value=response)) as get:
            await catalog._search_archive("", 1)
        self.assertEqual(get.await_args.args[0], catalog.IA_SEARCH)
        self.assertEqual(get.await_args.args[1]["q"], "mediatype:texts AND collection:opensource")

    async def test_archive_query_searches_title_creator_and_subject(self):
        response = {"response": {"docs": [], "numFound": 0}}
        with patch.object(catalog, "get_json", new=AsyncMock(return_value=response)) as get:
            await catalog._search_archive("Tolstoy", 1)
        query = get.await_args.args[1]["q"]
        self.assertIn('title:"Tolstoy"', query)
        self.assertIn('creator:"Tolstoy"', query)
        self.assertIn('subject:"Tolstoy"', query)

    async def test_multisource_search_survives_provider_failure_and_deduplicates(self):
        gutenberg = catalog.Book(1, "Same Book", "Author", "en", [], None, {}, source="Gutenberg")
        openlib = catalog.Book(-12, "Same Book", "Author", "en", [], None, {}, source="Open Library")
        google = catalog.Book(2_000_000_001, "Other Book", "Writer", "en", [], None, {}, source="Google Books")
        with patch.object(catalog, "_search_gutenberg", new=AsyncMock(return_value=([gutenberg], False, 1))), \
             patch.object(catalog, "_search_variants", new=AsyncMock(side_effect=[
                 ([openlib], False, 1),
                 ([google], False, 1),
                 ([], False, 0),
             ])), \
             patch.object(catalog, "_remember"):
            books, has_next, count = await catalog.search("test")
        self.assertEqual([b.title for b in books], ["Same Book", "Other Book"])
        self.assertFalse(has_next)
        self.assertEqual(count, 3)

    async def test_empty_search_browses_default_provider_catalog(self):
        provider = AsyncMock(return_value=([], False, 0))
        await catalog._search_variants(provider, [""], 1)
        provider.assert_awaited_once_with("", 1)

    async def test_russian_query_has_english_alias_and_original(self):
        variants = search_variants("Война и мир")
        self.assertEqual(variants[0], "War and Peace")
        self.assertIn("Война и мир", variants)

    async def test_synthetic_ids_are_in_separate_provider_ranges(self):
        ids = [catalog._synthetic_id(prefix, "same-external-id")
               for prefix in (2_000_000_000, 3_000_000_000, 4_000_000_000)]
        self.assertEqual(len(set(ids)), 3)
        self.assertTrue(all(i > 10_000_000 for i in ids))


class CacheTests(unittest.TestCase):
    def test_cache_persists_negative_openlibrary_ids(self):
        book = catalog.Book(-123, "Russian Novel", "Author", "ru", ["Novel"], None, {},
                            source="Open Library", source_url="https://openlibrary.org/works/OL123W")
        old_cache, old_path = catalog._BOOK_CACHE, catalog.CACHE_PATH
        with tempfile.TemporaryDirectory() as tmp:
            try:
                catalog._BOOK_CACHE = {-123: book}
                catalog.CACHE_PATH = Path(tmp) / "catalog_cache.json"
                catalog._save_cache()
                catalog._BOOK_CACHE = {}
                catalog._load_cache()
                self.assertIn(-123, catalog._BOOK_CACHE)
                self.assertEqual(catalog._BOOK_CACHE[-123].author, "Author")
            finally:
                catalog._BOOK_CACHE, catalog.CACHE_PATH = old_cache, old_path

    def test_storage_favorites_and_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Storage(str(Path(tmp) / "books.sqlite3"))
            db.set_language(77, "en")
            self.assertEqual(db.language(77), "en")
            book = catalog.Book(77, "A Book", "An Author", "en", [], None, {})
            self.assertTrue(db.toggle_favorite(77, book))
            self.assertEqual(db.favorites(77)[0][1:], ("A Book", "An Author"))
            self.assertFalse(db.toggle_favorite(77, book))


if __name__ == "__main__":
    unittest.main()
