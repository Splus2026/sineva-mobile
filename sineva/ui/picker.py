"""Выбор файлов-книг: на Android — системный выбор (SAF) через androidstorage4kivy, на ПК — окно Kivy."""
import os

from ..paths import is_android


def pick_files(on_paths, parent_popup_factory=None):
    """on_paths(list[str]) вызывается с путями файлов, уже доступных приложению для чтения."""
    if is_android():
        try:
            from androidstorage4kivy import Chooser, SharedStorage
            holder = {}

            def cb(uris):
                ss = SharedStorage()
                paths = []
                for u in uris or []:
                    try:
                        p = ss.copy_from_shared(u)      # копия во внутреннем хранилище приложения
                        if p:
                            paths.append(p)
                    except Exception:
                        pass
                on_paths(paths)
            holder['chooser'] = Chooser(cb)
            holder['chooser'].choose_content('*/*', multiple=True)
            pick_files._holder = holder           # не даём сборщику мусора убить Chooser
            return
        except Exception:
            pass                                   # нет библиотеки — пробуем окно Kivy
    _desktop_chooser(on_paths)


def _desktop_chooser(on_paths):
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.button import Button
    from kivy.uix.filechooser import FileChooserListView
    from kivy.uix.popup import Popup

    box = BoxLayout(orientation='vertical')
    fc = FileChooserListView(path=os.path.expanduser('~'), multiselect=True,
                             filters=['*.txt', '*.docx', '*.TXT', '*.DOCX'])
    row = BoxLayout(size_hint_y=None, height='48dp')
    ok, cancel = Button(text='Добавить'), Button(text='Отмена')
    row.add_widget(ok)
    row.add_widget(cancel)
    box.add_widget(fc)
    box.add_widget(row)
    pop = Popup(title='Выберите книги', content=box, size_hint=(0.95, 0.9))
    ok.bind(on_release=lambda *_: (pop.dismiss(), on_paths(list(fc.selection))))
    cancel.bind(on_release=lambda *_: pop.dismiss())
    pop.open()
