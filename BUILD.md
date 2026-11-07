# Синева+ Mobile — сборка APK

Приложение: Kivy + Python, поиск FTS5 (триграммы) по книгам .txt/.docx, лежащим на телефоне.
Векторные модели заложены в архитектуре (`sineva/vector/`), но не реализованы.

## Сборка без своего Linux — GitHub Actions
1. Создайте репозиторий на GitHub и загрузите в него содержимое этой папки.
2. Вкладка **Actions → Build APK → Run workflow**.
3. Через 15–40 минут скачайте артефакт `sineva-apk` — внутри `.apk`.
4. Скопируйте APK на телефон, разрешите установку из неизвестных источников.

## Сборка в WSL (Ubuntu)
```
sudo apt install -y git zip unzip openjdk-17-jdk autoconf libtool pkg-config zlib1g-dev libncurses5-dev libtinfo5 cmake libffi-dev libssl-dev
pip install buildozer cython==0.29.36
buildozer -v android debug     # APK появится в bin/
```

## Книги на телефоне
- Кнопка «Добавить» на вкладке «Книги» (системный выбор файлов) копирует книгу в папку приложения.
- Либо по USB положите файлы в `Android/data/org.sineva.sineva/files/books`, затем нажмите ↻.
- `mconfig.ini` (алиасы, разделители) того же формата, что на ПК; секцию `[Bases]` можно перенести как есть.

## Проверка на ПК без телефона
```
python tests/run_core_tests.py      # ядро и логика экранов
pip install kivy && python main.py  # интерфейс в окне (книги: ~/.sineva_mobile)
```

## Известные риски первой сборки
- Android-часть (выбор файлов, пути, SQLite в p4a) в разработке не запускалась на устройстве.
- Если в SQLite p4a нет FTS5/trigram, приложение работает перебором (медленнее на больших книгах).
- Индексация больших книг идёт в фоне; на телефоне она медленнее, чем на ПК.
