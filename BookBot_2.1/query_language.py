"""Russian title aliases and transliteration for Gutenberg searches."""
import re

ALIASES = {
    'война и мир': 'War and Peace',
    'анна каренина': 'Anna Karenina',
    'преступление и наказание': 'Crime and Punishment',
    'идиот': 'The Idiot',
    'братья карамазовы': 'The Brothers Karamazov',
    'отцы и дети': 'Fathers and Sons',
    'евгений онегин': 'Eugene Onegin',
    'капитанская дочка': "The Daughter of the Commandant",
    'мертвые души': 'Dead Souls',
    'герой нашего времени': 'A Hero of Our Time',
    'мастер и маргарита': 'The Master and Margarita',
    'гордость и предубеждение': 'Pride and Prejudice',
    'алиса в стране чудес': "Alice's Adventures in Wonderland",
    'франкенштейн': 'Frankenstein',
    'дракула': 'Dracula',
    'шерлок холмс': 'Sherlock Holmes',
    'моби дик': 'Moby Dick',
    'джейн эйр': 'Jane Eyre',
    'граф монте кристо': 'The Count of Monte Cristo',
    'три мушкетера': 'The Three Musketeers',
    'маленькие женщины': 'Little Women',
    'обломов': 'Oblomov',
    'белые ночи': 'White Nights',
    'записки из подполья': 'Notes from Underground',
    'кавказский пленник': 'The Prisoner of the Caucasus',
    'пиковая дама': 'The Queen of Spades',
    'русалка': 'The Mermaid',
    'ревизор': 'The Government Inspector',
    'шинель': 'The Overcoat',
    'записки охотника': 'A Sportsman Sketches',
    'накануне': 'On the Eve',
    'дым': 'Smoke',
}
AUTHOR_ALIASES = {
    'лев толстой': 'Tolstoy', 'толстой': 'Tolstoy',
    'федор достоевский': 'Dostoyevsky', 'достоевский': 'Dostoyevsky',
    'александр пушкин': 'Pushkin', 'пушкин': 'Pushkin',
    'николай гоголь': 'Gogol', 'гоголь': 'Gogol',
    'иван тургенев': 'Turgenev', 'тургенев': 'Turgenev',
    'михаил лермонтов': 'Lermontov', 'лермонтов': 'Lermontov',
    'иван гончаров': 'Goncharov', 'гончаров': 'Goncharov',
    'антон чехов': 'Chekhov', 'чехов': 'Chekhov',
    'максим горький': 'Gorky', 'горький': 'Gorky',
    'александр куприн': 'Kuprin', 'куприн': 'Kuprin',
    'иван бунин': 'Bunin', 'бунин': 'Bunin',
    'джейн остин': 'Austen', 'остин': 'Austen',
}
TRANS = str.maketrans(dict(zip(
    'абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
    ['a','b','v','g','d','e','yo','zh','z','i','y','k','l','m','n','o','p','r','s','t','u','f','kh','ts','ch','sh','shch','','y','','e','yu','ya'])))

def normalize(text):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', text.casefold().replace('ё', 'е'))).strip()

def search_variants(query):
    """Prefer known English titles; keep original and transliterated fallbacks."""
    q = normalize(query)
    variants = []
    for ru, en in sorted(ALIASES.items(), key=lambda x: -len(x[0])):
        if re.search(r'(?<!\w)' + re.escape(ru) + r'(?!\w)', q):
            variants.append(en)
            break
    if not variants:
        for ru, en in sorted(AUTHOR_ALIASES.items(), key=lambda x: -len(x[0])):
            if re.search(r'(?<!\w)' + re.escape(ru) + r'(?!\w)', q):
                variants.append(en)
                break
    variants.append(query)
    if re.search('[а-яё]', query.casefold()):
        variants.append(query.casefold().translate(TRANS))
    return list(dict.fromkeys(variants))
