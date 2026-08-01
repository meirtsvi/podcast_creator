import os
import dotenv

from mailersend import MailerSendClient, EmailRequest

from podcast_creator.logger import logger

dotenv.load_dotenv()

API_KEY = os.getenv("MAILERSEND_API_KEY")
FROM_EMAIL = os.getenv("MAIL_FROM_EMAIL", "info@podcaster.dev")
FROM_NAME = os.getenv("MAIL_FROM_NAME", "Podcast Creator")


def send_email(send_to, subject, body):
    """Send a notification email.

    Notifications are best-effort: episode production must never fail because the mailer is
    unconfigured or the provider is down, so every failure here is logged and swallowed.
    """
    if not API_KEY or not send_to:
        logger.warning(f"Email not sent ('{subject}'). Set MAILERSEND_API_KEY and MAIL_SEND_TO "
                       f"in .env to enable notifications.")
        return

    logger.info(f"Sending email to {send_to} with subject {subject}")
    try:
        mailer = MailerSendClient(api_key=API_KEY)
        request = EmailRequest(**{
            "from": {"email": FROM_EMAIL, "name": FROM_NAME},
            "to": [{"email": send_to}],
            "subject": subject,
            "text": body,
        })
        response = mailer.emails.send(request)
        logger.info(f"Email sent: {response}")
    except Exception as e:
        # Deliberately does not reference the response: when the send raises, there is no
        # response to report, and referencing it here would mask the real error.
        logger.error(f"Failed to send mail to {send_to}: {e}")


if __name__ == '__main__':
    send_email(os.getenv("MAIL_SEND_TO"), "Test Email from Python Script",
               "This is a test email sent from the podcast_creator sendmail module.")
