"""Create the harness's first user, once, from the server's shell (ADR 519).

`python3 -m board_service.first_user [--name NAME]` reads `BOARD_AUTH_DIR`,
asks for the password twice without echo, creates an owner and prints the TOTP
secret and the otpauth URI once. This is the one place a secret is printed
(an exception to ADR 324, 10): the person at the shell is the only reader.
Exit 0 created, 1 a user already exists, 2 bad input or no `BOARD_AUTH_DIR`.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from urllib.parse import quote

from board_service import signin


def main(argv=None, *, environ=os.environ, read_password=getpass.getpass,
         out=sys.stdout, err=sys.stderr) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m board_service.first_user")
    parser.add_argument("--name", default="owner")
    args = parser.parse_args(argv)
    name = args.name

    auth_dir = environ.get(signin.AUTH_DIR_ENV, "")
    if not auth_dir:
        print(f"{signin.AUTH_DIR_ENV} is not set", file=err)
        return 2
    if signin._read(signin._users_path(auth_dir)):
        print("a user already exists; nothing was changed", file=err)
        return 1
    password = read_password("Password: ")
    if read_password("Password again: ") != password:
        print("the passwords differ", file=err)
        return 2
    try:
        secret = signin.create_user(auth_dir, name, password, "owner", first=True)
    except signin.UserExists:
        print("a user already exists; nothing was changed", file=err)
        return 1
    except ValueError as exc:
        print(str(exc), file=err)
        return 2
    signin.audit(auth_dir, "user_created", name, role="owner")
    uri = (
        f"otpauth://totp/ia-harness:{quote(name)}?secret={secret}"
        "&issuer=ia-harness&algorithm=SHA1&digits=6&period=30"
    )
    print(f"TOTP secret: {secret}", file=out)
    print(f"otpauth URI: {uri}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
