# Hosting guide — Data Management (MABA) on AWS EC2

Step-by-step deploy for this Django project on the EC2 instance you launched
(Ubuntu 24.04, `t3.micro`, Stockholm `eu-north-1`).

**Out of scope for now:** S3. Keep uploads on the server disk (`media/`). Add S3 later when you need it.

**Never commit secrets.** Put your `.pem` key, `.env`, and any personal notes in places covered by `.gitignore` (see end of this file).

---

## 0. What you need before starting

| Item | Where |
|------|--------|
| EC2 instance running | AWS Console → EC2 → Instances |
| Public IPv4 address | Instance summary (e.g. `13.51.x.x`) |
| Key pair file | `Data-management.pem` (download once from AWS; store privately) |
| Security group | SSH (22), HTTP (80), HTTPS (443) open |
| This GitHub repo URL | Your remote clone URL |
| API keys (optional at first) | MillionVerifier, etc. — can add later in `.env` |

Save the `.pem` somewhere safe **outside** the repo, e.g.:

```text
C:\Users\YOU\.ssh\Data-management.pem
```

---

## 1. Connect with SSH (from your Windows PC)

### PowerShell — fix key permissions (once)

```powershell
icacls $env:USERPROFILE\.ssh\Data-management.pem /inheritance:r
icacls $env:USERPROFILE\.ssh\Data-management.pem /grant:r "$($env:USERNAME):(R)"
```

### SSH in

Replace `YOUR_EC2_PUBLIC_IP` with the instance public IP:

```powershell
ssh -i $env:USERPROFILE\.ssh\Data-management.pem ubuntu@YOUR_EC2_PUBLIC_IP
```

First time: type `yes` when asked about the host fingerprint.

You should see an Ubuntu prompt: `ubuntu@ip-...:~$`

---

## 2. Update the server & install packages

Run on the EC2 instance:

```bash
sudo apt update && sudo apt upgrade -y

sudo apt install -y \
  python3-pip python3-venv python3-dev \
  nginx git curl \
  build-essential libpq-dev \
  sqlite3
```

(Optional later for Chrome/Selenium automation on the server — skip for first launch.)

---

## 3. Clone the project

```bash
sudo mkdir -p /var/www
sudo chown ubuntu:ubuntu /var/www
cd /var/www

git clone YOUR_GITHUB_REPO_URL data-management-mava
cd data-management-mava
```

If the repo is private, use a GitHub personal access token or SSH deploy key.

---

## 4. Python virtualenv & dependencies

Your AMI may be **Ubuntu 26.04** with **Python 3.14 only** (no `python3.12` in apt). That is fine.

Use the system `python3`, but install **pandas 2.3.3+** (has prebuilt 3.14 wheels).  
Older `pandas==2.2.3` tries to compile from source and gets **OOM-killed** on `t3.micro` (exit 137).

```bash
cd /var/www/data-management-mava

# Clean any broken venv
deactivate 2>/dev/null || true
rm -rf env

python3 -m venv env
source env/bin/activate
python --version   # 3.14.x is OK

pip install --upgrade pip
pip install -r requirements.txt
```

If `requirements.txt` on the server still says `pandas==2.2.3`, fix it first:

```bash
sed -i 's/pandas==2.2.3/pandas==2.3.3/' requirements.txt
```

Or edit by hand: `nano requirements.txt` → change that line → save.

### Optional: 2G swap (safety net on t3.micro)

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 5. Production `.env` (secrets — never commit)

```bash
cd /var/www/data-management-mava
nano .env
```

Paste (edit values):

```env
# Django
SECRET_KEY=change-me-to-a-long-random-string
DEBUG=false
ALLOWED_HOSTS=YOUR_EC2_PUBLIC_IP,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://YOUR_EC2_PUBLIC_IP

# APIs — fill when ready (leave blank to skip)
OUTSCRAPER_API_KEY=
MILLIONVERIFIER_API_KEY=
SMARTLEAD_API_KEY=
PHONE_VALIDATION_API_KEY=
SIMPLETEXTING_API_KEY=
GHL_API_KEY=
GHL_LOCATION_ID=
XVERIFY_DOMAIN=
OPENAI_API_KEY=
```

Generate a strong `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Save: `Ctrl+O`, Enter, `Ctrl+X`.

Lock file permissions:

```bash
chmod 600 .env
```

---

## 6. Database, static files, admin user

```bash
cd /var/www/data-management-mava
source env/bin/activate

python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

Follow prompts for admin username/email/password.

Create media folder:

```bash
mkdir -p media
```

---

## 7. Test Gunicorn (quick check)

```bash
cd /var/www/data-management-mava
source env/bin/activate
gunicorn --bind 127.0.0.1:8000 datamanagement.wsgi:application
```

