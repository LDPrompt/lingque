"""
🐦 灵雀 - 邮件监控 (P2)

功能:
- 定时检查新邮件
- 重要邮件主动通知
- 支持关键词过滤
"""

import asyncio
import imaplib
import email as email_lib
import logging
from datetime import datetime
from email.header import decode_header
from typing import Callable, List
from dataclasses import dataclass

logger = logging.getLogger("lingque.email_monitor")


@dataclass
class EmailMessage:
    """邮件消息"""
    msg_id: str
    subject: str
    sender: str
    date: str
    preview: str


class EmailMonitor:
    """
    邮件监控器

    功能:
    - 定时轮询检查新邮件
    - 根据关键词/发件人过滤重要邮件
    - 新邮件主动推送通知

    用法:
        monitor = EmailMonitor(config, notify_callback)
        monitor.add_important_sender("boss@company.com")
        monitor.add_important_keyword("紧急")
        await monitor.start()
    """

    def __init__(
        self,
        imap_host: str,
        imap_port: int,
        username: str,
        password: str,
        notify_callback: Callable,
        check_interval: int = 300,  # 默认 5 分钟检查一次
    ):
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.username = username
        self.password = password
        self.notify_callback = notify_callback
        self.check_interval = check_interval

        # 过滤规则
        self.important_senders: set[str] = set()
        self.important_keywords: set[str] = set()

        # 已处理的邮件 ID
        self._processed_ids: set[str] = set()
        self._running = False
        self._task: asyncio.Task = None
        self._notify_chat_id = ""  # 通知目标会话

    def set_notify_chat_id(self, chat_id: str):
        """设置通知目标会话 ID"""
        self._notify_chat_id = chat_id

    def add_important_sender(self, sender: str):
        """添加重要发件人"""
        self.important_senders.add(sender.lower())

    def add_important_keyword(self, keyword: str):
        """添加重要关键词"""
        self.important_keywords.add(keyword.lower())

    async def start(self):
        """启动邮件监控"""
        if not self.imap_host:
            logger.warning("邮件未配置，监控器未启动")
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"邮件监控已启动，每 {self.check_interval} 秒检查一次")

    async def stop(self):
        """停止邮件监控"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("邮件监控已停止")

    async def _monitor_loop(self):
        """监控循环"""
        while self._running:
            try:
                new_emails = await self._check_new_emails()
                important_emails = self._filter_important(new_emails)

                if important_emails and self._notify_chat_id:
                    await self._send_notifications(important_emails)

            except Exception as e:
                logger.error(f"检查邮件失败: {e}")

            await asyncio.sleep(self.check_interval)

    async def _check_new_emails(self) -> List[EmailMessage]:
        """检查新邮件"""
        def _sync_check():
            conn = None
            try:
                conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
                conn.login(self.username, self.password)
                conn.select("INBOX")

                _, nums = conn.search(None, "UNSEEN")
                msg_ids = nums[0].split()

                new_emails = []
                for mid in msg_ids[-10:]:
                    mid_str = mid.decode()
                    if mid_str in self._processed_ids:
                        continue

                    _, data = conn.fetch(mid, "(RFC822)")
                    if not data or not data[0] or not isinstance(data[0], tuple) or len(data[0]) < 2:
                        continue
                    msg = email_lib.message_from_bytes(data[0][1])

                    subject = self._decode_header(msg.get("Subject", "(无主题)"))
                    sender = self._decode_header(msg.get("From", ""))
                    date = msg.get("Date", "")

                    preview = self._get_body_preview(msg)

                    new_emails.append(EmailMessage(
                        msg_id=mid_str,
                        subject=subject,
                        sender=sender,
                        date=date,
                        preview=preview,
                    ))

                    self._processed_ids.add(mid_str)

                    # 防止 _processed_ids 无限增长
                    if len(self._processed_ids) > 10000:
                        to_remove = list(self._processed_ids)[:5000]
                        for item in to_remove:
                            self._processed_ids.discard(item)

                return new_emails

            except Exception as e:
                logger.error(f"IMAP 连接失败: {e}")
                return []
            finally:
                if conn:
                    try:
                        conn.logout()
                    except Exception:
                        pass

        return await asyncio.get_running_loop().run_in_executor(None, _sync_check)

    def _filter_important(self, emails: List[EmailMessage]) -> List[EmailMessage]:
        """过滤出重要邮件"""
        if not self.important_senders and not self.important_keywords:
            return emails  # 没有过滤规则，全部返回

        important = []
        for email in emails:
            # 检查发件人
            sender_lower = email.sender.lower()
            if any(s in sender_lower for s in self.important_senders):
                important.append(email)
                continue

            # 检查关键词
            subject_lower = email.subject.lower()
            if any(k in subject_lower for k in self.important_keywords):
                important.append(email)

        return important

    async def _send_notifications(self, emails: List[EmailMessage]):
        """发送新邮件通知"""
        for email in emails:
            msg = (
                f"📧 **新邮件通知**\n\n"
                f"**主题**: {email.subject}\n"
                f"**发件人**: {email.sender}\n"
                f"**时间**: {email.date}\n\n"
                f"**预览**: {email.preview[:200]}..."
            )
            try:
                await self.notify_callback(self._notify_chat_id, msg)
                logger.info(f"已发送邮件通知: {email.subject}")
            except Exception as e:
                logger.error(f"发送邮件通知失败: {e}")

    def _decode_header(self, raw: str) -> str:
        """解码邮件头"""
        parts = decode_header(raw or "")
        return "".join(
            p.decode(c or "utf-8", errors="replace") if isinstance(p, bytes) else p
            for p, c in parts
        )

    def _get_body_preview(self, msg) -> str:
        """获取邮件正文预览"""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                        break
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")

        return body[:300].replace("\n", " ").strip()

    def get_status(self) -> str:
        """获取监控状态"""
        return (
            f"📧 邮件监控状态\n"
            f"  运行中: {'是' if self._running else '否'}\n"
            f"  检查间隔: {self.check_interval}秒\n"
            f"  重要发件人: {len(self.important_senders)} 个\n"
            f"  重要关键词: {len(self.important_keywords)} 个\n"
            f"  已处理邮件: {len(self._processed_ids)} 封"
        )
