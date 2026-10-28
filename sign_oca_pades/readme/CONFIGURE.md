The seal needs a PKCS#12 file (`.p12` or `.pfx`) holding a private key and its
certificate. A key and a certificate kept as two PEM files are bundled into one with
`openssl`:

```bash
openssl pkcs12 -export -inkey seal.key -in seal.crt -out seal.p12 -passout pass:secret
```

For a test database, a self-signed certificate is enough:

```bash
openssl req -x509 -newkey rsa:3072 -nodes -days 1095 \
  -keyout seal.key -out seal.crt -subj "/CN=Document seal (test)" \
  -addext "keyUsage=critical,digitalSignature,nonRepudiation"
```

The certificate needs the _non repudiation_ key usage: pyHanko requires it of a signer,
so a certificate without it still seals, but the verification reports every seal as
untrusted. The seals of a self-signed certificate are intact and valid, but PDF readers
report the signer as untrusted, since no recognised authority issued the certificate. In
production, use a certificate issued by an authority the readers trust.

The certificate is read from the first source that provides one:

1. The server configuration file (`odoo.conf`, section `[options]`):

   ```ini
   sign_oca_pades_pkcs12_path = /path/to/seal.p12
   sign_oca_pades_pkcs12_password = secret
   sign_oca_pades_tsa_url = https://tsa.example.com
   ```

   The file stays on the server and never enters the database. The server must be
   restarted after a change.

2. The company: go to _Sign > Settings > General settings_, section _Document Sealing
   (PAdES)_ (visible to administrators only), and upload the PKCS#12 file with its
   password. An optional _Timestamp Authority_ URL can be set there too.

   The same fields can be set from `odoo shell`, which checks the password before
   storing anything:

   ```python
   import base64
   from cryptography.hazmat.primitives.serialization import pkcs12

   data = open("/path/to/seal.p12", "rb").read()
   password = "secret"
   pkcs12.load_key_and_certificates(data, password.encode())  # wrong password: raises
   env.company.write(
       {
           "sign_oca_pades_pkcs12": base64.b64encode(data),
           "sign_oca_pades_pkcs12_filename": "seal.p12",
           "sign_oca_pades_pkcs12_password": password,
       }
   )
   env.cr.commit()
   ```

   `env.company` is the company of the shell user; in a multi-company database, each
   company that has documents signed needs its own certificate.

   A certificate stored on the company travels with the database: every backup or copy
   of it holds the private key, and the password in clear text. The fields are readable
   by administrators only, but anyone with access to PostgreSQL or to the backups can
   seal documents in the name of the company. The configuration file avoids this.

A timestamp authority URL from the configuration file takes precedence over the one of
the company.

Without a certificate, the documents are still signed, but not sealed, and each
signature logs the warning `no sealing certificate configured`. Only the signatures made
after a certificate is configured are sealed: a document signed before stays unsealed.

The module depends on the Python library `pyhanko`. Install `pyhanko<0.26`: pyHanko 0.26
and later require `lxml>=5.4`, while Odoo 18 environments commonly pin an older `lxml`.
Version 0.25.3 is the one this module is tested with.

The local times of the certificate page use the time zone of the request's tier
validation when `sign_oca_tier_validation` sets one, else the time zone of the company's
contact, else UTC.