In another SSH session (or stop with `Ctrl+C` after this works), you can leave it running briefly. Next we wire systemd + Nginx.

Stop the test with `Ctrl+C` when done.

---

## 8. Systemd service (keeps the app running)

```bash
sudo nano /etc/systemd/system/datamanagement.service
```

Paste:

```ini
[Unit]
Description=Data Management Gunicorn
After=network.target

[Service]
User=ubuntu
Group=www-data
WorkingDirectory=/var/www/data-management-mava
EnvironmentFile=/var/www/data-management-mava/.env
ExecStart=/var/www/data-management-mava/env/bin/gunicorn \
  --workers 2 \
  --bind unix:/var/www/data-management-mava/gunicorn.sock \
  datamanagement.wsgi:application
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable datamanagement
sudo systemctl start datamanagement
sudo systemctl status datamanagement
```

You want `active (running)`. If it failed: `journalctl -u datamanagement -n 50 --no-pager`

Allow the web user to reach the socket folder:

```bash
sudo usermod -aG ubuntu www-data
sudo chmod 755 /var/www/data-management-mava
```

---

## 9. Nginx reverse proxy

```bash
sudo nano /etc/nginx/sites-available/datamanagement
```

Paste (replace `YOUR_EC2_PUBLIC_IP` if you want a `server_name`; `_` also works):

```nginx
server {
    listen 80;
    server_name YOUR_EC2_PUBLIC_IP;

    client_max_body_size 50M;

    location /static/ {
        alias /var/www/data-management-mava/staticfiles/;
    }

    location /media/ {
        alias /var/www/data-management-mava/media/;
    }

    location / {
        include proxy_params;
        proxy_pass http://unix:/var/www/data-management-mava/gunicorn.sock;
    }
}
```

Enable site and reload Nginx:

```bash
sudo ln -sf /etc/nginx/sites-available/datamanagement /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

---

## 10. Open the site

In your browser:

```text
http://YOUR_EC2_PUBLIC_IP/
```

Lead DB dashboard:

```text
http://YOUR_EC2_PUBLIC_IP/dashboard/
```

Admin:

```text
http://YOUR_EC2_PUBLIC_IP/admin/
```

If the page does not load:

1. Security group: inbound **80** and **443** from `0.0.0.0/0`
2. `sudo systemctl status datamanagement`
3. `sudo systemctl status nginx`
4. `sudo tail -n 50 /var/log/nginx/error.log`

---

## 11. Deploy updates later (after code changes)

On your PC: commit & push to GitHub.

On EC2:

```bash
cd /var/www/data-management-mava
source env/bin/activate
git pull
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart datamanagement
```

---

## 12. HTTPS (optional, when you have a domain)

When you point a domain (e.g. `app.yourdomain.com`) to the EC2 Elastic IP:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d app.yourdomain.com
```

Then update `.env`:

```env
ALLOWED_HOSTS=app.yourdomain.com,YOUR_EC2_PUBLIC_IP
CSRF_TRUSTED_ORIGINS=https://app.yourdomain.com
```

```bash
sudo systemctl restart datamanagement
```

---

## 13. S3 media storage

Full walkthrough (bucket → IAM keys → `.env` → test): **[docs/S3_SETUP.md](S3_SETUP.md)**

Summary on the server after code is deployed:

```env
USE_S3=true
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_STORAGE_BUCKET_NAME=your-bucket-name
AWS_S3_REGION_NAME=eu-north-1
AWS_LOCATION=media
```

```bash
pip install -r requirements.txt
sudo systemctl restart datamanagement
```

Uploads stay on local `media/` **and** are mirrored to S3 (so merges / `.path` keep working).

---

## 14. Useful commands cheat sheet

```bash
# App logs
sudo journalctl -u datamanagement -f

# Restart app
sudo systemctl restart datamanagement

# Restart Nginx
sudo systemctl restart nginx

# Disk space
df -h
```

---

## Security checklist (do soon)

- [ ] `DEBUG=false` in `.env`
- [ ] Strong unique `SECRET_KEY`
- [ ] Prefer locking SSH to your IP in the security group (not `0.0.0.0/0`)
- [ ] Prefer an **Elastic IP** so the public IP does not change on stop/start
- [ ] Keep `.pem` and `.env` out of Git (see `.gitignore`)
- [ ] Regular `git pull` + backups of `db.sqlite3` and `media/`

---

## Files that must stay private (`.gitignore`)

Already ignored / should stay ignored:

- `.env`, `.env.*` (except `.env.example`)
- `*.pem`, `*.ppk` (AWS key pairs)
- `db.sqlite3` / `*.sqlite3`
- `media/`
- `HOSTING.local.md` (optional personal notes: IPs, passwords — create locally if you want)

Do **not** paste real keys, passwords, or private IPs into committed docs.
