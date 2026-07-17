from __future__ import annotations

import base64
import os
import subprocess
from typing import Any, Callable
from xml.sax.saxutils import escape


class WindowsNotifier:
    def __init__(self, *, runner: Callable[..., Any] = subprocess.run):
        self.runner = runner

    def notify(self, title: str, message: str) -> None:
        safe_title = escape(title)
        safe_message = escape(message)
        script = f"""
$template = @'
<toast><visual><binding template="ToastGeneric"><text>{safe_title}</text><text>{safe_message}</text></binding></visual></toast>
'@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Expense Agent').Show($toast)
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.runner(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-EncodedCommand",
                encoded,
            ],
            check=False,
            capture_output=True,
            creationflags=creation_flags,
        )


class ConsoleNotifier:
    def notify(self, title: str, message: str) -> None:
        print(f"{title}: {message}")


def default_notifier() -> WindowsNotifier | ConsoleNotifier:
    return WindowsNotifier() if os.name == "nt" else ConsoleNotifier()

