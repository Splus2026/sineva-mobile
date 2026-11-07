"""Реестр векторных моделей: типы (классы) и модели, описанные в mconfig.ini."""
from typing import Dict, List, Optional, Type

from .base import VectorModel, VectorUnavailable

_TYPES: Dict[str, Type[VectorModel]] = {}


def register_model_type(type_name: str, cls: Type[VectorModel]) -> None:
    cls.type_name = type_name
    _TYPES[type_name] = cls


def available_types() -> List[str]:
    return sorted(t for t, c in _TYPES.items() if c.is_supported())


class VectorRegistry:
    """Модели из раздела [VectorModels] mconfig.ini: 'Имя = id, тип, путь' (как в Синеве+ для ПК)."""

    def __init__(self, config=None, base_dir: str = ''):
        self.models: Dict[str, VectorModel] = {}
        self.problems: List[str] = []          # что не удалось подключить — для экрана настроек
        if config is not None:
            self.reload(config, base_dir)

    def reload(self, config, base_dir: str = '') -> None:
        import os
        self.models.clear()
        self.problems.clear()
        parser = getattr(config, '_parser', None)
        if parser is None or not parser.has_section('VectorModels'):
            return
        for display, value in parser.items('VectorModels'):
            parts = [x.strip() for x in value.split(',')]
            if len(parts) < 3:
                self.problems.append(f"{display}: ожидается 'id, тип, путь'")
                continue
            model_id, type_name, path = parts[0], parts[1], ','.join(parts[2:])
            cls = _TYPES.get(type_name)
            if cls is None:
                self.problems.append(f"{display}: тип '{type_name}' в этой версии не поддерживается")
                continue
            if not cls.is_supported():
                self.problems.append(f"{display}: на этом устройстве не хватает библиотек для '{type_name}'")
                continue
            full = path if os.path.isabs(path) else os.path.join(base_dir, path.replace('\\', os.sep))
            self.models[model_id] = cls(model_id, display, full)

    def list(self) -> List[VectorModel]:
        return list(self.models.values())

    def get(self, model_id: str) -> Optional[VectorModel]:
        return self.models.get(model_id)

    def require(self, model_id: Optional[str] = None) -> VectorModel:
        """Модель для поиска или VectorUnavailable с понятным пояснением."""
        if not self.models:
            raise VectorUnavailable("Векторные модели в этой версии ещё не подключены")
        model = self.models.get(model_id) if model_id else next(iter(self.models.values()))
        if model is None:
            raise VectorUnavailable(f"Модель '{model_id}' не найдена")
        if not model.is_loaded():
            model.load()
        return model
