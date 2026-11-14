# Email Service Setup Guide

Configure email service for OAuth email verification and notifications.

> **Status (2026-10-30):** verified against `auth/email_service.py`. `EMAIL_PROVIDER` accepts `smtp`, `sendgrid` or `aws_ses` (Mailgun is not implemented). Only the variables shown below are read; the sender display name comes from the brand descriptor (`email_sender_name`, see [BRANDING_ARCHITECTURE.md](../Architecture/BRANDING_ARCHITECTURE.md)), SMTP always uses STARTTLS, and verification links use `APP_BASE_URL` (default `http://localhost:5050`) unless a branded host supplies its own base URL.

---

## 📋 Overview

The OAuth implementation includes email verification for security. You need to configure one of these email services:

| Service | Setup Time | Cost | Best For |
|---------|-----------|------|----------|
| **SMTP** | 5-10 min | Free (your email) | Self-hosted, small scale |
| **SendGrid** | 10-15 min | Free tier: 100/day | Production, scalable |
| **AWS SES** | 15-20 min | Pay-as-you-go | AWS users, high volume |

**Recommendation for MVP**: Start with **SendGrid** (easiest, free tier available)

---

## 🚀 Quick Start

### Minimum Requirements
Your email service needs to:
- ✅ Send transactional emails (verification, password reset)
- ✅ Support HTML email templates
- ✅ Provide SMTP credentials or API key
- ✅ Configure sender email address

### Configuration Process
1. Choose email service (pick one)
2. Get credentials (SMTP or API key)
3. Add to `.env` file
4. Test sending email
5. Deploy with production credentials

---

## 📧 Option 1: SendGrid (Recommended)

**Best For**: Production apps, scalable, easy setup

**Time**: 10-15 minutes

**Cost**: Free tier: 100 emails/day

### Step 1: Create SendGrid Account

1. Go to https://sendgrid.com
2. Click "Sign Up" 
3. Create account (free tier)
4. Verify email and phone

### Step 2: Create API Key

1. Login to SendGrid dashboard
2. Go to "Settings" → "API Keys"
3. Click "Create API Key"
4. Name: `Uderia OAuth Verification`
5. Permissions: "Restricted Access"
6. Select "Mail Send": Read/Write
7. Create and copy the key (looks like: `SG.xxxxxxxxxxxx`)
8. **Important**: Save this key immediately (shown only once!)

### Step 3: Add to .env

```env
# Email Service Configuration
EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=SG.your_key_here
SENDGRID_FROM_EMAIL=verification@yourdomain.com
```

### Step 4: Create Sender Email

1. In SendGrid dashboard, go to "Settings" → "Sender Authentication"
2. Click "Verify a Single Sender"
3. Enter your sender email (e.g., `verification@yourdomain.com`)
4. Verify the email (click link in email)
5. Use this verified email in `.env` as `SENDGRID_FROM_EMAIL`

### Step 5: Test Configuration

```python
# Test with Python
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

sg = SendGridAPIClient('SG.your_key_here')
email = Mail(
    from_email='verification@yourdomain.com',
    to_emails='test@example.com',
    subject='Test Email',
    html_content='<strong>Test successful!</strong>'
)
response = sg.send(email)
print(response.status_code)  # Should be 202
```

✅ **Success**: If status code is 202

---

## 📧 Option 2: AWS SES (Enterprise)

**Best For**: AWS users, high volume

**Time**: 15-20 minutes

**Cost**: Pay-as-you-go ($0.10 per 1,000 emails)

### Step 1: Set Up AWS SES

1. Login to AWS Console: https://console.aws.amazon.com
2. Go to "Simple Email Service" (SES)
3. Select your region (e.g., us-east-1)
4. Go to "Verified identities"
5. Click "Create identity"
6. Select "Email address"
7. Enter your sender email: `verification@yourdomain.com`
8. Click "Create identity"
9. Verify by clicking link in verification email

### Step 2: Get AWS Credentials

