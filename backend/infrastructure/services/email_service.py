import asyncio
import logging
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

from config.redis import get_redis_client
from config.settings import settings

logger = logging.getLogger(__name__)


class EmailService:
    """Email service using SMTP protocol."""

    def _generate_code(self, length: int = 6) -> str:
        """Generate a random numeric verification code."""
        return ''.join(secrets.choice("0123456789") for _ in range(length))

    def _send_email_smtp(self, to_email: str, subject: str, html_body: str) -> bool:
        """
        Send email via SMTP protocol.
        
        Args:
            to_email: Recipient email address
            subject: Email subject
            html_body: HTML email body
            
        Returns:
            True if successful, False otherwise
        """
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = f'{settings.SMTP_FROM_NAME} <{settings.SMTP_USERNAME}>'
            msg['To'] = to_email
            msg['Subject'] = Header(subject, 'utf-8')

            html_part = MIMEText(html_body, 'html', 'utf-8')
            msg.attach(html_part)

            if settings.SMTP_USE_SSL:
                with smtplib.SMTP_SSL(
                    settings.SMTP_HOST,
                    settings.SMTP_PORT,
                    timeout=settings.SMTP_TIMEOUT,
                ) as server:
                    server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                    server.sendmail(settings.SMTP_USERNAME, [to_email], msg.as_string())
            else:
                with smtplib.SMTP(
                    settings.SMTP_HOST,
                    settings.SMTP_PORT,
                    timeout=settings.SMTP_TIMEOUT,
                ) as server:
                    server.starttls()
                    server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                    server.sendmail(settings.SMTP_USERNAME, [to_email], msg.as_string())

            logger.info(f"Email sent successfully to {to_email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            return False

    async def send_verification_code(self, email: str) -> bool:
        """
        Generate a code, store it in Redis, and send it via email.
        
        Args:
            email: Recipient email address
            
        Returns:
            True if successful, False otherwise
        """
        code = self._generate_code()
        redis_client = await get_redis_client()
        key = f"verify_code:{email}"
        await redis_client.set(key, code, ex=300)

        subject = "【Lumen】您的验证码"
        html_body = f"""
        <div style="background-color:#f5f5f5; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.05);">
                <div style="background-color: #000000; padding: 20px; text-align: center;">
                    <h1 style="color: #ffffff; margin: 0; font-family: monospace;">LUM<span style="color: #06b6d4;">EN</span></h1>
                </div>
                <div style="padding: 30px;">
                    <p style="color: #333333; font-size: 16px;">尊敬的用户：</p>
                    <p style="color: #666666; font-size: 14px; line-height: 1.6;">
                        您正在进行身份验证。请使用以下验证码完成操作：
                    </p>
                    <div style="background-color: #f0fdfa; border: 1px solid #ccfbf1; border-radius: 4px; padding: 15px; margin: 20px 0; text-align: center;">
                        <span style="font-size: 24px; font-weight: bold; letter-spacing: 4px; color: #0f766e;">{code}</span>
                    </div>
                    <p style="color: #999999; font-size: 12px;">
                        验证码有效期为 5 分钟。如果这不是您的操作，请忽略此邮件。
                    </p>
                </div>
                <div style="background-color: #fafafa; padding: 15px; text-align: center; border-top: 1px solid #eeeeee;">
                    <p style="color: #999999; font-size: 12px; margin: 0;">&copy; 2025 Lumen. All rights reserved.</p>
                </div>
            </div>
        </div>
        """

        success = await asyncio.to_thread(self._send_email_smtp, email, subject, html_body)
        if not success:
            await redis_client.delete(key)

        if settings.DEBUG and not success:
            logger.warning(f"[DEBUG] Verification code for {email}: {code}")
            return True

        return success

    async def verify_code(self, email: str, code: str) -> bool:
        """
        Verify the code provided by the user.
        Deletes the code from Redis upon successful verification.
        
        Args:
            email: User email address
            code: Verification code to verify
            
        Returns:
            True if valid, False otherwise
        """
        redis_client = await get_redis_client()
        key = f"verify_code:{email}"
        stored_code = await redis_client.get(key)

        if stored_code and stored_code == code:
            await redis_client.delete(key)
            return True
        return False
