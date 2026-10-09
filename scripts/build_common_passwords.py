"""Build data/common-passwords.sha256, the list that "flask create-admin" checks (ASVS 6.2.4).

    python scripts/build_common_passwords.py XATO_FILE NCSC_FILE > data/common-passwords.sha256

OWASP ASVS 5.0, requirement 6.2.4, asks that a new password is checked against at
least the 3,000 most common passwords that satisfy the application's own
policy. PhishAware requires twelve characters, and few of the most common
passwords are that long, so the list is drawn from far down two published
rankings:

* XATO_FILE: "xato-net-10-million-passwords-1000000.txt", the million most
  frequent passwords of Mark Burnett's 2015 release of ten million passwords,
  most frequent first. The first 3,000 entries of at least twelve characters
  are taken; the 3,000th stands at rank 312,104.
* NCSC_FILE: "100k-most-used-passwords-NCSC.txt", the 100,000 most frequent
  passwords in breach data, published by the UK National Cyber Security Centre
  with Have I Been Pwned. All of its entries of at least twelve characters are
  taken (1,212).

Both files are distributed in the SecLists collection
(https://github.com/danielmiessler/SecLists, MIT License), folder
Passwords/Common-Credentials; the list in the repository was built from commit
27c0806 of 9 October 2026. They are not copied into this repository.

The output holds no password. Each line is the first 16 hexadecimal digits of
the SHA-256 digest of one password in lower case, so the comparison ignores
capitalisation. Storing digests keeps a dictionary of passwords, and the
offensive words such rankings contain, out of the repository and the image. With
64 bits for each of about 4,000 entries, the chance that an unrelated password
is refused by coincidence is below one in a thousand million million.
"""

import hashlib
import sys

MINIMUM_LENGTH = 12      # admin.MIN_PASSWORD_LENGTH
FROM_XATO = 3000


def digest(password):
    """The form in which a password is listed: 16 hex digits of SHA-256, case ignored."""
    return hashlib.sha256(password.lower().encode("utf-8")).hexdigest()[:16]


def long_enough(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\r\n") for line in handle
                if len(line.rstrip("\r\n")) >= MINIMUM_LENGTH]


def main(xato_file, ncsc_file):
    xato = long_enough(xato_file)[:FROM_XATO]
    ncsc = long_enough(ncsc_file)
    digests = sorted({digest(password) for password in xato + ncsc})
    print("# Digests of commonly used passwords of at least twelve characters (ASVS 6.2.4).")
    print("# Built by scripts/build_common_passwords.py, which names the sources;")
    print(f"# {len(xato):,} entries from the first source, {len(ncsc):,} from the second,")
    print(f"# {len(digests):,} different passwords when capitalisation is ignored.")
    print("# One entry per line: the first 16 hex digits of SHA-256(password in lower case).")
    for line in digests:
        print(line)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
