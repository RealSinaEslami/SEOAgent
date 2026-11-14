import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class Vault:
    def __init__(self, directory: Path):
        key = os.getenv("SEO_ENCRYPTION_KEY")
        if not key:
            path = directory / "vault.key"
            try:
                # Exclusive creation prevents two starts from generating different keys.
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as f:
                    f.write(Fernet.generate_key())
            key = path.read_bytes()
        self.fernet = Fernet(key)

    def encrypt(self, value):
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, value):
        try:
            return self.fernet.decrypt(value.encode()).decode()
        except InvalidToken as e:
            raise ValueError("کلید رمزنگاری با اطلاعات ذخیره‌شده مطابقت ندارد؛ اتصال را دوباره تنظیم کنید.") from e