1. Go to AWS IAM console
2. Click "Create user"
3. Name: `uderia-ses-user`
4. Click "Attach policies directly"
5. Search for "AmazonSESFullAccess"
6. Select and attach
7. Go to "Security credentials"
8. Click "Create access key"
9. Choose "Other"
10. Copy:
    - Access Key ID
    - Secret Access Key

### Step 3: Add to .env

```env
# Email Service Configuration
EMAIL_PROVIDER=aws_ses
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_SES_REGION=us-east-1
AWS_SES_FROM_EMAIL=verification@yourdomain.com
```

### Step 4: Request Production Access

By default, SES is in sandbox mode (limited to verified emails only).

To send to any email:
1. In SES console, click "Request production access"
2. Fill out form explaining use case
3. Wait for approval (usually 24 hours)
4. Once approved, you can send to any email

### Step 5: Test Configuration

```python
# Test with Python
import boto3

client = boto3.client('ses', region_name='us-east-1')
response = client.send_email(
    Source='verification@yourdomain.com',
    Destination={'ToAddresses': ['test@example.com']},
    Message={
        'Subject': {'Data': 'Test Email'},
        'Body': {'Html': {'Data': '<strong>Test successful!</strong>'}}
    }
)
print(response['MessageId'])  # Should print message ID
```

✅ **Success**: If message ID is printed

---

## 📧 Option 3: SMTP (Self-Hosted)

**Best For**: Small scale, using your email provider

**Time**: 5-10 minutes

**Cost**: Free (using your email)

### Providers with SMTP

| Provider | SMTP Host | Port | Auth |
|----------|-----------|------|------|
| Gmail | smtp.gmail.com | 587 | App Password |
| Outlook | smtp-mail.outlook.com | 587 | Email + Password |
| Zoho | smtp.zoho.com | 587 | Email + Password |
| ProtonMail | smtp.protonmail.com | 587 | SMTP Token |
| Your Server | your-server.com | 25/587/465 | Username/Password |

### Setup Example: Gmail

#### Step 1: Enable 2FA

1. Login to https://myaccount.google.com
2. Go to "Security" in left sidebar
3. Enable 2-Step Verification

#### Step 2: Create App Password

1. Go back to "Security"
2. Scroll down to "App passwords"
3. Select "Mail" and "Windows Computer"
4. Google generates password (16 characters)
5. Copy this password

#### Step 3: Add to .env

```env
# Email Service Configuration
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your_app_password_here
SMTP_FROM_EMAIL=your-email@gmail.com
```

#### Step 4: Test Configuration

```python
# Test with Python
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

smtp = smtplib.SMTP('smtp.gmail.com', 587)
smtp.starttls()
smtp.login('your-email@gmail.com', 'your_app_password')

msg = MIMEMultipart()
msg['From'] = 'your-email@gmail.com'
msg['To'] = 'test@example.com'
msg['Subject'] = 'Test Email'
msg.attach(MIMEText('<strong>Test successful!</strong>', 'html'))

smtp.send_message(msg)
smtp.quit()
print("Email sent!")
```

✅ **Success**: If no errors and email is received

---

## 🔧 Complete .env Examples

### Development (SendGrid)
```env
# Email Service
EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=SG.xxx...
SENDGRID_FROM_EMAIL=dev@yourdomain.com
```

### Development (SMTP Gmail)
```env
# Email Service
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=app_password_here
SMTP_FROM_EMAIL=your-email@gmail.com
```

### Production (SendGrid)
```env
# Email Service
EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=SG.prod_key_here
SENDGRID_FROM_EMAIL=noreply@yourdomain.com
```

### Production (AWS SES)
```env
# Email Service
EMAIL_PROVIDER=aws_ses
AWS_ACCESS_KEY_ID=AKIA_PROD_KEY
AWS_SECRET_ACCESS_KEY=prod_secret_key
AWS_SES_REGION=us-east-1
AWS_SES_FROM_EMAIL=noreply@yourdomain.com
```

---

## 🔌 Integration with OAuth

### Where Email Verification Happens

