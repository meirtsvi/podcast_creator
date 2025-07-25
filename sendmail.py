# import os
# import base64
# from email.message import EmailMessage
# from google.auth.transport.requests import Request
# from google.oauth2.credentials import Credentials
# from googleapiclient.discovery import build
#
# import dotenv
# from logger import logger
#
# dotenv.load_dotenv()
#
# def get_service_from_env():
#     creds = Credentials(
#         None,
#         refresh_token=os.getenv("GMAIL_REFRESH_TOKEN"),
#         client_id=os.getenv("GMAIL_CLIENT_ID"),
#         client_secret=os.getenv("GMAIL_CLIENT_SECRET"),
#         token_uri='https://oauth2.googleapis.com/token'
#     )
#     creds.refresh(Request())
#     return build('gmail', 'v1', credentials=creds)
#
# def send_email(send_to, subject, body):
#     try:
#         service = get_service_from_env()
#
#         message = EmailMessage()
#         body = body.replace('\n', '<br>')
#         message.set_content(body, subtype='html')
#         message['To'] = send_to
#         message['From'] = os.getenv("GMAIL_SENDER_EMAIL")
#         message['Subject'] = subject
#
#         raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
#
#         send_request = {'raw': raw_message}
#         result = service.users().messages().send(userId="me", body=send_request).execute()
#         logger.info(f"Email sent: ID {result['id']}")
#     except Exception as e:
#         logger.error(f"Failed to send mail due to: {str(e)}")
#
import os
import base64
import dotenv
from logger import logger

from mailersend import emails

dotenv.load_dotenv()
api_key = os.getenv('MAILERSEND_API_KEY')

def send_email(send_to, subject, body):

    try:
        logger.info(f"Sending email to {send_to} with subject {subject}")
        mailer = emails.NewEmail(api_key)
        # with open('file.pdf', 'rb') as f:
        #     encoded_file = base64.b64encode(f.read()).decode()

        message = {
            'from': {
                'email': 'MS_UuectP@test-xkjn41m70dq4z781.mlsender.net',
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
            # 'attachments': [
            #     {
            #         'content': encoded_file,
            #         'filename': 'file.pdf',
            #         'type': 'application/pdf',
            #         'disposition': 'attachment'
            #     }
            # ]
        }

        response = mailer.send(message)
        logger.info(f"Email sent: {response}")
    except Exception as e:
        logger.error(f"Failed to send mail due to: {str(e)}. Response: {response}")


if __name__ == '__main__':
    send_to = os.getenv("GMAIL_SEND_TO")
    subject = "Test Email from Python Script"
    body = "<h1>This is a test email</h1><p>Sent from a Python script using Mailsender API.</p>"
    send_email(send_to, subject, body)
