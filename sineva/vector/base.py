"""Интерфейс векторных моделей (смыслового поиска). В первой версии НЕ реализован — только архитектура.

Как добавить модель позже:
  1. Класс-наследник VectorModel (например, FastTextLite на numpy или LSA на numpy/scipy — они есть в p4a;
     gensim/torch на Android не нужны: модель можно обучить на ПК и скопировать на телефон готовым файлом).
  2. Зарегистрировать: registry.register_model_type('fasttext', FastTextLite).
  3. Прописать модель в mconfig.ini, раздел [VectorModels]:  Имя = id, тип, путь_к_файлу_модели
Интерфейс приложения (список моделей, кнопка "Векторы", панель соседей) уже работает через этот интерфейс.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple


class VectorUnavailable(Exception):
    """Векторный поиск сейчас недоступен (нет моделей, модель не загружена, не хватает библиотек)."""


@dataclass
class VectorHit:
    """Результат смыслового поиска: топик книги и близость к запросу (0..1)."""
    index_id: str          # имя файла книги (ключ в [Bases])
    rowid: int
    score: float


class VectorModel(ABC):
    #: 'word'     — векторы слов (как FastText): умеет neighbors(); поиск идёт по словам запроса;
    #: 'sentence' — векторы предложений/абзацев (как LSA, Paraphrase): умеет search() по тексту запроса
    kind: str = 'word'
    type_name: str = ''          # ключ в реестре и в mconfig.ini

    def __init__(self, model_id: str, display_name: str, path: str):
        self.model_id = model_id
        self.display_name = display_name
        self.path = path

    @classmethod
    def is_supported(cls) -> bool:
        """Хватает ли на этом устройстве библиотек для работы модели."""
        return True

    @abstractmethod
    def load(self) -> None:
        """Загружает готовую модель из self.path."""

    def is_loaded(self) -> bool:
        return False

    def train(self, topics: Iterable[Tuple[str, int, str]],
              progress: Optional[Callable[[int, int], None]] = None) -> None:
        """Обучение на топиках (index_id, rowid, текст). На телефоне обычно не нужно — модель готовят на ПК."""
        raise VectorUnavailable("Обучение этой модели на устройстве не поддерживается")

    def neighbors(self, word: str, topn: int = 15) -> List[Tuple[str, float]]:
        """Близкие слова с близостью — для моделей kind='word'."""
        raise VectorUnavailable("Модель не умеет искать соседей слов")

    def search(self, query: str, index_ids: List[str], topn: int = 50) -> List[VectorHit]:
        """Топики, смыслово близкие к запросу, — для моделей kind='sentence' (и 'word' по кругу слов)."""
        raise VectorUnavailable("Модель не умеет искать по смыслу")