- **Password registration** (`POST /api/v1/auth/register`): the user is created with `email_verified=False`, a token is generated by `EmailVerificationService.generate_verification_token()` and `EmailService.send_verification_email()` mails a link to `<base_url>/verify-email?token=...&email=...`. The user confirms via `POST /api/v1/auth/verify-email`; `POST /api/v1/auth/resend-verification-email` sends a fresh link.
- **OAuth sign-up** (`OAuthHandler` in `auth/oauth_handlers.py`): no verification email is sent. The provider's own `email_verified` claim is trusted (Google/Microsoft set it); an address matching `ADMIN_EMAIL` is treated as verified and created as an admin. A later OAuth login that reports `email_verified` upgrades an unverified account.
- **SSO (OIDC)**: per-configuration `require_email_verification` flag on the `sso_configurations` row (Administration UI), not an env variable.

`<base_url>` is resolved by `core/branding.email_base_url()`: an explicit `verification_base_url` in the request body, else the branded host's base URL, else `APP_BASE_URL` (default `http://localhost:5050`).

---

## 📧 Email Template

The verification email uses this template:

```html
<!DOCTYPE html>
<html>
<head>
    <style>
        body { font-family: Arial, sans-serif; }
        .container { max-width: 600px; margin: 0 auto; }
        .header { background-color: #007bff; color: white; padding: 20px; }
        .content { padding: 20px; }
        .button { 
            background-color: #007bff; 
            color: white; 
            padding: 10px 20px; 
            text-decoration: none; 
            border-radius: 5px; 
        }
        .footer { color: #666; font-size: 12px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>Email Verification Required</h2>
        </div>
        <div class="content">
            <p>Hello,</p>
            <p>Thank you for signing up! Please verify your email address to complete your account setup.</p>
            <p>
                <a href="https://yourdomain.com/verify-email?token=VERIFICATION_TOKEN&amp;email=USER_EMAIL" class="button">
                    Verify Email Address
                </a>
            </p>
            <p>Or copy this link:</p>
            <p>https://yourdomain.com/verify-email?token=VERIFICATION_TOKEN&amp;email=USER_EMAIL</p>
            <p>This link expires in 24 hours.</p>
            <p>If you did not sign up, you can safely ignore this email.</p>
        </div>
        <div class="footer">
            <p>© 2026 Uderia. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
```

---

## ✅ Configuration Checklist

### Choose Email Service
- [ ] Decided on SMTP, SendGrid, or AWS SES
- [ ] Credentials ready (API key or SMTP credentials)

### Configure .env
- [ ] Added `EMAIL_PROVIDER=sendgrid|smtp|aws_ses`
- [ ] Added email service credentials
- [ ] Added `SENDGRID_FROM_EMAIL` or `SMTP_FROM_EMAIL`
- [ ] Credentials are correct (no typos)

### Test Email Service
```bash
# Run test (create a test script)
python test_email.py
```

Should:
- [ ] No errors in console
- [ ] Email received in inbox (may be spam folder)
- [ ] Email format looks correct

### Enable in OAuth
- [ ] Set `EMAIL_PROVIDER` and its variables in .env (verification emails are sent on every registration; there is no switch)
- [ ] Restarted app
- [ ] Tested OAuth login flow

### Verify in User Journey
- [ ] Sign up with OAuth provider
- [ ] See "Please verify your email" message
- [ ] Receive verification email
- [ ] Click verification link
- [ ] Email marked as verified
- [ ] Can now access app

---

## 🧪 Testing Email Service

### Test 1: SendGrid

```python
# test_sendgrid.py
import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

api_key = os.getenv('SENDGRID_API_KEY')
from_email = os.getenv('SENDGRID_FROM_EMAIL')

sg = SendGridAPIClient(api_key)
email = Mail(
    from_email=from_email,
    to_emails='your-test@example.com',
    subject='Test Email from Uderia',
    html_content='<h1>Test Successful!</h1><p>Email service is working.</p>'
)

try:
    response = sg.send(email)
    if response.status_code == 202:
        print("✅ Email sent successfully!")
    else:
        print(f"❌ Error: {response.status_code}")
except Exception as e:
    print(f"❌ Error: {e}")
```

