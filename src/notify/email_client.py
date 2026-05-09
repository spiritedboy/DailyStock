"""SMTP 邮件通知（钉钉故障时的备用通道）。"""
from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import List, Optional

logger = logging.getLogger(__name__)


class EmailClient:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        sender: str,
        recipients: List[str],
        use_ssl: bool = True,
        timeout: int = 15,
    ):
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.sender = sender or user
        self.recipients = [r for r in (recipients or []) if r]
        self.use_ssl = use_ssl
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.user and self.recipients)

    def send(self, subject: str, body: str, html: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        msg = MIMEText(html or body, "html" if html else "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = formataddr(("DailyStock", self.sender))
        msg["To"] = ", ".join(self.recipients)
        try:
            if self.use_ssl:
                srv = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
            else:
                srv = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
                srv.starttls()
            try:
                srv.login(self.user, self.password)
                srv.sendmail(self.sender, self.recipients, msg.as_string())
            finally:
                srv.quit()
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("邮件发送失败: %s", e)
            return False
