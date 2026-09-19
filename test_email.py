import os, json, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

with open('data/output/interview_emails.json') as f:
    emails = json.load(f)

SENDER = "asiffiza333@gmail.com"
APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]

with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
    smtp.login(SENDER, APP_PASSWORD)
    
    e = emails[0]
    msg = MIMEMultipart()
    msg['From'] = SENDER
    msg['To'] = e['to']
    msg['Subject'] = e['subject']
    msg.attach(MIMEText(e['body'], 'plain'))
    smtp.sendmail(SENDER, e['to'], msg.as_string())
    print(f"Test sent to: {e['to']}")
