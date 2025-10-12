import os
import dotenv

from mailersend import MailerSendClient, EmailRequest

from podcast_creator.logger import logger

dotenv.load_dotenv()
api_key = os.getenv('MAILERSEND_API_KEY')

def send_email(send_to, subject, body):
    logger.info(f"Sending email to {send_to} with subject {subject}")
    try:
        mailer = MailerSendClient(api_key=api_key)
        message = {
            'from': {
                'email': 'info@podcaster.dev',
                'name': 'Meir Tsvi'
            },
            'to': [
                {
                    'email': send_to,
                    'name': 'Meir'
                }
            ],
        'subject': subject,
        'text': body,
        }
        req = EmailRequest(**message)
        response = mailer.emails.send(req)
        logger.info(f"Email sent: {response}")

    except Exception as e:
            logger.error(f"Failed to send mail due to: {str(e)}. Response: {response}")


if __name__ == '__main__':
    send_to = "meir.tsvi@gmail.com"
    subject = "Test Email from Python Script"
    body = "<h1>This is a test email</h1><p>Sent from a Python script using Mailsender API.</p>"
    send_email(send_to, subject, body)