### Test 2: SMTP

```python
# test_smtp.py
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

smtp_host = os.getenv('SMTP_HOST')
smtp_port = int(os.getenv('SMTP_PORT', 587))
smtp_user = os.getenv('SMTP_USERNAME')
smtp_pass = os.getenv('SMTP_PASSWORD')
from_email = os.getenv('SMTP_FROM_EMAIL')

try:
    smtp = smtplib.SMTP(smtp_host, smtp_port)
    smtp.starttls()
    smtp.login(smtp_user, smtp_pass)
    
    msg = MIMEMultipart()
    msg['From'] = from_email
    msg['To'] = 'your-test@example.com'
    msg['Subject'] = 'Test Email from Uderia'
    msg.attach(MIMEText('<h1>Test Successful!</h1><p>Email service is working.</p>', 'html'))
    
    smtp.send_message(msg)
    smtp.quit()
    print("✅ Email sent successfully!")
except Exception as e:
    print(f"❌ Error: {e}")
```

### Test 3: AWS SES

```python
# test_aws_ses.py
import os
import boto3

region = os.getenv('AWS_SES_REGION', 'us-east-1')
from_email = os.getenv('AWS_SES_FROM_EMAIL')

ses = boto3.client('ses', region_name=region)

try:
    response = ses.send_email(
        Source=from_email,
        Destination={'ToAddresses': ['your-test@example.com']},
        Message={
            'Subject': {'Data': 'Test Email from Uderia'},
            'Body': {'Html': {'Data': '<h1>Test Successful!</h1><p>Email service is working.</p>'}}
        }
    )
    print(f"✅ Email sent successfully! Message ID: {response['MessageId']}")
except Exception as e:
    print(f"❌ Error: {e}")
```

---

## 🚨 Troubleshooting

### "Authentication failed"
- ✅ Check credentials in .env
- ✅ For Gmail: Use app password, not regular password
- ✅ For SendGrid: API key should start with `SG.`
- ✅ For AWS: Access key ID should start with `AKIA`

### "Email not received"
- ✅ Check spam folder
- ✅ Verify sender email is registered/verified with service
- ✅ For SMTP Gmail: Enable "Less secure app access"
- ✅ Check rate limits haven't been exceeded

### "Invalid API key"
- ✅ Generate new key in provider dashboard
- ✅ Copy entire key (including `SG.` prefix for SendGrid)
- ✅ No trailing spaces in .env

### "From email not verified"
- ✅ SendGrid: Verify single sender in "Sender Authentication"
- ✅ AWS SES: Verify email in "Verified identities"
- ✅ SMTP: Use email that matches authentication account

### "Port connection refused"
- ✅ Check SMTP_PORT is correct (usually 587 or 465)
- ✅ Verify firewall allows outgoing connections
- ✅ Try different port: 25, 465, or 2525

### Email goes to spam
- ✅ Sender domain reputation
- ✅ Proper SPF/DKIM/DMARC records
- ✅ Use branded domain (not shared IP)
- ✅ SendGrid: Setup sender domain for better deliverability

---

## 📞 Need Help?

| Issue | Solution |
|-------|----------|
| SendGrid setup | https://sendgrid.com/docs/for-developers/sending-email/smtp-service/ |
| AWS SES setup | https://docs.aws.amazon.com/ses/latest/dg/send-email.html |
| Gmail SMTP | https://support.google.com/accounts/answer/185833 |
| Email templates | [SECURITY.md](./SECURITY.md#1-email-verification-system) |
| Integration | [INTEGRATION_GUIDE.md](./INTEGRATION_GUIDE.md) |

---

## 🎯 Next Steps

1. ✅ Choose email service (SendGrid recommended)
2. ✅ Get credentials
3. ✅ Add to .env
4. ✅ Test with provided test scripts
5. 📋 Enable in OAuth config
6. 📋 Test complete flow
7. 📋 Deploy to production

---

**Back to:** [README.md](./README.md) | [SECURITY.md](./SECURITY.md)
