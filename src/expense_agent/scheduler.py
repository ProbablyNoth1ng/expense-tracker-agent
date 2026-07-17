from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape


def build_task_xml(*, executable: str, working_directory: str, schedule_time: str, arguments: str = "sync") -> str:
    hour, minute = schedule_time.split(":", maxsplit=1)
    command = escape(executable)
    working = escape(working_directory)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Sync approved and new Monobank expenses</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>2026-07-17T{hour}:{minute}:00</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable><ExecutionTimeLimit>PT2H</ExecutionTimeLimit><Enabled>true</Enabled></Settings>
  <Actions Context="Author"><Exec><Command>{command}</Command><Arguments>{escape(arguments)}</Arguments><WorkingDirectory>{working}</WorkingDirectory></Exec></Actions>
</Task>"""


def install_task(*, project_dir: Path, schedule_time: str = "23:59") -> None:
    executable = Path(sys.executable).with_name("expense-agent.exe")
    if not executable.exists():
        executable = Path(sys.executable)
        arguments = "-m expense_agent sync"
    else:
        arguments = "sync"
    xml = build_task_xml(
        executable=str(executable),
        working_directory=str(project_dir.resolve()),
        schedule_time=schedule_time,
        arguments=arguments,
    )
    with tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-16", delete=False) as handle:
        handle.write(xml)
        xml_path = Path(handle.name)
    try:
        subprocess.run(
            ["schtasks.exe", "/Create", "/TN", "Monobank Expense Agent", "/XML", str(xml_path), "/F"],
            check=True,
        )
    finally:
        xml_path.unlink(missing_ok=True)
