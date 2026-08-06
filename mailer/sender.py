"""SMTP 邮件发送（QQ 邮箱默认配置，支持 SSL）"""
import os
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from utils.logger import get_logger

log = get_logger("mailer")


def _load_env(config: dict) -> dict:
    """合并 .env 中的敏感配置（授权码等）

    优先级：.env 环境变量 > config.yaml（config.yaml 中 sender 是占位符，必须允许被覆盖）
    """
    cfg = dict(config)
    cfg["sender"] = os.getenv("MAIL_SENDER") or cfg.get("sender", "")
    cfg["auth_code"] = os.getenv("MAIL_AUTH_CODE") or cfg.get("auth_code", "")
    receivers = list(cfg.get("receivers") or [])
    env_receivers = os.getenv("MAIL_RECEIVERS", "")
    if env_receivers:
        receivers += [r.strip() for r in env_receivers.split(",") if r.strip()]
    cfg["receivers"] = list(dict.fromkeys(receivers))
    return cfg


def build_message(subject: str, text_body: str, html_body: str = "", attach_path: str = "") -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    if attach_path and os.path.exists(attach_path):
        from email.mime.base import MIMEBase
        from email import encoders

        with open(attach_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=("utf-8", "", os.path.basename(attach_path)),
            )
            msg.attach(part)
    return msg


def send_email(cfg: dict, subject: str, text_body: str, html_body: str = "", attach_path: str = "") -> bool:
    """发送邮件。cfg: {smtp_host, smtp_port, use_ssl, sender, auth_code, receivers}"""
    cfg = _load_env(cfg)
    if not cfg.get("sender") or not cfg.get("auth_code"):
        log.error("缺少发件邮箱或 SMTP 授权码（检查 .env 的 MAIL_SENDER / MAIL_AUTH_CODE）")
        return False
    if not cfg.get("receivers"):
        log.error("未配置收件人")
        return False

    msg = build_message(subject, text_body, html_body, attach_path)
    msg["From"] = formataddr(("农村集体案例口播机器人", cfg["sender"]))
    msg["To"] = ", ".join(cfg["receivers"])

    try:
        if cfg.get("use_ssl", True):
            server = smtplib.SMTP_SSL(cfg["smtp_host"], int(cfg.get("smtp_port", 465)), timeout=30)
        else:
            server = smtplib.SMTP(cfg["smtp_host"], int(cfg.get("smtp_port", 25)), timeout=30)
            server.starttls()
        server.login(cfg["sender"], cfg["auth_code"])
        server.sendmail(cfg["sender"], cfg["receivers"], msg.as_string())
        server.quit()
        log.info("邮件已发送至 %s", cfg["receivers"])
        return True
    except Exception as e:
        log.error("邮件发送失败: %s", e)
        return False
