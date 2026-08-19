#!/bin/sh
set -e

# Именованный том tg_session монтируется в /data от root — Telethon не может
# создать session.session без записи в эту директорию.
mkdir -p /data
chown -R appuser:appuser /data

exec gosu appuser "$@"
