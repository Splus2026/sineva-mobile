[app]
title = Синева+
package.name = sineva
package.domain = org.sineva
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ini
source.exclude_dirs = tests,bin,.buildozer,.github
version = 0.1.0
# sqlite3 нужен для индексов; в p4a он собирается со своим SQLite (FTS5/trigram может отсутствовать —
# тогда приложение само переходит на медленный, но рабочий перебор).
requirements = python3,kivy==2.3.0,sqlite3,androidstorage4kivy,pyjnius
orientation = portrait
fullscreen = 0
android.archs = arm64-v8a
android.api = 34
android.minapi = 24
android.accept_sdk_license = True
# Книги лежат в Android/data/org.sineva.sineva/files/books (видны по USB), особых разрешений не нужно.
android.permissions =
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
