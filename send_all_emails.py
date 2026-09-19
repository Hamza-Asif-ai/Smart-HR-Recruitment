import os, json, smtplib, time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

with open('data/output/interview_emails.json') as f:
    emails = json.load(f)

SENDER = "saifh6765@gmail.com"
APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]

with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
    smtp.login(SENDER, APP_PASSWORD)
    for e in emails:
        msg = MIMEMultipart()
        msg['From'] = SENDER
        msg['To'] = e['to']
        msg['Subject'] = e['subject']
        msg.attach(MIMEText(e['body'], 'plain'))
        smtp.sendmail(SENDER, e['to'], msg.as_string())
        print(f"Sent to: {e['to']}")
        time.sleep(1)

print("All emails sent!")
