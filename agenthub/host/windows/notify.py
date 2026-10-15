# -*- coding: utf-8 -*-
"""读取 Windows 通知中心的通知。

为什么不走微软官方的 UserNotificationListener：那个 API 只允许 Microsoft Store
（MSIX）打包的应用调用，普通 exe 拿不到权限。这里改走另一条路——以只读方式
打开通知中心背后的 SQLite 数据库（wpndatabase.db），轮询增量记录。效果等价，
而且不需要把程序打包成 MSIX。

通知的 toast XML 正文里直接带着文案，所以能进一步区分「任务完成」和「等待审批」：

    WorkBuddy : 任务已完成  /  任务等待确认，请处理
    Trae      : 任务完成 · xxx  /  等待操作

这点很关键——「要你点确认」必须比「跑完了」更醒目。
"""

from __future__ import annotations

import os
import re
import sqlite3
import time

from ...models import Notice, classify, match_agent, parse_toast

#: 通知库的位置。注意它是**滚动日志**（默认只留最近 20 条），不是待处理队列。
DB_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "Microsoft", "Windows", "Notifications", "wpndatabase.db",
)


def _filetime_to_unix(value) -> float:
    """Windows FILETIME（1601 纪元的 100 纳秒计数）转 unix 时间戳。"""
    try:
        return int(value) / 1e7 - 11644473600
    except (TypeError, ValueError):
        return 0.0


class WinNotificationSource:
    """轮询 Windows 通知库，把通知解析成结构化的 Notice。

    以 Notification 表的自增 Order 作游标拿增量。表是滚动日志，老记录可能
    早就被处理过，所以启动时只回看最近 LOOKBACK 秒内的记录。
    """

    LOOKBACK = 30 * 60      # 启动时回看最近 30 分钟，避免漏掉刚发出的审批

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        self.available = bool(db_path) and os.path.isfile(db_path)
        self._cursor = 0
        self._primed = False
        self._handlers: dict[str, str] = {}

    # ---------------- 内部

    def _connect(self) -> sqlite3.Connection:
        uri = "file:%s?mode=ro" % self.db_path.replace("\\", "/")
        return sqlite3.connect(uri, uri=True, timeout=1.0)

    def _load_handlers(self, cur: sqlite3.Cursor) -> None:
        """HandlerId -> PrimaryId（如 WorkBuddy.WorkBuddy）。"""
        cur.execute("SELECT RecordId, PrimaryId FROM NotificationHandler")
        for record_id, primary_id in cur.fetchall():
            self._handlers[str(record_id)] = str(primary_id)

    def _rows_to_notices(self, rows) -> list[Notice]:
        notices: list[Notice] = []
        for order, handler_id, payload, arrival in rows:
            key = match_agent(self._handlers.get(str(handler_id), ""))
            if not key:
                continue
            title, body = parse_toast(payload)
            notices.append(Notice(
                order=int(order),
                agent_key=key,
                kind=classify(title, body),
                title=title,
                body=body,
                at=_filetime_to_unix(arrival),
            ))
        return notices

    # ---------------- 对外

    @property
    def cursor(self) -> int:
        """当前已读到的最大 Order，可持久化下来避免旧通知重复提醒。"""
        return self._cursor

    def prime(self) -> list[Notice]:
        """首次调用：记住当前水位，并回看最近一段时间的通知。

        返回回看窗口内命中的通知（按时间升序），供界面初始化未读/审批状态——
        这样即使程序刚打开，也能看到刚刚发出的那条审批请求。
        """
        if not self.available:
            return []
        try:
            con = self._connect()
        except sqlite3.Error:
            return []

        seeded: list[Notice] = []
        try:
            cur = con.cursor()
            self._load_handlers(cur)
            cur.execute("SELECT COALESCE(MAX([Order]), 0) FROM Notification")
            self._cursor = int(cur.fetchone()[0] or 0)

            cutoff = time.time() - self.LOOKBACK
            cur.execute(
                """SELECT [Order], HandlerId, Payload, ArrivalTime
                   FROM Notification WHERE [Order] >= 1 ORDER BY [Order]""",
            )
            recent = [row for row in cur.fetchall()
                      if _filetime_to_unix(row[3]) >= cutoff]
            seeded = self._rows_to_notices(recent)
            self._primed = True
        except sqlite3.Error:
            pass
        finally:
            con.close()
        return seeded

    def poll(self) -> list[Notice]:
        """返回本轮新增的通知。"""
        if not self.available:
            return []
        if not self._primed:
            self.prime()
            return []

        try:
            con = self._connect()
        except sqlite3.Error:
            return []

        notices: list[Notice] = []
        try:
            cur = con.cursor()
            self._load_handlers(cur)
            cur.execute(
                """SELECT [Order], HandlerId, Payload, ArrivalTime
                   FROM Notification WHERE [Order] > ? ORDER BY [Order]""",
                (self._cursor,),
            )
            rows = cur.fetchall()
            for order, _hid, _payload, _arrival in rows:
                self._cursor = max(self._cursor, int(order))
            notices = self._rows_to_notices(rows)
        except sqlite3.Error:
            pass
        finally:
            con.close()
        return notices
