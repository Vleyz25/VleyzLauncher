# Как помочь проекту

Спасибо, что зашли. Лаунчер небольшой, поэтому любая помощь заметна.

## Сообщить об ошибке

Откройте [Issue](https://github.com/Vleyz25/VleyzLauncher/issues/new/choose) и выберите «Ошибка».
Приложите версию лаунчера, версию Windows, шаги и последние строки лога из
`%APPDATA%\VleyzLauncher\logs\launcher.log`. Без лога починка занимает сильно больше времени.

## Предложить идею

Там же — «Идея». Опишите задачу, а не только решение: что вы хотели сделать и что мешает.

## Pull request

- Ветка от `main`, один смысловой кусок на PR.
- Код в стиле проекта: `from __future__ import annotations`, понятные имена, комментарии по делу.
- Логика — в `vleyzlauncher/core/`, интерфейс — в `vleyzlauncher/ui/`.
- Перед отправкой:

```bash
python -m compileall -q main.py vleyzlauncher tools
python tools/check_version.py
python -m unittest discover -s tests -v
```

- Если меняете поведение — обновите `README.md` и `CHANGELOG.md`.

## Чего лучше не делать

- Добавлять телеметрию, рекламу и автообновление без согласия пользователя.
- Класть в репозиторий игру, ассеты Mojang, JRE или чужие моды.
- Обещать «бесплатный Minecraft»: проект работает с аккаунтом пользователя и EULA Mojang.
