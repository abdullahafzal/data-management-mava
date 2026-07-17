# AWS S3 setup — bucket, IAM keys, `.env`, EC2

This project stores uploads on the **EC2 disk** (`media/`) and, when enabled,
**also mirrors them to S3**. Local paths keep working (`FileField.path`, merges,
pandas). S3 is your durable backup / shared store.

Region for your instance: **eu-north-1 (Stockholm)** — use the same for the bucket.

---

## 1. Create the S3 bucket

1. Open [AWS Console → S3](https://s3.console.aws.amazon.com/s3/home?region=eu-north-1)
2. Confirm region is **Europe (Stockholm) eu-north-1** (top-right)
3. Click **Create bucket**
4. Settings:

| Setting | Value |
|--------|--------|
| Bucket name | Something unique, e.g. `data-management-mava-media` |
| AWS Region | Europe (Stockholm) `eu-north-1` |
| Object Ownership | ACLs disabled (recommended) |
| Block Public Access | **Keep all blocks ON** (private bucket) |
| Bucket Versioning | Optional — enable if you want file history |
| Default encryption | SSE-S3 (Amazon S3 managed keys) is fine |

5. Create bucket

You do **not** need public website hosting or public read for this app.

---

## 2. Create an IAM user for the app (access keys)

> Prefer an IAM **user + access keys** for a quick start.  
> Later you can switch to an **EC2 instance role** (no keys in `.env`).

### 2a. Policy (permissions)

1. [IAM → Policies → Create policy](https://console.aws.amazon.com/iam/home#/policies)
2. JSON tab — paste (replace `YOUR_BUCKET_NAME`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListBucket",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET_NAME"
    },
    {
      "Sid": "ObjectRW",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::YOUR_BUCKET_NAME/*"
    }
  ]
}
```

3. Name it e.g. `DataManagementMavaS3Media`
4. Create policy

### 2b. User + attach policy

1. [IAM → Users → Create user](https://console.aws.amazon.com/iam/home#/users)
2. User name: e.g. `data-management-mava-s3`
3. **Do not** enable console password (programmatic access only)
4. Attach policy: `DataManagementMavaS3Media`
5. Create user

### 2c. Access keys

1. Open the user → **Security credentials**
2. **Create access key** → choose **Application running outside AWS** (or “Other”)
3. Create access key
4. **Copy both values once** (Secret is shown only once):

- Access key ID → `AWS_ACCESS_KEY_ID`
- Secret access key → `AWS_SECRET_ACCESS_KEY`

Store them in a password manager — **never commit to Git**.

---

## 3. Put values in `.env` on EC2

SSH into the server:

```bash
cd /var/www/data-management-mava
nano .env
```

Add / update (use your real values):

```env
USE_S3=true
AWS_ACCESS_KEY_ID=AKIA................
AWS_SECRET_ACCESS_KEY=................................
AWS_STORAGE_BUCKET_NAME=data-management-mava-media
AWS_S3_REGION_NAME=eu-north-1
AWS_LOCATION=media
```

Save: `Ctrl+O`, Enter, `Ctrl+X`.

```bash
chmod 600 .env
```

---

## 4. Deploy the S3 code on the server

From your PC: commit & push the repo changes (`datamanagement/storage.py`, settings, `boto3`, etc.).

On EC2:

```bash
cd /var/www/data-management-mava
source env/bin/activate
git pull
pip install -r requirements.txt
sudo systemctl restart datamanagement
sudo systemctl status datamanagement --no-pager
```

If you cannot `git pull` yet, install packages and copy files manually, then restart.

---

## 5. Test that S3 works

### A. From Django shell on EC2

```bash
cd /var/www/data-management-mava
source env/bin/activate
python manage.py shell
```

```python
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

print('USE_S3', settings.USE_S3)
print('bucket', settings.AWS_STORAGE_BUCKET_NAME)

name = default_storage.save('s3_test/hello.txt', ContentFile(b'hello from mava'))
print('saved', name)
print('local exists', default_storage.exists(name))
# Check AWS Console → S3 → bucket → media/s3_test/hello.txt
```

Exit shell: `exit()`

### B. From the website

1. Open Lead DB → upload a small CSV (new workspace or merge)
2. In S3 console, open the bucket → folder `media/` → you should see `lead_db/...`

### C. Quick AWS CLI check (optional on EC2)

```bash
source env/bin/activate
python - <<'PY'
import boto3, os
from dotenv import load_dotenv
load_dotenv('/var/www/data-management-mava/.env')
c = boto3.client(
    's3',
    region_name=os.environ['AWS_S3_REGION_NAME'],
    aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],
)
print(c.list_objects_v2(Bucket=os.environ['AWS_STORAGE_BUCKET_NAME'], MaxKeys=5))
PY
```

---

## 6. How it behaves

| Action | Local `media/` | S3 bucket |
|--------|----------------|-----------|
| Upload / save FileField | Written | Uploaded under `media/...` |
| App reads `.path` | Uses local file | If local missing, downloads from S3 first |
| Delete file | Removed locally | Object deleted in S3 |

Static CSS/JS still come from Nginx `staticfiles/` (not S3). Only **media uploads** are mirrored.

---

## 7. Backfill existing local files to S3 (optional)

If you already have files under `/var/www/data-management-mava/media/`:

```bash
cd /var/www/data-management-mava
source env/bin/activate
python manage.py shell
```

```python
from pathlib import Path
from django.conf import settings
from django.core.files.storage import default_storage

root = Path(settings.MEDIA_ROOT)
for path in root.rglob('*'):
    if not path.is_file():
        continue
    rel = path.relative_to(root).as_posix()
    if rel.startswith('.s3_cache'):
        continue
    default_storage._upload_to_s3(rel)
    print('uploaded', rel)
```

---

## 8. Troubleshooting

| Problem | Fix |
|---------|-----|
| `USE_S3` true but nothing in bucket | Restart gunicorn after editing `.env`; confirm `AWS_STORAGE_BUCKET_NAME` |
| `AccessDenied` | IAM policy bucket name mismatch; wrong key; wrong region |
| `InvalidAccessKeyId` | Typo in access key; key deactivated |
| App works but no S3 objects | `USE_S3` still false, or code not pulled / not restarted |
| Region errors | Bucket region must match `AWS_S3_REGION_NAME=eu-north-1` |

Check app logs:

```bash
sudo journalctl -u datamanagement -n 80 --no-pager
```

---

## 9. Security tips

- Keep Block Public Access **on**
- Never commit `.env` or `*.pem`
- Rotate access keys if they leak
- Later: attach an **IAM role to the EC2 instance** and remove keys from `.env` (leave `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` empty; boto3 uses the instance role)

---

## 10. Turn S3 off

In `.env`:

```env
USE_S3=false
```

```bash
sudo systemctl restart datamanagement
```

Uploads stay local only again.
