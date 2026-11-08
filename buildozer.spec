[app]
title = Синева Mobile
package.name = sineva
package.domain = org.sineva
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ini
source.exclude_dirs = tests,bin,.buildozer,.github
version = 1.0
icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png
android.presplash_color = #141A2E
# sqlite3 нужен для индексов; в p4a он собирается со своим SQLite (FTS5/trigram может отсутствовать —
# тогда приложение само переходит на медленный, но рабочий перебор).
requirements = python3==3.11.5,hostpython3==3.11.5,kivy==2.3.0,sqlite3,android,pyjnius,androidstorage4kivy
orientation = portrait
fullscreen = 0
android.archs = arm64-v8a
android.api = 33
android.ndk = 25b
android.ndk_api = 24
android.minapi = 24
android.accept_sdk_license = True
# Книги лежат в Android/data/org.sineva.sineva/files/books (видны по USB), особых разрешений не нужно.
android.permissions =
android.allow_backup = True

# Проверенная связка: p4a этого релиза + Python 3.11 (новый p4a берёт Python 3.14, с которым Kivy 2.3 не собирается)
p4a.branch = v2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
